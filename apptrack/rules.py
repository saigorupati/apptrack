"""Rule-based classification. High-precision patterns only — anything the rules
aren't sure about returns None and falls through to the LLM.
"""

from __future__ import annotations

import re
from email.utils import parseaddr

from .models import Email, Status, Verdict
from .prefilter import ats_source

# --- status patterns (checked in priority order) -----------------------------

_REJECT_RE = re.compile(
    r"(?:not (?:be )?moving forward|move forward with other|other candidates"
    r"|pursue other (?:candidates|applicants)|decided to (?:go|proceed) (?:in another|with (?:an)?other)"
    r"|position has been filled|no longer under consideration|not (?:been )?selected"
    r"|unable to (?:move|proceed|offer)|will not be (?:progressing|proceeding)"
    r"|unfortunately[^.]{0,120}(?:application|candidacy|position|role|time|qualifications)"
    r"|regret to inform|wish you (?:the best|success|luck)[^.]{0,60}(?:search|endeavou?rs))",
    re.IGNORECASE,
)

_OFFER_RE = re.compile(
    r"(?:offer letter|pleased to (?:extend|offer)|extend (?:you )?an offer"
    r"|congratulations[^.]{0,80}offer|your offer (?:from|details|package))",
    re.IGNORECASE,
)

_INTERVIEW_RE = re.compile(
    r"(?:schedule (?:your|an|a) (?:interview|call|chat|conversation)"
    r"|interview (?:invitation|request|confirmed|scheduled|availability)"
    r"|invite you to (?:interview|a call|chat|speak|meet)"
    r"|would love to (?:chat|speak|talk|meet)|set up (?:a|some) time"
    r"|phone screen|technical (?:interview|screen)|onsite|on-site"
    r"|take[- ]home (?:assessment|assignment|challenge)|coding challenge|online assessment"
    r"|next (?:step|round)[^.]{0,80}(?:interview|call|assessment)"
    r"|your interview with)",
    re.IGNORECASE,
)

_APPLIED_RE = re.compile(
    r"(?:thank (?:you|s) for (?:applying|your application|your interest in (?:applying|the))"
    r"|(?:we(?:'ve| have)? )?received your application|application (?:was |has been )?(?:received|submitted)"
    r"|your application (?:to|for|was sent)|successfully (?:applied|submitted)"
    r"|application confirmation|confirm(?:ing|ation of)? (?:receipt of )?your application)",
    re.IGNORECASE,
)

# --- company / role extraction ----------------------------------------------

# "Your application to Stripe", "your application for the SWE role at Figma"
_COMPANY_PATTERNS = [
    re.compile(r"application (?:to|at|with) (?P<c>[A-Z][\w.&'\- ]{1,40}?)(?:\s+(?:for|was|has|is|-|–|\|)|[.,!\n]|$)"),
    re.compile(r"(?:position|role|opportunity|interview|career(?:s)?) (?:at|with) (?P<c>[A-Z][\w.&'\- ]{1,40}?)(?:[.,!\n]|\s+(?:for|as|-|–|\|)|$)"),
    re.compile(r"joining (?P<c>[A-Z][\w.&'\- ]{1,40}?)(?:[.,!\n]|$)"),
]

_ROLE_PATTERNS = [
    re.compile(r"application (?:for|to) (?:the )?(?P<r>[A-Z][\w+#/().,'\- ]{2,60}?) (?:position|role|opening|opportunity)", re.IGNORECASE),
    re.compile(r"(?:for the|for our|the) (?P<r>[A-Z][\w+#/().'\- ]{2,60}?) (?:position|role|opening) at", re.IGNORECASE),
    re.compile(r"(?:position|role):\s*(?P<r>[^\n,|–-]{3,60})", re.IGNORECASE),
]

# ATS notification mailboxes often carry the company in the display name:
# "Figma <no-reply@ashbyhq.com>", "Careers at Ramp <notifications@lever.co>"
_DISPLAY_CLEAN_RE = re.compile(
    r"^(?:the\s+)?(.*?)\s*(?:careers?|recruiting|recruitment|talent(?:\s+(?:acquisition|team))?"
    r"|hiring(?:\s+team)?|jobs?|team|hr|people(?:\s+ops)?|notifications?|no[- ]?reply)?\s*$",
    re.IGNORECASE,
)
_GENERIC_DISPLAY_RE = re.compile(
    r"^(?:no[- _]?reply|do[- _]?not[- _]?reply|notifications?|careers?|jobs?|recruiting"
    r"|talent|hr|info|hello|team|admin|mail(?:er)?|greenhouse|lever|workday|ashby|icims"
    r"|smartrecruiters|taleo|jobvite|linkedin)$",
    re.IGNORECASE,
)


