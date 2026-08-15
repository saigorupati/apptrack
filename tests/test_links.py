from datetime import datetime, timezone

from apptrack.links import gmail_link
from apptrack.models import Application, Status
from apptrack.sheet import _email_cell


def _app(**kw):
    defaults = dict(
        id=1, company="Stripe", role="Backend Engineer", status=Status.APPLIED,
        applied_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        last_update=datetime(2026, 8, 1, tzinfo=timezone.utc),
        last_subject="Thank you for applying", ats_source="Greenhouse",
    )
    defaults.update(kw)
    return Application(**defaults)


def test_gmail_link_strips_brackets_and_encodes():
    url = gmail_link("<abc+def@mail.greenhouse.io>")
    assert url.startswith("https://mail.google.com/mail/u/0/#search/")
    assert "rfc822msgid%3Aabc%2Bdef%40mail.greenhouse.io" in url
    assert gmail_link("") == ""
    assert gmail_link("  ") == ""


def test_email_cell_is_hyperlink_when_msgid_known():
    cell = _email_cell(_app(last_message_id="<x@y.com>"))
    assert cell.startswith('=HYPERLINK("https://mail.google.com/')
    assert '"Thank you for applying"' in cell


def test_email_cell_plain_text_without_msgid():
    assert _email_cell(_app(last_message_id="")) == "Thank you for applying"


def test_email_cell_escapes_quotes_in_subject():
    cell = _email_cell(_app(last_message_id="<x@y>", last_subject='Your "amazing" application'))
    assert '""amazing""' in cell
