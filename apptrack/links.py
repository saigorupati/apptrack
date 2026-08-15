"""Gmail deep links from RFC 822 Message-IDs.

Gmail's search operator `rfc822msgid:` finds a message by its Message-ID header,
so `https://mail.google.com/mail/u/0/#search/rfc822msgid:<id>` opens the exact
email. This is the documented, stable way to deep-link a Gmail message.
"""

from __future__ import annotations

from urllib.parse import quote


def gmail_link(message_id: str) -> str:
    """Return a Gmail URL for the message, or '' if no Message-ID is known."""
    mid = (message_id or "").strip().strip("<>")
    if not mid:
        return ""
    return "https://mail.google.com/mail/u/0/#search/" + quote(f"rfc822msgid:{mid}", safe="")
