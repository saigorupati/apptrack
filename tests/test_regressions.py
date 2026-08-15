"""Regression tests from real misclassifications in the first 30-day backfill."""

from apptrack import prefilter, rules
from apptrack.rules import _clean_company, _clean_role
from apptrack.store import normalize_company

from . import fixtures as fx

AMAZON_LOCKER = fx.make_email(
    'Amazon Locker <no-reply@amazon.com>',
    "You have a package to pick up at the Packages Amazon Locker",
    "Your package is ready for pickup. Use pickup code 123456. This opportunity at the locker expires in 3 days.",
)

AMAZON_PHARMACY = fx.make_email(
    'Amazon Pharmacy <pharmacy@amazon.com>',
    "Finish signing up in minutes",
    "Complete your Amazon Pharmacy sign up to get your prescriptions delivered. Next steps: verify your insurance.",
)

WELLFOUND_ALERT = fx.make_email(
    'Wellfound <team@wellfound.com>',
    "New jobs: Staff Software Engineer, Data Platform at SentiLink and 4 more",
    "New jobs matching your profile. Staff Software Engineer at SentiLink. Take-home assessment friendly companies hiring now.",
)

WELLFOUND_REAL = fx.make_email(
    'Wellfound <team@wellfound.com>',
    "Your application was sent to Rho",
    "Your application was sent to Rho for Software Engineer.",
)

HEALTH_INSURANCE = fx.make_email(
    'Health For CA <info@healthforca.com>',
    "We received your application",
    "Thank you, we received your application for health insurance coverage. "
    "Your enrollment period ends soon. A licensed agent will review your health plan options and premium.",
)


def test_package_and_signup_noise_dropped():
    assert not prefilter.is_candidate(AMAZON_LOCKER)
    assert not prefilter.is_candidate(AMAZON_PHARMACY)


def test_wellfound_alerts_gated_but_real_applications_kept():
    assert not prefilter.is_candidate(WELLFOUND_ALERT)
    assert prefilter.is_candidate(WELLFOUND_REAL)


def test_insurance_application_dropped():
    assert not prefilter.is_candidate(HEALTH_INSURANCE)


def test_company_cleanup():
    assert _clean_company("Notion's") == "Notion"
    assert _clean_company("McGraw Hill @ icims") == "McGraw Hill"
    assert _clean_company("Flatiron a few months ago") == "Flatiron"
    assert _clean_company("Match Group") == "Match Group"
    assert _clean_company("Health For CA") == "Health For CA"  # connectors kept


def test_role_cleanup():
    assert _clean_role("current") == ""
    assert _clean_role("the") == ""
    assert _clean_role("our (USA) Software Engineer II") == "(USA) Software Engineer II"
    assert _clean_role("Software Engineer, Diagnostics") == "Software Engineer, Diagnostics"


def test_normalize_company_variants_merge():
    assert normalize_company("Ramp Financial") == normalize_company("Ramp")
    assert normalize_company("Amazon.com Services LLC") == normalize_company("Amazon")
    assert normalize_company("Harbinger Motors Inc.") == normalize_company("Harbinger Motors Inc")
    assert normalize_company("CoStar Group") == "costar"
    # Distinct companies stay distinct
    assert normalize_company("Stripe") != normalize_company("Striped")


def test_real_fixtures_still_pass():
    assert prefilter.is_candidate(fx.APPLIED_GREENHOUSE)
    assert prefilter.is_candidate(fx.REJECT_CLASSIC)
    v = rules.classify(fx.APPLIED_GREENHOUSE)
    assert v.company == "Stripe"


def test_possessive_stripped_after_truncation():
    assert _clean_company("Notion's") == "Notion"
    assert _clean_company("Notion's Software Engineer") == "Notion"


def test_role_with_apply_fragment_rejected():
    assert _clean_role("time to apply to the Software Engineer") == ""
    assert _clean_role("Software Engineer") == "Software Engineer"
