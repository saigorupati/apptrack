"""Regression tests from replaying ~3 months (4,021 emails) of real history."""

from datetime import datetime

from apptrack import prefilter, rules
from apptrack.imap_client import clean_text
from apptrack.models import Status
from apptrack.scheduler import _next_run
from apptrack.store import normalize_company

from . import fixtures as fx

# --- prefilter: noise that used to reach the rules / LLM ---------------------

INDEED_RECOMMENDATION = fx.make_email(
    "Indeed <donotreply@match.indeed.com>",
    "Software Engineer, RL Data @ Anthropic",
    "Hi Sai,\n\nYour background in software engineering could be a strong match for this "
    "Software Engineer, RL Data role at Anthropic. If you're interested, you can apply now "
    "or view the job description for more details.",
)

INDEED_REAL_APPLICATION = fx.make_email(
    "Indeed Apply <indeedapply@indeed.com>",
    "Indeed Application: Software Engineer",
    "Your application has been submitted to Acme Corp. Good luck!",
)

SPORTS_NEWSLETTER = fx.make_email(
    "The Athletic Pulse <TheAthletic@e1.theathletic.com>",
    "World. Cup. Final.",
    "Good morning! Unfortunately the final went to penalties. An interview with the coach "
    "on his role at the club and the next steps for the team.",
)

LEETCODE_DIGEST = fx.make_email(
    "LeetCode <no-reply@leetcode.com>",
    "LeetCode Weekly Digest",
    "A LeetCoder shared an offer and interview experience. Join the Daily LeetCoding Challenge!",
)

JOB_SEARCH_MARKETING = fx.make_email(
    "Leela <hello@scale.jobs>",
    "What actually happens in the first 30 days",
    "47 tailored applications submitted, three separate interview processes underway by Day 18.",
)

IDENTITY_CHECK = fx.make_email(
    "Uber <no-reply@uber.com>",
    "Confirm your identity",
    "Use this code to confirm your identity and continue your application.",
)

INCOMPLETE_APPLICATION = fx.make_email(
    "Mercor <team@mercor.com>",
    "Last reminder to finish your STEM PhDs and Technical Domain Experts application",
    "Your application is still incomplete. Continue application.",
)

ONE_PASSWORD = fx.make_email(
    "1Password <no-reply@ashbyhq.com>",
    "Thank you for applying to 1Password!",
    "Hi Sainikit, thanks for applying. We'll review your application soon.",
)


def test_job_board_recommendations_are_not_candidates():
    assert not prefilter.is_candidate(INDEED_RECOMMENDATION)
    assert prefilter.is_candidate(INDEED_REAL_APPLICATION)


def test_newsletters_and_marketing_are_not_candidates():
    for e in (SPORTS_NEWSLETTER, LEETCODE_DIGEST, JOB_SEARCH_MARKETING, IDENTITY_CHECK, INCOMPLETE_APPLICATION):
        assert not prefilter.is_candidate(e), e.subject


def test_password_noise_word_does_not_eat_company_names():
    assert prefilter.is_candidate(ONE_PASSWORD)
    v = rules.classify(ONE_PASSWORD)
    assert v.status == Status.APPLIED and v.company == "1Password"


def test_weak_phrases_only_count_in_subject():
    body_only = fx.make_email(
        "Some Media <news@example-media.com>", "Our top stories of the week",
        "Unfortunately, the interview with the new hire for the role at the club went badly.",
    )
    subject_hit = fx.make_email(
        "Jordan <jordan@acme.com>", "Interview availability", "Let me know what works for you.",
    )
    assert not prefilter.is_candidate(body_only)
    assert prefilter.is_candidate(subject_hit)


# --- rules: status ----------------------------------------------------------

CONDITIONAL_REJECTION_ACK = fx.make_email(
    "Figma <no-reply@figma.com>",
    "Thank you for your application to Figma",
    "Hi Sainikit, Thank you for your interest in Figma! We wanted to let you know we received your "
    "application for Software Engineer - Growth & Monetization. If you are not selected for this "
    "position, keep an eye on our jobs page as we're growing and adding openings.",
)

SHORTLIST_ACK = fx.make_email(
    "Bjak Talent Acquisition Team <no-reply@ashbyhq.com>",
    "We’ve Received Your Application",
    "Hi Sainikit, We're glad to have received your application. If you're shortlisted, you'll receive "
    "a follow-up email outlining the next steps which will consist of a mix of assessments and interviews.",
)

WALMART_REJECTION = fx.make_email(
    "noreply@walmart.com",
    "Thank you for your interest",
    "Hi Sainikit, Thanks very much for your interest in joining Walmart. We wanted to let you know that "
    "we have decided not to move forward with your application for our Software Engineer II position.",
)

