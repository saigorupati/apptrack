"""LLM fallback classifier — llama.cpp's OpenAI-compatible /v1/chat/completions.

Only called for emails that passed the prefilter but that the rules engine
couldn't classify confidently. Returns a Verdict, or a NEEDS_REVIEW verdict if
the model output can't be parsed after one retry.
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from .config import Config
from .models import Email, Status, Verdict

log = logging.getLogger(__name__)

_SYSTEM = """You classify emails for a personal job-application tracker.
The user only cares about emails concerning THEIR OWN job applications.

Respond with ONLY a JSON object, no prose, matching exactly:
{
  "is_job_related": true/false,
  "status": "APPLIED" | "REJECTED" | "INTERVIEW" | "OFFER" | null,
  "company": "company name or empty string",
  "role": "job title or empty string",
  "confidence": 0.0-1.0
}

Rules:
- is_job_related=false for: job-listing newsletters, job alerts, promotions,
  networking spam, anything not about an application the user submitted.
- APPLIED: confirmation an application was received/submitted.
- REJECTED: the user is not moving forward for this role.
- INTERVIEW: scheduling/invitation/assessment/next-round for the user.
- OFFER: a job offer is being extended.
- status=null if job-related but none of the above fit.
- company: the hiring company (never the ATS platform like Greenhouse/Lever/Workday).
"""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

_VALID_STATUS = {s.value for s in (Status.APPLIED, Status.REJECTED, Status.INTERVIEW, Status.OFFER)}


def _build_user_prompt(email: Email) -> str:
    body = email.body[:3500]  # keep well inside the 8k ctx with prompt + headers
    return (
        f"From: {email.sender}\n"
        f"Subject: {email.subject}\n"
        f"Date: {email.date.isoformat()}\n\n"
        f"{body}"
    )


def _parse(raw: str) -> Verdict | None:
    m = _JSON_RE.search(raw)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "is_job_related" not in data:
        return None

    if not data.get("is_job_related"):
        return Verdict(is_job_related=False, source="llm")

    status_raw = data.get("status")
    status = Status(status_raw) if status_raw in _VALID_STATUS else None
    company = str(data.get("company") or "").strip()[:60]
    role = str(data.get("role") or "").strip()[:80]
    # Models sometimes verbalize absence instead of returning an empty string.
    if re.fullmatch(r"(?:role )?(?:title )?(?:not specified|not provided|unknown|unspecified|n/?a|none|null|-+)", role, re.IGNORECASE):
        role = ""
    if re.fullmatch(r"(?:not specified|not provided|unknown|unspecified|n/?a|none|null|-+)", company, re.IGNORECASE):
        company = ""
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5

    if status is None or not company:
        # Job-related but under-specified → surface for human review
        return Verdict(
            is_job_related=True, status=Status.NEEDS_REVIEW,
            company=company, role=role, confidence=confidence, source="llm",
        )
    return Verdict(
        is_job_related=True, status=status,
        company=company, role=role, confidence=confidence, source="llm",
    )


class LlmClassifier:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._client = httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0))

    def classify(self, email: Email) -> Verdict:
        if not self.cfg.llm_enabled:
            return Verdict(is_job_related=True, status=Status.NEEDS_REVIEW, source="needs_review")

        payload = {
            "model": self.cfg.llm_model,
            "temperature": 0.1,
            "max_tokens": 200,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": _build_user_prompt(email)},
            ],
        }
        for attempt in (1, 2):
            try:
                resp = self._client.post(f"{self.cfg.llm_base_url}/chat/completions", json=payload)
                resp.raise_for_status()
                raw = resp.json()["choices"][0]["message"]["content"]
            except Exception as exc:  # network / HTTP / schema
                log.warning("LLM call failed (attempt %d) for uid=%s: %s", attempt, email.uid, exc)
                continue
            verdict = _parse(raw)
            if verdict is not None:
                return verdict
            log.warning("LLM returned unparseable output (attempt %d) for uid=%s: %.200s", attempt, email.uid, raw)

        return Verdict(is_job_related=True, status=Status.NEEDS_REVIEW, source="needs_review",
                       company="", role="", confidence=0.0)

    def close(self) -> None:
        self._client.close()
