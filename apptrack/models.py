"""Shared data types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Status(str, Enum):
    APPLIED = "APPLIED"
    INTERVIEW = "INTERVIEW"
    OFFER = "OFFER"
    REJECTED = "REJECTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"

    @property
    def rank(self) -> int:
        """Precedence for status advancement. REJECTED is terminal, handled separately."""
        return {"APPLIED": 1, "NEEDS_REVIEW": 1, "INTERVIEW": 2, "OFFER": 3, "REJECTED": 4}[self.value]


@dataclass
class Email:
    """A parsed inbox message."""

    uid: int
    message_id: str
    thread_id: str  # Gmail X-GM-THRID
    sender: str  # full From header
    sender_email: str  # bare address, lowercased
    sender_domain: str
    subject: str
    body: str  # text/plain best-effort, truncated
    date: datetime


@dataclass
class Verdict:
    """Classification result for one email."""

    is_job_related: bool
    status: Status | None = None
    company: str = ""
    role: str = ""
    confidence: float = 0.0
    source: str = "rules"  # "rules" | "llm" | "needs_review"


@dataclass
class Application:
    """One tracked job application (SQLite row)."""

    id: int | None
    company: str
    role: str
    status: Status
    applied_at: datetime | None
    last_update: datetime
    last_subject: str
    ats_source: str
    thread_ids: list[str] = field(default_factory=list)
    notes: str = ""
