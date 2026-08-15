"""Link classified emails to applications and apply status transitions."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .models import Application, Email, Status, Verdict
from .store import Store, normalize_company

log = logging.getLogger(__name__)


def _role_tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def _roles_match(a: str, b: str) -> bool:
    """Token-based role comparison; empty roles match anything (many emails omit it).

    Punctuation/formatting variants of the same title match ("Software Engineer -
    Backend" vs "Software Engineer, Backend"), but titles that merely share a word
    do not ("Backend Engineer" vs "Data Engineer"), and neither do adjacent levels
    ("Software Engineer I" vs "Software Engineer II").
    """
    if not a or not b:
        return True
    ta, tb = _role_tokens(a), _role_tokens(b)
    if not ta or not tb:
        return True
    if ta <= tb or tb <= ta:  # one title contains the other's every word
        return True
    jaccard = len(ta & tb) / len(ta | tb)
    return jaccard > 0.5


@dataclass
class UpsertResult:
    app_id: int
    created: bool
    status_changed: bool
    old_status: Status | None
    new_status: Status


def upsert(store: Store, email: Email, verdict: Verdict) -> UpsertResult:
    """Attach the email to an existing application or create a new one."""
    assert verdict.status is not None

    app = _find(store, email, verdict)

    if app is None:
        new_app = Application(
            id=None,
            company=verdict.company or "(unknown)",
            role=verdict.role,
            status=verdict.status,
            applied_at=email.date if verdict.status == Status.APPLIED else None,
            last_update=email.date,
            last_subject=email.subject,
            ats_source=_ats_of(email),
            thread_ids=[email.thread_id] if email.thread_id else [],
            last_message_id=email.message_id,
        )
        app_id = store.insert_application(new_app)
        return UpsertResult(app_id, True, True, None, verdict.status)

    old_status = app.status
    changed = _advance(app, verdict.status)

    # Enrich fields the earlier emails lacked
    if not app.role and verdict.role:
        app.role = verdict.role
    if not app.applied_at and verdict.status == Status.APPLIED:
        app.applied_at = email.date
    if not app.ats_source:
        app.ats_source = _ats_of(email)
    if email.date >= (app.last_update or email.date):
        app.last_update = email.date
        app.last_subject = email.subject
        app.last_message_id = email.message_id
    if email.thread_id and email.thread_id not in app.thread_ids:
        app.thread_ids.append(email.thread_id)

    store.update_application(app)
    return UpsertResult(app.id, False, changed, old_status, app.status)


def _find(store: Store, email: Email, verdict: Verdict) -> Application | None:
    # 1. Same Gmail thread → same application, always.
    if email.thread_id:
        app = store.find_by_thread(email.thread_id)
        if app:
            return app

    # 2. Same company + compatible role.
    company_norm = normalize_company(verdict.company)
    if not company_norm:
        return None
    candidates = [a for a in store.find_by_company(company_norm) if _roles_match(a.role, verdict.role)]
    if not candidates:
        return None

    # Every match is REJECTED and this isn't another rejection → the company is
    # back (re-application, or a recruiter reviving a closed application). A
    # terminal REJECTED row stays closed; start a fresh one.
    if verdict.status != Status.REJECTED and all(a.status == Status.REJECTED for a in candidates):
        return None

    # Prefer a live application over a rejected one.
    live = [a for a in candidates if a.status != Status.REJECTED]
    pool = live or candidates

    # Ambiguity guard: an email with no parseable role and several live roles at
    # the same company could attach to the wrong application. Prefer the one on
    # the same ATS platform; otherwise fall back to most recently updated.
    if not verdict.role and len(pool) > 1:
        email_ats = _ats_of(email)
        if email_ats:
            same_ats = [a for a in pool if a.ats_source == email_ats]
            if same_ats:
                pool = same_ats
        if len(pool) > 1:
            log.warning(
                "Ambiguous match: %s email (no role) fits %d applications at %s; using most recent",
                verdict.status.value, len(pool), verdict.company,
            )
    return pool[0]


def _advance(app: Application, new: Status) -> bool:
    """Apply status precedence. Returns True if status changed."""
    if app.status == new:
        return False
    if app.status == Status.REJECTED:
        return False  # terminal
    if new == Status.REJECTED or new.rank > app.status.rank:
        app.status = new
        return True
    if app.status == Status.NEEDS_REVIEW and new != Status.NEEDS_REVIEW:
        app.status = new
        return True
    return False


def _ats_of(email: Email) -> str:
    from .prefilter import ats_source

    return ats_source(email.sender_domain) or ""
