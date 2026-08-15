"""Read-only Gmail IMAP access with UID-incremental fetching.

Uses [Gmail]/All Mail so archived messages are seen and Spam/Trash are not.
Nothing is ever written: no flags, no labels, no expunge (BODY.PEEK only).
"""

from __future__ import annotations

import email as email_lib
import email.policy
import logging
import re
from datetime import datetime, timedelta, timezone

from imapclient import IMAPClient

from .config import Config
from .models import Email
from .store import Store

log = logging.getLogger(__name__)

ALL_MAIL = "[Gmail]/All Mail"
_BATCH = 200

_HTML_TAG_RE = re.compile(r"<(?:script|style)[^>]*>.*?</(?:script|style)>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]{2,}")
_NL_RE = re.compile(r"\n{3,}")


def _html_to_text(html: str) -> str:
    text = _HTML_TAG_RE.sub(" ", html)
    text = re.sub(r"<br\s*/?>|</p>|</div>|</tr>", "\n", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub(" ", text)
    text = email_lib.utils.unquote(text)
    import html as html_mod

    text = html_mod.unescape(text)
    text = _WS_RE.sub(" ", text)
    return _NL_RE.sub("\n\n", text).strip()


def _best_body(msg: email_lib.message.EmailMessage) -> str:
    plain = msg.get_body(preferencelist=("plain",))
    if plain is not None:
        try:
            return plain.get_content().strip()
        except Exception:
            pass
    html = msg.get_body(preferencelist=("html",))
    if html is not None:
        try:
            return _html_to_text(html.get_content())
        except Exception:
            pass
    return ""


def _parse_message(uid: int, raw: bytes, thread_id: str) -> Email | None:
    try:
        msg = email_lib.message_from_bytes(raw, policy=email.policy.default)
    except Exception as exc:
        log.warning("Failed to parse uid=%s: %s", uid, exc)
        return None

    sender = str(msg.get("From", ""))
    _, addr = email_lib.utils.parseaddr(sender)
    addr = addr.lower()
    domain = addr.rsplit("@", 1)[-1] if "@" in addr else ""

    date_hdr = msg.get("Date")
    try:
        date = email_lib.utils.parsedate_to_datetime(date_hdr) if date_hdr else None
    except Exception:
        date = None
    if date is None:
        date = datetime.now(timezone.utc)
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)

    return Email(
        uid=uid,
        message_id=str(msg.get("Message-ID", "")).strip(),
        thread_id=thread_id,
        sender=sender,
        sender_email=addr,
        sender_domain=domain,
        subject=str(msg.get("Subject", "")).strip(),
        body=_best_body(msg)[:20000],
        date=date.astimezone(timezone.utc),
    )


class GmailReader:
    def __init__(self, cfg: Config, store: Store):
        self.cfg = cfg
        self.store = store

    def fetch_new(self):
        """Yield unprocessed Email objects since the last sync (or backfill window)."""
        with IMAPClient(self.cfg.imap_host, ssl=True) as client:
            client.login(self.cfg.gmail_address, self.cfg.gmail_app_password)
            info = client.select_folder(ALL_MAIL, readonly=True)
            uidvalidity = int(info[b"UIDVALIDITY"])

            stored_validity = self.store.get_state("uidvalidity")
            last_uid = self.store.get_state("last_uid")
            if stored_validity != str(uidvalidity):
                # First run, or Gmail reset UIDs → date-window search
                since = (datetime.now(timezone.utc) - timedelta(days=self.cfg.backfill_days)).date()
                uids = client.search(["SINCE", since])
                log.info("Backfill search since %s: %d messages (uidvalidity=%s)", since, len(uids), uidvalidity)
            else:
                uids = client.search(["UID", f"{int(last_uid) + 1}:*"])
                # Gmail returns the last message even when the range is empty; filter it.
                uids = [u for u in uids if u > int(last_uid)]
                log.info("Incremental search after uid=%s: %d new messages", last_uid, len(uids))

            max_seen = int(last_uid) if (last_uid and stored_validity == str(uidvalidity)) else 0
            for i in range(0, len(uids), _BATCH):
                batch = uids[i : i + _BATCH]
                resp = client.fetch(batch, [b"BODY.PEEK[]", b"X-GM-THRID"])
                for uid in batch:
                    data = resp.get(uid)
                    if not data:
                        continue
                    max_seen = max(max_seen, uid)
                    if self.store.is_processed(uid, uidvalidity):
                        continue
                    raw = data.get(b"BODY[]")
                    thrid = str(data.get(b"X-GM-THRID", "") or "")
                    if raw is None:
                        continue
                    parsed = _parse_message(uid, raw, thrid)
                    if parsed:
                        yield uidvalidity, parsed

            # Persist watermark only after the full walk
            self.store.set_state("uidvalidity", str(uidvalidity))
            if max_seen:
                self.store.set_state("last_uid", str(max_seen))
