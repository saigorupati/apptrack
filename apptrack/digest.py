"""Nightly digest email over Gmail SMTP."""

from __future__ import annotations

import logging
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

from .config import Config
from .models import Application, Status
from .sheet import days_silent, needs_followup
from .store import Store

log = logging.getLogger(__name__)


def build_digest(cfg: Config, store: Store, run_stats: dict) -> tuple[str, str]:
    """Return (subject, body)."""
    now = datetime.now(timezone.utc)
    apps = store.all_applications()

    new_apps: list[Application] = run_stats.get("new_app_objs", [])
    status_changes: list[tuple[Application, str]] = run_stats.get("status_change_objs", [])
    followups = [a for a in apps if needs_followup(a, now, cfg.followup_days)]
    review = [a for a in apps if a.status == Status.NEEDS_REVIEW]

    # Count rejections/interviews whether they arrived as a status change or as
    # a brand-new row (e.g. a backfilled rejection with no earlier email seen).
    n_rej = sum(1 for _, s in status_changes if s == "REJECTED") + sum(
        1 for a in new_apps if a.status == Status.REJECTED
    )
    n_int = sum(1 for _, s in status_changes if s in ("INTERVIEW", "OFFER")) + sum(
        1 for a in new_apps if a.status in (Status.INTERVIEW, Status.OFFER)
    )
    subject = (
        f"AppTrack — {len(new_apps)} new, {n_rej} rejection{'s' if n_rej != 1 else ''}, "
        f"{len(followups)} to follow up"
    )

    lines: list[str] = []
    if new_apps:
        lines.append("NEW APPLICATIONS LOGGED")
        for a in new_apps:
            lines.append(f"  • {a.company} — {a.role or '(role unknown)'}")
        lines.append("")
    if status_changes:
        lines.append("STATUS CHANGES")
        for a, new_status in status_changes:
            lines.append(f"  • {a.company} — {a.role or '(role unknown)'} → {new_status}")
        lines.append("")
    if followups:
        lines.append(f"NEEDS FOLLOW-UP (>{cfg.followup_days}d silent)")
        for a in followups:
            lines.append(f"  • {a.company} — {a.role or '(role unknown)'} — applied {days_silent(a, now)}d ago")
        lines.append("")
    if review:
        lines.append("NEEDS YOUR REVIEW (couldn't auto-classify)")
        for a in review:
            lines.append(f"  • {a.company or '(unknown company)'} — {a.last_subject[:80]}")
        lines.append("")
    if run_stats.get("error"):
        lines.append(f"RUN ERROR: {run_stats['error']}")
        lines.append("")
    if not lines:
        lines.append("Nothing new tonight. All quiet.")
        lines.append("")

    active = sum(1 for a in apps if a.status in (Status.APPLIED, Status.INTERVIEW, Status.OFFER))
    rejected = sum(1 for a in apps if a.status == Status.REJECTED)
    lines.append(f"Totals: {active} active · {rejected} rejected · {len(apps)} tracked")
    lines.append(f"Sheet: https://docs.google.com/spreadsheets/d/{cfg.sheet_id}")

    return subject, "\n".join(lines)


def send_digest(cfg: Config, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = cfg.gmail_address
    msg["To"] = cfg.digest_to
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(cfg.gmail_address, cfg.gmail_app_password)
        smtp.send_message(msg)
    log.info("Digest sent to %s: %s", cfg.digest_to, subject)
