from apptrack import rules
from apptrack.models import Status

from . import fixtures as fx


def test_applied_greenhouse():
    v = rules.classify(fx.APPLIED_GREENHOUSE)
    assert v is not None
    assert v.status == Status.APPLIED
    assert v.company == "Stripe"
    assert "Backend Engineer" in v.role


def test_applied_workday_company_and_role():
    v = rules.classify(fx.APPLIED_WORKDAY)
    assert v is not None
    assert v.status == Status.APPLIED
    assert v.company == "Figma"
    assert "Software Engineer II" in v.role


def test_rejection_classic():
    v = rules.classify(fx.REJECT_CLASSIC)
    assert v is not None
    assert v.status == Status.REJECTED
    assert v.company == "Notion"


def test_rejection_soft():
    v = rules.classify(fx.REJECT_SOFT)
    assert v is not None
    assert v.status == Status.REJECTED


def test_interview_invite():
    v = rules.classify(fx.INTERVIEW_INVITE)
    assert v is not None
    assert v.status == Status.INTERVIEW
    assert v.company == "Airbnb"


def test_assessment_is_interview_stage():
    v = rules.classify(fx.INTERVIEW_ASSESSMENT)
    assert v is not None
    assert v.status == Status.INTERVIEW


def test_offer():
    v = rules.classify(fx.OFFER_EMAIL)
    assert v is not None
    assert v.status == Status.OFFER
    assert v.company == "Vercel"


def test_rejection_beats_interview_phrases_in_same_email():
    # Rejection wording plus 'interview' mention → REJECTED wins
    v = rules.classify(fx.SAME_THREAD_REJECT)
    assert v is not None
    assert v.status == Status.REJECTED


def test_ambiguous_returns_none_for_llm():
    # Recruiter follow-up with no clear signal words → rules should defer
    assert rules.classify(fx.AMBIGUOUS_RECRUITER) is None


def test_company_never_the_ats():
    for e in (fx.APPLIED_GREENHOUSE, fx.APPLIED_LEVER, fx.REJECT_CLASSIC):
        v = rules.classify(e)
        assert v is not None
        assert v.company.lower() not in ("greenhouse", "lever", "workday", "hire")
