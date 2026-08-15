"""SQLite persistence: applications, processed emails, and IMAP sync state."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Application, Status

_SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    company_norm TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    applied_at TEXT,
    last_update TEXT NOT NULL,
    last_subject TEXT NOT NULL DEFAULT '',
    ats_source TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_threads (
    thread_id TEXT PRIMARY KEY,
    app_id INTEGER NOT NULL REFERENCES applications(id)
);
CREATE TABLE IF NOT EXISTS processed_emails (
    uid INTEGER NOT NULL,
    uidvalidity INTEGER NOT NULL,
    message_id TEXT,
    app_id INTEGER,
    verdict TEXT NOT NULL,          -- ignored | classified | needs_review
    processed_at TEXT NOT NULL,
    PRIMARY KEY (uid, uidvalidity)
);
CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    fetched INTEGER DEFAULT 0,
    candidates INTEGER DEFAULT 0,
    classified INTEGER DEFAULT 0,
    llm_calls INTEGER DEFAULT 0,
    new_apps INTEGER DEFAULT 0,
    updated_apps INTEGER DEFAULT 0,
    needs_review INTEGER DEFAULT 0,
    error TEXT
);
"""


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def _parse_dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


_COMPANY_SUFFIXES = (
    ", inc.", ", inc", " inc.", " inc", " llc", " ltd", " corp.", " corp", " co.",
    " gmbh", " labs", " technologies", " technology", " tech", " group",
    " holdings", " services", " financial", " systems", " software", " company",
    ".com", ".io", ".ai",
)


def normalize_company(name: str) -> str:
    n = name.lower().strip()
    if n.startswith("the "):
        n = n[4:]
    changed = True
    while changed:  # "Amazon.com Services LLC" → "amazon"
        changed = False
        for suffix in _COMPANY_SUFFIXES:
            if n.endswith(suffix) and len(n) > len(suffix) + 1:
                n = n[: -len(suffix)].strip()
                changed = True
    return "".join(ch for ch in n if ch.isalnum())


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    # --- sync state ---------------------------------------------------------

    def get_state(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM sync_state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_state(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO sync_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    # --- processed emails ---------------------------------------------------

    def is_processed(self, uid: int, uidvalidity: int) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM processed_emails WHERE uid=? AND uidvalidity=?", (uid, uidvalidity)
            ).fetchone()
            is not None
        )

    def mark_processed(self, uid: int, uidvalidity: int, message_id: str, app_id: int | None, verdict: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO processed_emails(uid,uidvalidity,message_id,app_id,verdict,processed_at) VALUES(?,?,?,?,?,?)",
            (uid, uidvalidity, message_id, app_id, verdict, _iso(datetime.now(timezone.utc))),
        )
        self.conn.commit()

    # --- applications -------------------------------------------------------

    def _row_to_app(self, row: sqlite3.Row) -> Application:
        threads = [
            r["thread_id"]
            for r in self.conn.execute("SELECT thread_id FROM app_threads WHERE app_id=?", (row["id"],))
        ]
        return Application(
            id=row["id"],
            company=row["company"],
            role=row["role"],
            status=Status(row["status"]),
            applied_at=_parse_dt(row["applied_at"]),
            last_update=_parse_dt(row["last_update"]),
            last_subject=row["last_subject"],
            ats_source=row["ats_source"],
            thread_ids=threads,
            notes=row["notes"],
        )

    def all_applications(self) -> list[Application]:
        rows = self.conn.execute(
            "SELECT * FROM applications ORDER BY last_update DESC"
        ).fetchall()
        return [self._row_to_app(r) for r in rows]

    def find_by_thread(self, thread_id: str) -> Application | None:
        row = self.conn.execute(
            "SELECT a.* FROM applications a JOIN app_threads t ON t.app_id=a.id WHERE t.thread_id=?",
            (thread_id,),
        ).fetchone()
        return self._row_to_app(row) if row else None

    def find_by_company(self, company_norm: str) -> list[Application]:
        rows = self.conn.execute(
            "SELECT * FROM applications WHERE company_norm=? ORDER BY last_update DESC", (company_norm,)
        ).fetchall()
        return [self._row_to_app(r) for r in rows]

    def insert_application(self, app: Application) -> int:
        cur = self.conn.execute(
            "INSERT INTO applications(company,company_norm,role,status,applied_at,last_update,last_subject,ats_source,notes,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                app.company,
                normalize_company(app.company),
                app.role,
                app.status.value,
                _iso(app.applied_at),
                _iso(app.last_update),
                app.last_subject,
                app.ats_source,
                app.notes,
                _iso(datetime.now(timezone.utc)),
            ),
        )
        app_id = cur.lastrowid
        for tid in app.thread_ids:
            self.conn.execute(
                "INSERT OR IGNORE INTO app_threads(thread_id,app_id) VALUES(?,?)", (tid, app_id)
            )
        self.conn.commit()
        return app_id

    def update_application(self, app: Application) -> None:
        assert app.id is not None
        self.conn.execute(
            "UPDATE applications SET company=?,company_norm=?,role=?,status=?,applied_at=?,last_update=?,last_subject=?,ats_source=? WHERE id=?",
            (
                app.company,
                normalize_company(app.company),
                app.role,
                app.status.value,
                _iso(app.applied_at),
                _iso(app.last_update),
                app.last_subject,
                app.ats_source,
                app.id,
            ),
        )
        for tid in app.thread_ids:
            self.conn.execute(
                "INSERT OR IGNORE INTO app_threads(thread_id,app_id) VALUES(?,?)", (tid, app.id)
            )
        self.conn.commit()

    def set_notes(self, app_id: int, notes: str) -> None:
        self.conn.execute("UPDATE applications SET notes=? WHERE id=?", (notes, app_id))
        self.conn.commit()

    # --- run log ------------------------------------------------------------

    def start_run(self) -> int:
        cur = self.conn.execute(
            "INSERT INTO run_log(started_at) VALUES(?)", (_iso(datetime.now(timezone.utc)),)
        )
        self.conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, **counts) -> None:
        cols = ", ".join(f"{k}=?" for k in counts)
        self.conn.execute(
            f"UPDATE run_log SET finished_at=?, {cols} WHERE id=?",
            (_iso(datetime.now(timezone.utc)), *counts.values(), run_id),
        )
        self.conn.commit()

    def recent_runs(self, n: int = 14) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM run_log ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()

    def close(self) -> None:
        self.conn.close()
