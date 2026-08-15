"""Cheap local filter: decide whether an email is even a *candidate* for
job-application classification. Everything failing this filter is ignored
outright and never stored or sent to the LLM.
"""

from __future__ import annotations

import re

from .models import Email

# Known ATS / recruiting-platform sender domains (matched as suffix).
ATS_DOMAINS = {
    "greenhouse.io": "Greenhouse",
    "greenhouse-mail.io": "Greenhouse",
    "lever.co": "Lever",
    "hire.lever.co": "Lever",
    "ashbyhq.com": "Ashby",
    "myworkday.com": "Workday",
    "workday.com": "Workday",
    "myworkdayjobs.com": "Workday",
    "icims.com": "iCIMS",
    "talent.icims.com": "iCIMS",
    "smartrecruiters.com": "SmartRecruiters",
    "taleo.net": "Taleo",
    "jobvite.com": "Jobvite",
    "bamboohr.com": "BambooHR",
    "breezy.hr": "Breezy",
    "workablemail.com": "Workable",
    "workable.com": "Workable",
    "recruitee.com": "Recruitee",
    "teamtailor.com": "Teamtailor",
    "jazz.co": "JazzHR",
    "applytojob.com": "JazzHR",
    "successfactors.com": "SuccessFactors",
    "avature.net": "Avature",
    "eightfold.ai": "Eightfold",
    "phenompeople.com": "Phenom",
    "wellfound.com": "Wellfound",
    "hired.com": "Hired",
    "otta.com": "Otta",
    "dover.com": "Dover",
    "rippling.com": "Rippling ATS",
    "gem.com": "Gem",
    "paylocity.com": "Paylocity",
    "adp.com": "ADP",
    "oraclecloud.com": "Oracle Recruiting",
    "pinpointhq.com": "Pinpoint",
    "hirebridgemail.com": "Hirebridge",
    "trakstar.com": "Trakstar",
    "clearcompany.com": "ClearCompany",
    "ultipro.com": "UKG",
    "hackerrankforwork.com": "HackerRank",
    "codesignal.com": "CodeSignal",
    "karat.com": "Karat",
    "goodtime.io": "GoodTime",
    "calendly.com": "Calendly",
    "modernloop.io": "ModernLoop",
}

# Phrases (subject or body) that mark an email as a job-application candidate.
_PHRASES = [
    r"your application",
    r"thank you for applying",
    r"thanks for applying",
    r"we received your application",
    r"application (?:was )?(?:received|submitted|complete)",
    r"application to",
    r"application for",
    r"application status",
    r"applied to",
    r"interview",
    r"phone screen",
    r"next steps? in (?:the|our) (?:hiring|recruit|interview)",
    r"hiring (?:team|manager|process)",
    r"recruiter",
    r"recruiting team",
    r"talent (?:acquisition|team)",
    r"move forward with (?:other|another)",
    r"not (?:be )?moving forward",
    r"other candidates",
    r"pursue other candidates",
    r"unfortunately",
    r"position has been filled",
    r"no longer under consideration",
    r"offer letter",
    r"pleased to (?:extend|offer)",
    r"job offer",
    r"take[- ]home (?:assessment|assignment|challenge)",
    r"coding (?:challenge|assessment|exercise)",
    r"online assessment",
    r"background check",
    r"candidate (?:profile|portal|experience)",
    r"we(?:'| a)re excited to (?:invite|move)",
    r"role at",
    r"position at",
    r"opportunity at",
    r"(?:the|our|this) [\w/+#&,.'\- ]{2,40}(?:role|position|opening)\b",
    r"next steps",
]
_PHRASE_RE = re.compile("|".join(f"(?:{p})" for p in _PHRASES), re.IGNORECASE)

# Obvious noise even if a phrase matches (job boards blasting listings, newsletters,
# transactional mail whose subject makes the non-job nature obvious).
_NOISE_RE = re.compile(
    r"(?:job alert|jobs? for you|^new jobs?\b|new jobs? (?:posted|match)|recommended jobs?"
    r"|jobs? you may|apply now to these|daily job|weekly job|job digest"
    r"|hiring now[:!]|top jobs|unsubscribe from job alerts"
    r"|package|deliver(?:y|ed)|your order|shipped|pick ?up"
    r"|sign(?:ing)? up|verify your|confirm your email|password|receipt|invoice"
    r"|subscription|renewal)",
    re.IGNORECASE,
)

# "Application" language that is about insurance/benefits/finance, not jobs.
_NONJOB_APPLICATION_RE = re.compile(
    r"(?:health (?:plan|insurance|coverage)|insurance|medicare|medicaid|premium"
    r"|enrollment period|deductible|beneficiar|loan|credit card|mortgage|visa application"
    r"|rental application|lease|apartment)",
    re.IGNORECASE,
)
_JOB_CONTEXT_RE = re.compile(
    r"(?:position|\brole\b|job|career|opening|candidate|recruit|hiring|talent"
    r"|engineer|developer|resume|\bcv\b|interview)",
    re.IGNORECASE,
)

# LinkedIn and Wellfound send both real application updates and pure noise
# (alerts, digests); allow only the real ones from these domains.
_GATED_DOMAINS = ("linkedin.com", "wellfound.com", "hired.com", "otta.com")
_GATED_OK_RE = re.compile(
    r"(?:your application (?:was|to|for)|application (?:sent|viewed)|viewed your application)",
    re.IGNORECASE,
)


def ats_source(domain: str) -> str | None:
    """Return the ATS name if `domain` is (a subdomain of) a known ATS domain."""
    d = domain.lower()
    for suffix, name in ATS_DOMAINS.items():
        if d == suffix or d.endswith("." + suffix):
            return name
    return None


def is_candidate(email: Email) -> bool:
    """True if this email might be about one of the user's job applications."""
    text = f"{email.subject}\n{email.body[:4000]}"

    domain = email.sender_domain.lower()
    if any(domain == g or domain.endswith("." + g) for g in _GATED_DOMAINS):
        return bool(_GATED_OK_RE.search(text))

    if _NOISE_RE.search(email.subject):
        return False

    if ats_source(domain):
        return True

    if not _PHRASE_RE.search(text):
        return False

    # "Application" language without any job context (insurance, leases, loans…)
    if _NONJOB_APPLICATION_RE.search(text) and not _JOB_CONTEXT_RE.search(text):
        return False

    return True
