"""Nightly digest email over Gmail SMTP."""

from __future__ import annotations

import html
import logging
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

from .config import Config
from .links import gmail_link
from .models import Application, Status
from .sheet import days_silent, needs_followup
from .store import Store

log = logging.getLogger(__name__)


def _item(a: Application, suffix: str = "") -> str:
    """One HTML list item: 'Company — Role' linked to the email when possible."""
    label = html.escape(f"{a.company} — {a.role or '(role unknown)'}")
    url = gmail_link(a.last_message_id)
    body = f'<a href="{html.escape(url)}">{label}</a>' if url else label
    return f"<li>{body}{html.escape(suffix)}</li>"


def build_digest(cfg: Config, store: Store, run_stats: dict) -> tuple[str, str, str]:
    """Return (subject, text_body, html_body)."""
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
    parts: list[str] = []
    if new_apps:
        lines.append("NEW APPLICATIONS LOGGED")
        parts.append("<h3>New applications logged</h3><ul>")
        for a in new_apps:
            lines.append(f"  • {a.company} — {a.role or '(role unknown)'}")
            parts.append(_item(a))
        lines.append("")
        parts.append("</ul>")
    if status_changes:
        lines.append("STATUS CHANGES")
        parts.append("<h3>Status changes</h3><ul>")
        for a, new_status in status_changes:
            lines.append(f"  • {a.company} — {a.role or '(role unknown)'} → {new_status}")
            parts.append(_item(a, f" → {new_status}"))
        lines.append("")
        parts.append("</ul>")
    if followups:
        lines.append(f"NEEDS FOLLOW-UP (>{cfg.followup_days}d silent)")
        parts.append(f"<h3>Needs follow-up (&gt;{cfg.followup_days}d silent)</h3><ul>")
        for a in followups:
            lines.append(f"  • {a.company} — {a.role or '(role unknown)'} — applied {days_silent(a, now)}d ago")
            parts.append(_item(a, f" — applied {days_silent(a, now)}d ago"))
        lines.append("")
        parts.append("</ul>")
    if review:
        lines.append("NEEDS YOUR REVIEW (couldn't auto-classify)")
        parts.append("<h3>Needs your review (couldn't auto-classify)</h3><ul>")
        for a in review:
            lines.append(f"  • {a.company or '(unknown company)'} — {a.last_subject[:80]}")
            url = gmail_link(a.last_message_id)
            label = html.escape(f"{a.company or '(unknown company)'} — {a.last_subject[:80]}")
            parts.append(f'<li><a href="{html.escape(url)}">{label}</a></li>' if url else f"<li>{label}</li>")
        lines.append("")
        parts.append("</ul>")
    if run_stats.get("error"):
        lines.append(f"RUN ERROR: {run_stats['error']}")
        lines.append("")
        parts.append(f"<p><b>Run error:</b> {html.escape(str(run_stats['error']))}</p>")
    if not lines:
        lines.append("Nothing new tonight. All quiet.")
        lines.append("")
        parts.append("<p>Nothing new tonight. All quiet.</p>")

    active = sum(1 for a in apps if a.status in (Status.APPLIED, Status.INTERVIEW, Status.OFFER))
    rejected = sum(1 for a in apps if a.status == Status.REJECTED)
    sheet_url = f"https://docs.google.com/spreadsheets/d/{cfg.sheet_id}"
    lines.append(f"Totals: {active} active · {rejected} rejected · {len(apps)} tracked")
    lines.append(f"Sheet: {sheet_url}")
    parts.append(
        f"<p style='color:#736f68'>Totals: {active} active · {rejected} rejected · {len(apps)} tracked<br>"
        f'<a href="{sheet_url}">Open the sheet</a></p>'
    )

    html_body = (
        "<div style='font-family:system-ui,-apple-system,sans-serif;font-size:14px;"
        "line-height:1.5;color:#1e1e1c;max-width:640px'>" + "".join(parts) + "</div>"
    )
    return subject, "\n".join(lines), html_body


def send_digest(cfg: Config, subject: str, body: str, html_body: str | None = None) -> None:
    msg = EmailMessage()
    msg["From"] = cfg.gmail_address
    msg["To"] = cfg.digest_to
    msg["Subject"] = subject
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(cfg.gmail_address, cfg.gmail_app_password)
        smtp.send_message(msg)
    log.info("Digest sent to %s: %s", cfg.digest_to, subject)
