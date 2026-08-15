"""Realistic email fixtures for classifier tests."""

from __future__ import annotations

from datetime import datetime, timezone

from apptrack.models import Email

_N = 0


def make_email(sender: str, subject: str, body: str, thread_id: str = "", date: datetime | None = None) -> Email:
    global _N
    _N += 1
    from email.utils import parseaddr

    _, addr = parseaddr(sender)
    addr = addr.lower()
    return Email(
        uid=_N,
        message_id=f"<fixture-{_N}@test>",
        thread_id=thread_id or f"thr-{_N}",
        sender=sender,
        sender_email=addr,
        sender_domain=addr.rsplit("@", 1)[-1] if "@" in addr else "",
        subject=subject,
        body=body,
        date=date or datetime(2026, 8, 1, tzinfo=timezone.utc),
    )


APPLIED_GREENHOUSE = make_email(
    'Stripe <no-reply@greenhouse.io>',
    "Thank you for applying to Stripe!",
    "Hi Sai,\n\nThank you for applying to the Backend Engineer position at Stripe. "
    "We have received your application and our recruiting team will review it shortly.\n\nStripe Recruiting",
)

APPLIED_LEVER = make_email(
    'Ramp <no-reply@hire.lever.co>',
    "We received your application",
    "Thank you for your interest in the Software Engineer - Backend role at Ramp. "
    "Your application was received and is under review.",
)

APPLIED_WORKDAY = make_email(
    'Figma Careers <figma@myworkday.com>',
    "Your application to Figma was received",
    "Dear Sai, thank you for applying. Your application for the Software Engineer II position "
    "has been submitted successfully. Ref: R-10422.",
)

REJECT_CLASSIC = make_email(
    'Notion <no-reply@greenhouse.io>',
    "Update on your application to Notion",
    "Hi Sai,\n\nThank you for your interest in the Fullstack Engineer role at Notion. "
    "After careful consideration, we have decided to move forward with other candidates at this time. "
    "We wish you the best in your job search.\n\nNotion Recruiting",
)

REJECT_SOFT = make_email(
    'Careers <careers@datadoghq.com>',
    "Your application status",
    "Hello Sai, unfortunately we will not be moving forward with your application "
    "for the Site Reliability Engineer position at this time. We encourage you to apply again in the future.",
)

INTERVIEW_INVITE = make_email(
    'Anna Chen <anna@airbnb.com>',
    "Interview invitation - Airbnb",
    "Hi Sai, thanks for applying to the Backend Engineer role at Airbnb! "
    "We'd like to schedule your interview with the team. Please share your availability for a 45-minute phone screen next week.",
)

INTERVIEW_ASSESSMENT = make_email(
    'HackerRank <no-reply@hackerrankforwork.com>',
    "Coding challenge from Databricks",
    "Databricks has invited you to complete an online assessment for the Software Engineer position. "
    "You have 7 days to complete this coding challenge.",
)

OFFER_EMAIL = make_email(
    'Maria Lopez <maria@vercel.com>',
    "Congratulations! Your offer from Vercel",
    "Hi Sai, congratulations! We are pleased to extend an offer for the Frontend Engineer position at Vercel. "
    "Your offer letter is attached. Please review and let us know if you have questions.",
)

NOISE_JOB_ALERT = make_email(
    'LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>',
    "30 new jobs for software engineer",
    "Jobs you may be interested in: Software Engineer at Acme...",
)

NOISE_NEWSLETTER = make_email(
    'Indeed <alert@indeed.com>',
    "Job alert: python developer jobs in San Francisco",
    "New jobs posted this week. Apply now to these top jobs...",
)

NOISE_PERSONAL = make_email(
    'Mom <mom@gmail.com>',
    "Dinner sunday?",
    "Are you coming to dinner on sunday? Let me know.",
)

NOISE_RECEIPT = make_email(
    'Amazon <auto-confirm@amazon.com>',
    "Your Amazon.com order has shipped",
    "Your package with USB cables is on the way.",
)

LINKEDIN_REAL_APPLICATION = make_email(
    'LinkedIn <jobs-noreply@linkedin.com>',
    "Your application was sent to Anthropic",
    "Your application was sent to Anthropic. Software Engineer, Infrastructure. San Francisco, CA.",
)

AMBIGUOUS_RECRUITER = make_email(
    'Jordan Smith <jordan.smith@acmecorp.com>',
    "Following up",
    "Hi Sai, just checking in on our conversation from last week. "
    "The team enjoyed meeting you and we should have next steps for the platform role soon.",
)

SAME_THREAD_REJECT = make_email(
    'Stripe <no-reply@greenhouse.io>',
    "Re: Thank you for applying to Stripe!",
    "Hi Sai, thank you again for your interest in the Backend Engineer position at Stripe. "
    "Unfortunately, we have decided not to move forward with your application at this time.",
    thread_id=APPLIED_GREENHOUSE.thread_id,
)
