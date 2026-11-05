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
    r"(?:not (?:to |be )?mov(?:e|ing) forward|move(?:d)? forward with (?:other|another|a candidate)"
    r"|other candidates|made the (?:tough|difficult) (?:choice|decision) not to"
    r"|pursue other (?:candidates|applicants)|decided to (?:go|proceed) (?:in another|with (?:an)?other)"
    r"|(?:position|role) has (?:now )?been (?:filled|closed)|(?:position|role) (?:is|has been) (?:closed|on hold)"
    r"|no longer (?:under consideration|available)|not (?:been )?selected|completed hiring for"
    r"|unable to (?:move|proceed|offer)|will not be (?:progressing|proceeding)"
    r"|(?:do not|don't) (?:have a fit|think your skills|see a (?:strong )?match)"
    r"|unfortunately[^.]{0,120}(?:application|candidacy|position|role|time|qualifications|fit)"
    r"|regret to (?:inform|share)|wish you (?:the best|success|luck)[^.]{0,60}(?:search|endeavou?rs))",
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
    r"|phone screen|technical (?:interview|screen)|on-?site interview"
    r"|take[- ]home (?:assessment|assignment|challenge)|coding challenge|online assessment"
    r"|(?:technical|coding) assessment|assessments? invitation"
    r"|next (?:step|round)[^.]{0,80}(?:interview|call|assessment)"
    r"|your interview with)",
    re.IGNORECASE,
)
# Dropped on purpose: "would love to chat", "set up a time", bare "onsite" —
# they matched cold recruiter outreach ("a part-time, on-site role", "open to a
# quick 15 minute chat?") and created INTERVIEW rows for jobs never applied to.
# Those emails now fall through to the LLM, which is told to ignore outreach.

# Rejection/interview language that's only hypothetical: "If you are not
# selected for this position…", "If you're shortlisted, next steps will consist
# of assessments and interviews". Application acknowledgements are full of it.
_CONDITIONAL_RE = re.compile(
    r"\b(?:if|should|whether|in the event|in case|once|when)\b[^.!?\n]*$", re.IGNORECASE
)


def _firm_search(pattern: re.Pattern, text: str) -> bool:
    """True if `pattern` matches somewhere not inside a conditional clause."""
    for m in pattern.finditer(text):
        sentence_start = max(text.rfind(".", 0, m.start()), text.rfind("\n", 0, m.start()),
                             text.rfind("!", 0, m.start()), text.rfind("?", 0, m.start()))
        if not _CONDITIONAL_RE.search(text[sentence_start + 1 : m.start()]):
            return True
    return False

_APPLIED_RE = re.compile(
    r"(?:thank(?:s| you) for (?:applying|your application|your interest in (?:applying|the))"
    r"|(?:we(?:'ve| have)? )?received your application|application (?:was |has been )?(?:received|submitted)"
    r"|your application (?:to|for|was sent)|successfully (?:applied|submitted)"
    r"|application (?:sent|confirmation)|copy of application to"
    r"|confirm(?:ing|ation of)? (?:receipt of )?your application)",
    re.IGNORECASE,
)

# --- company / role extraction ----------------------------------------------

# "Your application to Stripe", "your application for the SWE role at Figma"
_COMPANY_PATTERNS = [
    re.compile(r"(?i:application (?:to|at|with)) (?P<c>[A-Z][\w.&'\- ]{1,40}?)(?:\s+(?:for|was|has|is|-|–|\|)|[.,!\n]|$)"),
    # "Thanks for applying to SpaceXSI!", "Thank you for applying at CJ"
    re.compile(r"(?i:thank(?:s| you) for applying (?:to|at|with)) (?P<c>[A-Z0-9][\w.&'\- ]{1,40}?)(?:\s+(?:for|-|–|\|)|[.,!\n]|$)"),
    re.compile(r"(?i:(?:position|role|opportunity|interview|careers?) (?:at|with)) (?P<c>[A-Z][\w.&'\- ]{1,40}?)(?:[.,!\n]|\s+(?:for|as|-|–|\|)|$)"),
    re.compile(r"(?i:joining) (?P<c>[A-Z][\w.&'\- ]{1,40}?)(?:[.,!\n]|$)"),
]

_ROLE_PATTERNS = [
    re.compile(r"application (?:for|to) (?:the )?(?P<r>[A-Z][\w+#/().,'\- ]{2,60}?) (?:position|role|opening|opportunity)", re.IGNORECASE),
    re.compile(r"(?:for the|for our|the) (?P<r>[A-Z][\w+#/().'\- ]{2,60}?) (?:position|role|opening) at", re.IGNORECASE),
    re.compile(r"(?:position|role):\s*(?P<r>[^\n,|–-]{3,60})", re.IGNORECASE),
]

# ATS notification mailboxes often carry the company in the display name:
# "Figma <no-reply@ashbyhq.com>", "Careers at Ramp <notifications@lever.co>"
_DISPLAY_CLEAN_RE = re.compile(
    r"^(?:the\s+)?(?:(?:careers?|jobs?|recruiting)(?:\s+(?:at|@))?\s+)?(.*?)"
    r"(?:\s+(?:careers?|recruiting|recruitment|talent|acquisition|hiring|jobs?|team|hr|people|ops"
    r"|notifications?|no[- ]?reply|assessments?|email)|(?<=[a-z])Email)*\s*$",
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
    # A connector can't end a name ("role at Abridge and for taking…" → "Abridge").
    while kept and kept[-1].lower() in _NAME_CONNECTORS:
        kept.pop()
    c = " ".join(kept).strip(".,!|–-—:;\"' ")
    # Drop trailing boilerplate words that leak into the match
    c = re.sub(r"\s+(?:team|careers?|recruiting|talent|hiring|hr)$", "", c, flags=re.IGNORECASE)
    if len(c) < 2 or len(c) > 40:
        return ""
    if _GENERIC_DISPLAY_RE.match(c):
        return ""
    # "Thanks for applying to Software Engineer II, Backend" names the role, not the company.
    if _TITLE_WORD_RE.search(c):
        return ""
    return c


_TITLE_WORD_RE = re.compile(
    r"\b(?:engineers?|developer|swe|sde|scientist|analyst|designer|architect|intern(?:ship)?"
    r"|manager|associate|specialist|technician|administrator|staff|senior|junior|new grad)\b",
    re.IGNORECASE,
)


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
    if _firm_search(_REJECT_RE, text):
        status = Status.REJECTED
    elif _OFFER_RE.search(text):
        status = Status.OFFER
    elif _APPLIED_RE.search(email.subject):
        # An acknowledgement subject ("We've Received Your Application") wins
        # over interview words in its body, which describe the process ahead.
        status = Status.APPLIED
    elif _firm_search(_INTERVIEW_RE, text):
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
