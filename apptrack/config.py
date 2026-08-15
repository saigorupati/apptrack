"""Environment-driven configuration for AppTrack."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str | None = None, required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and not val:
        raise SystemExit(f"Missing required environment variable: {name}")
    return val or ""


@dataclass
class Config:
    gmail_address: str
    gmail_app_password: str
    sheet_id: str
    google_sa_json: Path
    llm_base_url: str
    llm_model: str
    followup_days: int
    digest_to: str
    sync_hour: int
    backfill_days: int
    db_path: Path
    imap_host: str = "imap.gmail.com"
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    llm_enabled: bool = True

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            gmail_address=_env("GMAIL_ADDRESS", required=True),
            gmail_app_password=_env("GMAIL_APP_PASSWORD", required=True),
            sheet_id=_env("SHEET_ID", required=True),
            google_sa_json=Path(_env("GOOGLE_SA_JSON", "/secrets/service-account.json")),
            llm_base_url=_env("LLM_BASE_URL", "http://llama:8080/v1").rstrip("/"),
            llm_model=_env("LLM_MODEL", "qwen2.5-7b"),
            followup_days=int(_env("FOLLOWUP_DAYS", "14")),
            digest_to=_env("DIGEST_TO") or _env("GMAIL_ADDRESS", required=True),
            sync_hour=int(_env("SYNC_HOUR", "2")),
            backfill_days=int(_env("BACKFILL_DAYS", "30")),
            db_path=Path(_env("DB_PATH", "/data/apptrack.db")),
            llm_enabled=_env("LLM_ENABLED", "1") not in ("0", "false", "no"),
        )
