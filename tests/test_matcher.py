from datetime import datetime, timezone

import pytest

from apptrack import matcher, rules
from apptrack.models import Status, Verdict
from apptrack.store import Store, normalize_company

from . import fixtures as fx


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    yield s
    s.close()


def test_normalize_company():
    assert normalize_company("Stripe, Inc.") == "stripe"
    assert normalize_company("Datadog Inc") == "datadog"
    assert normalize_company("Foo Labs") == "foo"
    assert normalize_company("X.AI") == "xai"


def test_new_application_created(store):
    v = rules.classify(fx.APPLIED_GREENHOUSE)
    r = matcher.upsert(store, fx.APPLIED_GREENHOUSE, v)
    assert r.created
    apps = store.all_applications()
    assert len(apps) == 1
    assert apps[0].company == "Stripe"
    assert apps[0].status == Status.APPLIED
    assert apps[0].applied_at is not None


def test_same_thread_rejection_updates_not_duplicates(store):
    v1 = rules.classify(fx.APPLIED_GREENHOUSE)
    matcher.upsert(store, fx.APPLIED_GREENHOUSE, v1)
    v2 = rules.classify(fx.SAME_THREAD_REJECT)
    r2 = matcher.upsert(store, fx.SAME_THREAD_REJECT, v2)
    assert not r2.created
    assert r2.status_changed
    apps = store.all_applications()
    assert len(apps) == 1
    assert apps[0].status == Status.REJECTED


def test_company_match_without_thread(store):
    v1 = rules.classify(fx.APPLIED_GREENHOUSE)
    matcher.upsert(store, fx.APPLIED_GREENHOUSE, v1)
    # Different thread, same company+role → same application
    reject = fx.make_email(
        'Stripe <no-reply@greenhouse.io>',
        "Your Stripe application",
        "Unfortunately we will not be moving forward with your application for the Backend Engineer position at Stripe.",
    )
    v2 = rules.classify(reject)
    r2 = matcher.upsert(store, reject, v2)
    assert not r2.created
    assert store.all_applications()[0].status == Status.REJECTED


def test_status_never_regresses(store):
    email1 = fx.INTERVIEW_INVITE
    v1 = rules.classify(email1)
    matcher.upsert(store, email1, v1)
    # A later "thanks for applying" style email must not demote INTERVIEW → APPLIED
    applied = fx.make_email(
        'Airbnb <no-reply@airbnb.com>',
        "Thank you for applying to Airbnb",
        "We received your application for the Backend Engineer role at Airbnb.",
        thread_id=email1.thread_id,
    )
    v2 = rules.classify(applied)
    r2 = matcher.upsert(store, applied, v2)
    assert not r2.status_changed
    assert store.all_applications()[0].status == Status.INTERVIEW


def test_rejected_is_terminal(store):
    v1 = rules.classify(fx.REJECT_CLASSIC)
    matcher.upsert(store, fx.REJECT_CLASSIC, v1)
    late = fx.make_email(
        'Notion <no-reply@greenhouse.io>',
        "Interview invitation",
        "We'd like to schedule your interview for the Fullstack Engineer role at Notion.",
        thread_id=fx.REJECT_CLASSIC.thread_id,
    )
    v2 = rules.classify(late)
    r2 = matcher.upsert(store, late, v2)
    assert not r2.status_changed
    assert store.all_applications()[0].status == Status.REJECTED


def test_reapply_after_rejection_creates_new_row(store):
    v1 = rules.classify(fx.REJECT_CLASSIC)
    matcher.upsert(store, fx.REJECT_CLASSIC, v1)
    reapply = fx.make_email(
        'Notion <no-reply@greenhouse.io>',
        "Thank you for applying to Notion!",
        "We received your application for the Fullstack Engineer role at Notion.",
    )
    v2 = rules.classify(reapply)
    r2 = matcher.upsert(store, reapply, v2)
    assert r2.created
    assert len(store.all_applications()) == 2


def test_different_roles_same_company_are_separate(store):
    v1 = Verdict(True, Status.APPLIED, "Google", "Software Engineer, Search", 0.9)
    e1 = fx.make_email('Google <no-reply@google.com>', "app 1", "x")
    matcher.upsert(store, e1, v1)
    v2 = Verdict(True, Status.APPLIED, "Google", "Product Manager, Ads", 0.9)
    e2 = fx.make_email('Google <no-reply@google.com>', "app 2", "y")
    r2 = matcher.upsert(store, e2, v2)
    assert r2.created
    assert len(store.all_applications()) == 2


def test_processed_email_idempotency(store):
    store.mark_processed(101, 5, "<x@y>", None, "ignored")
    assert store.is_processed(101, 5)
    assert not store.is_processed(101, 6)  # different uidvalidity
    store.mark_processed(101, 5, "<x@y>", None, "ignored")  # no crash on repeat


def test_reengagement_after_rejection_creates_new_row(store):
    """Company rejected you, then a recruiter comes back with an interview → new row."""
    v1 = rules.classify(fx.REJECT_CLASSIC)
    matcher.upsert(store, fx.REJECT_CLASSIC, v1)
    revive = fx.make_email(
        'Notion <recruiting@makenotion.com>',
        "Revisiting your application - Notion",
        "Hi Sai, a new Fullstack Engineer role opened up and we'd like to schedule an interview with you at Notion.",
    )
    v2 = matcher.Verdict(True, Status.INTERVIEW, "Notion", "Fullstack Engineer", 0.9)
    r2 = matcher.upsert(store, revive, v2)
    assert r2.created
    apps = store.all_applications()
    assert len(apps) == 2
    assert {a.status for a in apps} == {Status.REJECTED, Status.INTERVIEW}


def test_roleless_email_prefers_same_ats(store):
    """A rejection with no role should attach to the app on the same ATS platform."""
    e1 = fx.make_email('Google <no-reply@greenhouse.io>', "a1", "x")
    matcher.upsert(store, e1, matcher.Verdict(True, Status.APPLIED, "Google", "Backend Engineer", 0.9))
    e2 = fx.make_email('Google <google@myworkday.com>', "a2", "y")
    matcher.upsert(store, e2, matcher.Verdict(True, Status.APPLIED, "Google", "Data Engineer", 0.9))
    # Roleless rejection arriving via Workday → must hit the Workday application
    rej = fx.make_email(
        'Google <google@myworkday.com>', "Update on your application",
        "Unfortunately we will not be moving forward.",
    )
    r = matcher.upsert(store, rej, matcher.Verdict(True, Status.REJECTED, "Google", "", 0.9))
    assert not r.created
    apps = {a.role: a.status for a in store.all_applications()}
    assert apps["Data Engineer"] == Status.REJECTED
    assert apps["Backend Engineer"] == Status.APPLIED


def test_role_matching_semantics():
    from apptrack.matcher import _roles_match
    # Formatting variants of the same title merge
    assert _roles_match("Software Engineer - Backend", "Software Engineer, Backend")
    assert _roles_match("Software Engineer", "Senior Software Engineer")  # substring
    assert _roles_match("", "anything")
    # Genuinely different roles stay separate
    assert not _roles_match("Backend Engineer", "Data Engineer")
    assert not _roles_match("Software Engineer I", "Software Engineer III")
    assert not _roles_match("Product Manager", "Software Engineer")