COLD_OUTREACH = fx.make_email(
    "Carlos De Guzman <careers@hertz.com>",
    "Hertz is hiring Part-Time Car Wash Detailers in Jersey City, NJ",
    "I'm a recruiter with Hertz. We're looking for a Car Wash Detailer to join our team in a "
    "part-time, on-site role. Would love to chat if you're interested!",
)


def test_conditional_rejection_is_an_acknowledgement():
    v = rules.classify(CONDITIONAL_REJECTION_ACK)
    assert v.status == Status.APPLIED and v.company == "Figma"


def test_process_description_is_not_an_interview():
    v = rules.classify(SHORTLIST_ACK)
    assert v.status == Status.APPLIED and v.company == "Bjak"


def test_decided_not_to_move_forward_is_rejection():
    assert rules.classify(WALMART_REJECTION).status == Status.REJECTED


def test_thanks_for_applying_is_applied():
    e = fx.make_email("noreply@google.com", "Thanks for applying to Google", "Thanks for applying to Google!")
    v = rules.classify(e)
    assert v.status == Status.APPLIED and v.company == "Google"


def test_on_site_role_in_outreach_is_not_interview():
    v = rules.classify(COLD_OUTREACH)
    assert v is None or v.status != Status.INTERVIEW


# --- rules: company extraction ----------------------------------------------

def test_nbsp_does_not_break_company_extraction():
    e = fx.make_email(
        "teradatarecruiting@msg.gr8people.com",
        clean_text("The Software Engineer Position at Teradata Is No Longer Available"),
        clean_text("Unfortunately, we have decided to pursue other candidates.&nbsp;Teradata Talent"),
    )
    assert rules.classify(e).company == "Teradata"


def test_trailing_connector_dropped():
    e = fx.make_email(
        "Abridge <no-reply@ashbyhq.com>", "Sainikit - Update from Abridge",
        "Thank you for your interest in the Member of Technical Staff role at Abridge and for taking the "
        "time to complete the take-home. We've decided to move forward with other candidates.",
    )
    assert rules.classify(e).company == "Abridge"


def test_applying_to_names_company_not_sender_domain():
    e = fx.make_email("no-reply@x.ai", "Thank you for applying to SpaceXSI", "Your application has been received.")
    assert rules.classify(e).company == "SpaceXSI"


def test_applying_to_a_role_is_not_a_company():
    e = fx.make_email(
        "Match Group <no-reply@hire.lever.co>",
        "Thanks for applying to Software Engineer II, Backend , here is a link",
        "We received your application.",
    )
    assert rules.classify(e).company == "Match Group"


def test_display_name_prefix_and_stacked_suffixes():
    paypal = fx.make_email("Careers PayPal <careers@paypal.com>", "We have received your application", "Thanks!")
    roblox = fx.make_email(
        "Roblox Assessment <noreply@email.roblox.com>", "Your Roblox Assessments Invitation",
        "We're thrilled to invite you to the assessments! Your technical assessment expires in 7 days.",
    )
    assert rules.classify(paypal).company == "PayPal"
    assert rules.classify(roblox).company == "Roblox"


def test_normalize_more_suffixes():
    assert normalize_company("Anduril Industries") == normalize_company("Anduril")
    assert normalize_company("DoorDash USA") == normalize_company("DoorDash")


# --- outreach & LLM guards ----------------------------------------------------

def test_cold_outreach_is_not_a_candidate():
    outreach = fx.make_email(
        "George <geo@joinharmony.ai>", "job offer / harmony ai",
        "Hi Sainikit, Saw your background and love what you've been up to. We're hiring full-stack "
        "engineers! I wanted to invite you to apply here. This is the next step.",
    )
    assert not prefilter.is_candidate(outreach)


def test_llm_offer_without_evidence_needs_review():
    from apptrack.llm import _check_evidence
    from apptrack.models import Verdict

    followup = fx.make_email("George <geo@joinharmony.ai>", "Re: job offer / harmony ai", "Just following up here :)")
    v = _check_evidence(Verdict(True, Status.OFFER, "harmony ai", "", 0.8, "llm"), followup)
    assert v.status == Status.NEEDS_REVIEW
    real = fx.make_email("hr@acme.com", "Congratulations!", "We're thrilled to offer you the role. Start date: Jan 5.")
    assert _check_evidence(Verdict(True, Status.OFFER, "Acme", "", 0.9, "llm"), real).status == Status.OFFER


# --- scheduler ----------------------------------------------------------------

def test_next_run_is_wall_clock_hour():
    now = datetime(2026, 11, 1, 3, 0).astimezone()  # after the run hour
    nxt = _next_run(now, 2)
    assert (nxt.hour, nxt.minute) == (2, 0)
    assert nxt > now
