"""One full sync run: fetch → prefilter → classify → upsert → sheet → digest."""

from __future__ import annotations

import logging
import re
import time

from . import matcher, prefilter, rules
from .config import Config
from .digest import build_digest, send_digest
from .imap_client import GmailReader
from .llm import LlmClassifier
from .models import Status
from .sheet import SheetWriter
from .store import Store

log = logging.getLogger(__name__)

# Matches our own digest subjects, including "Re:"/"Fwd:" chains and any dash variant.
_DIGEST_SUBJECT_RE = re.compile(r"^\s*(?:(?:re|fwd?)\s*:\s*)*apptrack\b", re.IGNORECASE)

# Google Sheets returns sporadic 503s (three nightly writes lost so far); retry those.
_SHEET_ATTEMPTS = 3
_SHEET_RETRY_S = 60


def _is_transient(exc: Exception) -> bool:
    """5xx / network errors are worth retrying; 4xx (e.g. 403 permission) are not."""
    code = getattr(getattr(exc, "response", None), "status_code", None)
    if code is not None:
        return code >= 500 or code == 429
    return isinstance(exc, (ConnectionError, TimeoutError, OSError))


def run_sync(cfg: Config, send_email: bool = True, write_sheet: bool = True) -> dict:
    store = Store(cfg.db_path)
    run_id = store.start_run()
    stats = {
        "fetched": 0, "candidates": 0, "classified": 0, "llm_calls": 0,
        "new_apps": 0, "updated_apps": 0, "needs_review": 0, "error": None,
        "new_app_objs": [], "status_change_objs": [],
    }
    llm = LlmClassifier(cfg)
    try:
        reader = GmailReader(cfg, store)
        for uidvalidity, email in reader.fetch_new():
            stats["fetched"] += 1

            # Never classify our own digest emails (or replies/forwards of them).
            if email.sender_email == cfg.gmail_address.lower() and _DIGEST_SUBJECT_RE.match(email.subject):
                store.mark_processed(email.uid, uidvalidity, email.message_id, None, "ignored")
                continue

            # A reply in a thread already linked to an application is always a
            # candidate, however casual ("Following up", "Quick question").
            linked = bool(email.thread_id) and store.find_by_thread(email.thread_id) is not None
            if not linked and not prefilter.is_candidate(email):
                store.mark_processed(email.uid, uidvalidity, email.message_id, None, "ignored")
                continue
            stats["candidates"] += 1

            verdict = rules.classify(email)
            if verdict is None:
                stats["llm_calls"] += 1
                verdict = llm.classify(email)

            if not verdict.is_job_related or verdict.status is None:
                store.mark_processed(email.uid, uidvalidity, email.message_id, None, "ignored")
                continue

            result = matcher.upsert(store, email, verdict)
            stats["classified"] += 1
            if verdict.status == Status.NEEDS_REVIEW:
                stats["needs_review"] += 1
            if result.created:
                stats["new_apps"] += 1
                app = next(a for a in store.all_applications() if a.id == result.app_id)
                stats["new_app_objs"].append(app)
            elif result.status_changed:
                stats["updated_apps"] += 1
                app = next(a for a in store.all_applications() if a.id == result.app_id)
                stats["status_change_objs"].append((app, result.new_status.value))
            store.mark_processed(
                email.uid, uidvalidity, email.message_id, result.app_id,
                "needs_review" if verdict.status == Status.NEEDS_REVIEW else "classified",
            )
            log.info(
                "[%s] %s | %s → %s (%s)",
                verdict.source, verdict.company, verdict.role or "?", verdict.status.value, email.subject[:60],
            )
    except Exception as exc:
        log.exception("Sync failed")
        stats["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        llm.close()

    store.finish_run(
        run_id,
        fetched=stats["fetched"], candidates=stats["candidates"], classified=stats["classified"],
        llm_calls=stats["llm_calls"], new_apps=stats["new_apps"], updated_apps=stats["updated_apps"],
        needs_review=stats["needs_review"], error=stats["error"],
    )

    if write_sheet:
        for attempt in range(1, _SHEET_ATTEMPTS + 1):
            try:
                writer = SheetWriter(cfg)
                writer.pull_notes(store)
                writer.write(store)
                break
            except Exception as exc:
                if attempt < _SHEET_ATTEMPTS and _is_transient(exc):
                    log.warning("Sheet write failed (attempt %d), retrying in %ds: %s", attempt, _SHEET_RETRY_S, exc)
                    time.sleep(_SHEET_RETRY_S)
                    continue
                log.exception("Sheet write failed")
                stats["error"] = (stats["error"] or "") + f" | sheet: {exc}"
                break

    if send_email:
        try:
            subject, body, html_body = build_digest(cfg, store, stats)
            send_digest(cfg, subject, body, html_body)
        except Exception as exc:
            log.exception("Digest send failed")

    store.close()
    log.info(
        "Run complete: fetched=%d candidates=%d classified=%d llm=%d new=%d updated=%d review=%d",
        stats["fetched"], stats["candidates"], stats["classified"], stats["llm_calls"],
        stats["new_apps"], stats["updated_apps"], stats["needs_review"],
    )
    return stats
