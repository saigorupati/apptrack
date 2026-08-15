from apptrack import prefilter

from . import fixtures as fx


def test_ats_senders_are_candidates():
    for e in (fx.APPLIED_GREENHOUSE, fx.APPLIED_LEVER, fx.APPLIED_WORKDAY, fx.INTERVIEW_ASSESSMENT):
        assert prefilter.is_candidate(e), e.subject


def test_phrase_matches_are_candidates():
    for e in (fx.REJECT_SOFT, fx.INTERVIEW_INVITE, fx.OFFER_EMAIL, fx.AMBIGUOUS_RECRUITER):
        assert prefilter.is_candidate(e), e.subject


def test_noise_is_dropped():
    for e in (fx.NOISE_JOB_ALERT, fx.NOISE_NEWSLETTER, fx.NOISE_PERSONAL, fx.NOISE_RECEIPT):
        assert not prefilter.is_candidate(e), e.subject


def test_linkedin_real_application_kept_alerts_dropped():
    assert prefilter.is_candidate(fx.LINKEDIN_REAL_APPLICATION)
    assert not prefilter.is_candidate(fx.NOISE_JOB_ALERT)


def test_ats_source_subdomains():
    assert prefilter.ats_source("greenhouse.io") == "Greenhouse"
    assert prefilter.ats_source("mail.greenhouse.io") == "Greenhouse"
    assert prefilter.ats_source("hire.lever.co") == "Lever"
    assert prefilter.ats_source("stripe.com") is None
