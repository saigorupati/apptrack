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
    "coderpad.io": "CoderPad",
}

# Phrases specific enough to mark a candidate wherever they appear (subject or body).
_STRONG_PHRASES = [
    r"your application",
    r"thanks? (?:you )?for applying",
    r"received your application",
    r"application (?:was |has been )?(?:received|submitted|complete)",
    r"application (?:to|for|with)\b",
    r"application status",
    r"your candidacy",
    r"reviewed your (?:resume|application|materials|profile|background)",
    r"(?:current|our) hiring needs",
    r"next steps? in (?:the|our) (?:hiring|recruit|interview)",
    r"next steps? (?:for|on|regarding|with) (?:the|your) [\w/+#&,.'\- ]{2,40}(?:role|position|application)",
    r"enjoyed (?:meeting|speaking|talking|chatting) (?:with )?you",
    r"move(?:d)? forward with (?:other|another|a candidate)",
    r"not (?:to |be )?mov(?:e|ing) forward",
    r"pursue other (?:candidates|applicants)",
    r"position has been filled",
    r"no longer under consideration",
    r"offer letter",
    r"pleased to (?:extend|offer)",
    r"take[- ]home (?:assessment|assignment|challenge)",
    r"(?:coding|technical|online) (?:assessment|exercise)",
    r"schedule (?:your|an|a) interview",
    r"interview (?:invitation|request|confirmed|scheduled|availability)",
]

# Generic words that newsletters, digests and marketing mail use all the time
# ("interview", "unfortunately", "role at"…). They only count in the SUBJECT —
# in a body they matched sports newsletters, NYT, Reddit digests and job-search
# marketing, and every one of those cost an LLM call.
_WEAK_PHRASES = [
    r"interview",
    r"phone screen",
    r"hiring (?:team|manager|process)",
    r"recruit(?:er|ing team)",
    r"talent (?:acquisition|team)",
    r"other candidates",
    r"unfortunately",
    r"background check",
    r"candidate (?:profile|portal|experience)",
    r"we(?:'| a)re excited to (?:invite|move)",
    r"(?:role|position|opportunity) (?:at|with)\b",
    r"(?:the|our|this) [\w/+#&,.'\- ]{2,40}(?:role|position|opening)\b",
    r"next steps?",
    r"\b(?:role|position|candidacy)\b",
    r"(?:job|your|the) application",
    r"application (?:update|status|received|confirmation|follow[- ]?up)",
    r"(?:technical|coding|online|skills?|hiring) assessments?",
    r"assessments? (?:invitation|invite|at|for)",
]
_STRONG_RE = re.compile("|".join(f"(?:{p})" for p in _STRONG_PHRASES), re.IGNORECASE)
_WEAK_RE = re.compile("|".join(f"(?:{p})" for p in _WEAK_PHRASES), re.IGNORECASE)

# Obvious noise even if a phrase matches (job boards blasting listings, newsletters,
# transactional mail whose subject makes the non-job nature obvious).
_NOISE_RE = re.compile(
    r"(?:job alert|jobs? for you|^new jobs?\b|new jobs? (?:posted|match)|recommended jobs?"
    r"|jobs? you may|apply now to these|daily job|weekly job|job digest|weekly digest|newsletter"
    r"|hiring now[:!]|top jobs|unsubscribe from job alerts|dream job"
    r"|package|deliver(?:y|ed)|your order|shipped|pick ?up"
    r"|sign(?:ing)? up|verify your|confirm your (?:email|identity)|take action to confirm"
    r"|\bpassword\b|\bOTP\b|one[- ]time (?:pass|code)|verification code|security code"
    r"|receipt|invoice|subscription|renewal"
    # Reminders about applications never submitted aren't applications.
    r"|(?:finish|complete|continue|resume) your (?:[\w&,'\- ]{0,60} )?application"
    r"|application is (?:still )?incomplete)",
    re.IGNORECASE,
)

# Unsolicited outreach about a role the user never applied to ("saw your
# background… I wanted to invite you to apply"). Ignored unless the email also
# references an actual application.
_OUTREACH_RE = re.compile(
    r"(?:(?:saw|came across|stumbled (?:up)?on|found|noticed) your (?:background|profile|resume|experience)"
    r"|invit(?:e|ation)(?: you)? to apply|encourage you to apply|could be a (?:strong|good|great) (?:match|fit))",
    re.IGNORECASE,
)
_APPLICATION_REF_RE = re.compile(
    r"(?:your (?:recent )?application|thanks? (?:you )?for applying|you applied|received your application)",
    re.IGNORECASE,
)

# Job-search services and media that write *about* applying and interviewing.
# Matched as domain suffix; nothing from these is ever the user's own application.
_BLOCKED_DOMAINS = (
    "scale.jobs", "greenhouse-jobs.com", "beehiiv.com", "substack.com", "redditmail.com",
    "theathletic.com", "nytimes.com", "leetcode.com", "hackerrankmail.com",
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

# Job boards send both real application updates and pure noise (alerts, digests,
# "could be a strong match for this role" recommendations); allow only the real
# ones from these domains. Indeed's match.indeed.com recommendations alone
# created ~27 phantom applications before Indeed was gated.
_GATED_DOMAINS = (
    "linkedin.com", "wellfound.com", "hired.com", "otta.com", "indeed.com", "ycombinator.com",
)
_GATED_OK_RE = re.compile(
    r"(?:your application (?:was|to|for|has)|application (?:sent|viewed|submitted)"
    r"|viewed your application|indeed application:)",
    re.IGNORECASE,
)


def ats_source(domain: str) -> str | None:
    """Return the ATS name if `domain` is (a subdomain of) a known ATS domain."""
    d = domain.lower()
    for suffix, name in ATS_DOMAINS.items():
        if d == suffix or d.endswith("." + suffix):
            return name
    return None


def _domain_in(domain: str, suffixes: tuple[str, ...]) -> bool:
    return any(domain == s or domain.endswith("." + s) for s in suffixes)


def is_candidate(email: Email) -> bool:
    """True if this email might be about one of the user's job applications."""
    text = f"{email.subject}\n{email.body[:4000]}"

    domain = email.sender_domain.lower()
    if _domain_in(domain, _BLOCKED_DOMAINS):
        return False
    if _domain_in(domain, _GATED_DOMAINS):
        return bool(_GATED_OK_RE.search(text))

    if _NOISE_RE.search(email.subject):
        return False

    if _OUTREACH_RE.search(text) and not _APPLICATION_REF_RE.search(text):
        return False

    if ats_source(domain):
        return True

    if not (_STRONG_RE.search(text) or _WEAK_RE.search(email.subject)):
        return False

    # "Application" language without any job context (insurance, leases, loans…)
    if _NONJOB_APPLICATION_RE.search(text) and not _JOB_CONTEXT_RE.search(text):
        return False

    return True