# Lowercase filler words that signal the company-name match ran past the name
# ("Flatiron a few months ago" → "Flatiron").
_COMPANY_STOPWORDS = {
    "a", "an", "the", "your", "our", "few", "months", "month", "weeks", "week",
    "days", "ago", "at", "on", "in", "is", "was", "has", "have", "will", "to",
    "via", "from", "regarding", "about",
}
_NAME_CONNECTORS = {"of", "and", "&", "for", "de", "la"}


def _clean_company(raw: str) -> str:
    c = raw.strip().strip(".,!|–-—:;\"' ")
    c = re.sub(r"\s+", " ", c)
    c = re.sub(r"\s*@.*$", "", c)  # "McGraw Hill @ icims" → "McGraw Hill"
    # Keep the leading run of name-like tokens; stop at filler words. A
    # possessive ends the name ("Notion's Software Engineer" → "Notion").
    tokens = c.split(" ")
    kept: list[str] = []
    for i, tok in enumerate(tokens):
        poss = re.match(r"(.+)['’‘`´ʼ][sS]$", tok)
        if poss:
            kept.append(poss.group(1))
            break
        low = tok.lower().strip(".,")
        if i > 0 and (low in _COMPANY_STOPWORDS or (tok[:1].islower() and low not in _NAME_CONNECTORS)):
            break
        kept.append(tok)
    c = " ".join(kept).strip(".,!|–-—:;\"' ")
    # Drop trailing boilerplate words that leak into the match
    c = re.sub(r"\s+(?:team|careers?|recruiting|talent|hiring|hr)$", "", c, flags=re.IGNORECASE)
    if len(c) < 2 or len(c) > 40:
        return ""
    if _GENERIC_DISPLAY_RE.match(c):
        return ""
    return c


_ROLE_STOPLIST = {"current", "the", "this", "that", "time", "your", "our", "new"}


def _clean_role(raw: str) -> str:
    r = raw.strip().strip(".,!|–-—:;\"' ")
    r = re.sub(r"\s+", " ", r)
    r = re.sub(r"^(?:the|our|your|a|an|this) ", "", r, flags=re.IGNORECASE)
    if len(r) <= 2 or len(r) > 60 or r.lower() in _ROLE_STOPLIST:
        return ""
    if not re.search(r"[A-Za-z]{2}", r):
        return ""
    # A real job title never contains verbs like "apply" — that's a sentence
    # fragment the pattern over-matched ("time to apply to the Software Engineer").
    if re.search(r"\b(?:apply|applying|application|applied)\b", r, re.IGNORECASE):
        return ""
    return r


def extract_company(email: Email) -> str:
    text = f"{email.subject}\n{email.body[:2000]}"
    for pat in _COMPANY_PATTERNS:
        m = pat.search(text)
        if m:
            c = _clean_company(m.group("c"))
            if c:
                return c

    # Display name of the sender ("Figma" <no-reply@ashbyhq.com>)
    display, _addr = parseaddr(email.sender)
    if display:
        m = _DISPLAY_CLEAN_RE.match(display)
        cand = _clean_company(m.group(1) if m else display)
        if cand:
            return cand

    # Company from its own domain (careers@stripe.com) — but never from an ATS domain
    if not ats_source(email.sender_domain):
        base = email.sender_domain.split(".")[0]
        if base and not _GENERIC_DISPLAY_RE.match(base) and base not in ("gmail", "googlemail", "outlook", "yahoo", "hotmail", "icloud", "proton", "protonmail"):
            return base.capitalize()
    return ""


def extract_role(email: Email) -> str:
    text = f"{email.subject}\n{email.body[:2000]}"
    for pat in _ROLE_PATTERNS:
        m = pat.search(text)
        if m:
            r = _clean_role(m.group("r"))
            if r:
                return r
    return ""


def classify(email: Email) -> Verdict | None:
    """Return a Verdict when rules are confident, else None (→ LLM fallback)."""
    text = f"{email.subject}\n{email.body[:6000]}"
    source = ats_source(email.sender_domain) or ""

    status: Status | None = None
    if _REJECT_RE.search(text):
        status = Status.REJECTED
    elif _OFFER_RE.search(text):
        status = Status.OFFER
    elif _INTERVIEW_RE.search(text):
        status = Status.INTERVIEW
    elif _APPLIED_RE.search(text):
        status = Status.APPLIED

    if status is None:
        return None

    company = extract_company(email)
    role = extract_role(email)

    # Rules are only "confident" when we at least know the company; otherwise
    # let the LLM read the full email.
    if not company:
        return None

    return Verdict(
        is_job_related=True,
        status=status,
        company=company,
        role=role,
        confidence=0.9 if source else 0.75,
        source="rules",
    )
