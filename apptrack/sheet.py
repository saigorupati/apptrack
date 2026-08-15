"""Google Sheet writer (gspread + service account).

Four tabs:
  Dashboard    — headline stats, pipeline breakdown, last-7-days, top follow-ups
  Applications — the full table with a visual progress column
  Follow Up    — filtered view of applications needing a nudge
  Log          — per-run sync history

The sheet is a *view* of SQLite. Manual edits to the Notes column are read back
and persisted before each rewrite, keyed by the hidden app_id column.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import gspread

from .config import Config
from .models import Application, Status
from .store import Store

log = logging.getLogger(__name__)

HEADERS = [
    "Company", "Role", "Progress", "Status", "Applied", "Updated",
    "Silent", "Follow Up", "Latest Email", "Source", "Notes", "app_id",
]

_STATUS_ORDER = {
    Status.NEEDS_REVIEW: 0, Status.OFFER: 1, Status.INTERVIEW: 2,
    Status.APPLIED: 3, Status.REJECTED: 4,
}

# Status palette (color never carries meaning alone — the label is always shown).
# Tints are light backgrounds behind a dark bold label.
_INK = {"red": 0.12, "green": 0.12, "blue": 0.11}
_MUTED = {"red": 0.45, "green": 0.44, "blue": 0.42}
_TILE_BG = {"red": 0.965, "green": 0.963, "blue": 0.955}
_HEADER_BG = {"red": 0.16, "green": 0.16, "blue": 0.15}
_BAND_BG = {"red": 0.975, "green": 0.973, "blue": 0.968}

_STATUS_HEX = {  # for SPARKLINE bars on the Dashboard
    "APPLIED": "#2a78d6",
    "INTERVIEW": "#fab219",
    "OFFER": "#0ca30c",
    "REJECTED": "#d03b3b",
    "NEEDS_REVIEW": "#8a8a86",
}
_STATUS_TINT = {  # cell backgrounds behind the status label
    "APPLIED": {"red": 0.875, "green": 0.925, "blue": 0.985},
    "INTERVIEW": {"red": 0.995, "green": 0.945, "blue": 0.82},
    "OFFER": {"red": 0.855, "green": 0.945, "blue": 0.855},
    "REJECTED": {"red": 0.975, "green": 0.875, "blue": 0.875},
    "NEEDS_REVIEW": {"red": 0.93, "green": 0.93, "blue": 0.92},
}

_PROGRESS = {
    "APPLIED": "● ○ ○",
    "INTERVIEW": "● ● ○",
    "OFFER": "● ● ●",
    "REJECTED": "✕",
    "NEEDS_REVIEW": "· · ·",
}
_STATUS_LABEL = {
    "APPLIED": "Applied",
    "INTERVIEW": "Interview",
    "OFFER": "Offer",
    "REJECTED": "Rejected",
    "NEEDS_REVIEW": "Review",
}

_APP_COL_WIDTHS = [180, 260, 90, 100, 95, 95, 75, 100, 330, 110, 240, 40]
_DASH_COL_WIDTHS = [150, 150, 150, 150, 150, 150, 60]


def _fmt(dt: datetime | None) -> str:
    return dt.astimezone().strftime("%b %d") if dt else ""


def _fmt_full(dt: datetime | None) -> str:
    return dt.astimezone().strftime("%Y-%m-%d %H:%M") if dt else ""


def days_silent(app: Application, now: datetime) -> int:
    ref = app.last_update or app.applied_at
    return (now - ref).days if ref else 0


def needs_followup(app: Application, now: datetime, threshold_days: int) -> bool:
    return app.status == Status.APPLIED and days_silent(app, now) >= threshold_days


class SheetWriter:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        gc = gspread.service_account(filename=str(cfg.google_sa_json))
        self.doc = gc.open_by_key(cfg.sheet_id)

    def _ws(self, title: str, rows: int = 200, cols: int = 12, index: int | None = None):
        try:
            return self.doc.worksheet(title)
        except gspread.WorksheetNotFound:
            return self.doc.add_worksheet(title=title, rows=rows, cols=cols, index=index)

    # --- notes readback -----------------------------------------------------

    def pull_notes(self, store: Store) -> None:
        """Persist manually-edited Notes cells back into SQLite before rewriting."""
        try:
            ws = self.doc.worksheet("Applications")
        except gspread.WorksheetNotFound:
            return
        rows = ws.get_all_values()
        if not rows:
            return
        header = rows[0]
        try:
            idx_notes = header.index("Notes")
            idx_id = header.index("app_id")
        except ValueError:
            return
        for row in rows[1:]:
            if len(row) <= idx_id or not row[idx_id].strip().isdigit():
                continue
            store.set_notes(int(row[idx_id]), row[idx_notes] if len(row) > idx_notes else "")

    # --- write --------------------------------------------------------------

    def write(self, store: Store) -> None:
        now = datetime.now(timezone.utc)
        apps = sorted(
            store.all_applications(),
            key=lambda a: (_STATUS_ORDER[a.status], -(a.last_update or now).timestamp()),
        )

        dash = self._ws("Dashboard", rows=40, cols=7, index=0)
        main = self._ws("Applications", index=1)
        fu = self._ws("Follow Up", rows=100, cols=6, index=2)
        logws = self._ws("Log", rows=100, cols=10, index=3)
        self._drop_default_sheet()

        self._write_dashboard(dash, apps, now)
        self._write_applications(main, apps, now)
        self._write_followup(fu, apps, now)
        self._write_log(logws, store)

        fu_count = sum(1 for a in apps if needs_followup(a, now, self.cfg.followup_days))
        log.info("Sheet updated: %d applications, %d follow-ups", len(apps), fu_count)

    def _drop_default_sheet(self) -> None:
        try:
            ws = self.doc.worksheet("Sheet1")
            if not any(v for row in ws.get_all_values() for v in row):
                self.doc.del_worksheet(ws)
        except gspread.WorksheetNotFound:
            pass

    # --- Dashboard ----------------------------------------------------------

    def _write_dashboard(self, ws, apps: list[Application], now: datetime) -> None:
        n = {s: sum(1 for a in apps if a.status == s) for s in Status}
        total = len(apps)
        active = n[Status.APPLIED] + n[Status.INTERVIEW] + n[Status.OFFER]
        followups = [a for a in apps if needs_followup(a, now, self.cfg.followup_days)]
        responded = sum(
            1 for a in apps
            if a.status != Status.APPLIED
            or (a.applied_at and a.last_update and a.last_update > a.applied_at)
        )
        resp_rate = f"{round(100 * responded / total)}%" if total else "—"

        week_ago = now - timedelta(days=7)
        sent_7d = sum(1 for a in apps if a.applied_at and a.applied_at >= week_ago)
        resp_7d = sum(
            1 for a in apps
            if a.last_update and a.last_update >= week_ago
            and a.applied_at and a.last_update > a.applied_at
        )
        rej_7d = sum(
            1 for a in apps
            if a.status == Status.REJECTED and a.last_update and a.last_update >= week_ago
        )

        rows: list[list] = [[""] * 7 for _ in range(40)]
        rows[0][0] = "JOB APPLICATION TRACKER"
        rows[1][0] = f"Last synced {_fmt_full(now)} · {total} applications tracked"

        tiles = [
            ("ACTIVE", active), ("INTERVIEWS", n[Status.INTERVIEW]),
            ("OFFERS", n[Status.OFFER]), ("REJECTED", n[Status.REJECTED]),
            ("FOLLOW-UPS DUE", len(followups)), ("RESPONSE RATE", resp_rate),
        ]
        for col, (label, value) in enumerate(tiles):
            rows[3][col] = label
            rows[4][col] = value

        rows[6][0] = "PIPELINE"
        bar_max = max(1, max(n.values()))
        pipeline = [
            ("Applied", n[Status.APPLIED], "APPLIED"),
            ("Interview", n[Status.INTERVIEW], "INTERVIEW"),
            ("Offer", n[Status.OFFER], "OFFER"),
            ("Rejected", n[Status.REJECTED], "REJECTED"),
            ("Needs review", n[Status.NEEDS_REVIEW], "NEEDS_REVIEW"),
        ]
        for i, (label, count, key) in enumerate(pipeline):
            r = 7 + i
            rows[r][0] = label
            rows[r][1] = count
            rows[r][2] = (
                f'=SPARKLINE({count},{{"charttype","bar";"max",{bar_max};"color1","{_STATUS_HEX[key]}"}})'
            )

        rows[13][0] = "LAST 7 DAYS"
        rows[14][0] = "Applications sent"
        rows[14][1] = sent_7d
        rows[15][0] = "Responses received"
        rows[15][1] = resp_7d
        rows[16][0] = "Rejections"
        rows[16][1] = rej_7d

        rows[18][0] = f"NEEDS FOLLOW-UP ({len(followups)})"
        if followups:
            rows[19][0], rows[19][1], rows[19][2] = "Company", "Role", "Days silent"
            for i, a in enumerate(followups[:6]):
                r = 20 + i
                rows[r][0] = a.company
                rows[r][1] = a.role or "—"
                rows[r][2] = days_silent(a, now)
            if len(followups) > 6:
                rows[26][0] = f"…and {len(followups) - 6} more → see the Follow Up tab"
        else:
            rows[19][0] = "Nothing due — all caught up."

        ws.clear()
        ws.update(rows, "A1:G40", value_input_option="USER_ENTERED")

        req: list[dict] = [
            _grid_size(ws, rows=40, cols=7),
            *_col_widths(ws, _DASH_COL_WIDTHS),
            _tab_color(ws, {"red": 0.16, "green": 0.47, "blue": 0.84}),
            # Title + subtitle
            _text(ws, 0, 0, 1, 7, size=18, bold=True),
            _text(ws, 1, 1, 0, 7, size=9, color=_MUTED),
            # Stat tiles: label row (small caps muted) + value row (hero numbers)
            _fill(ws, 3, 5, 0, 6, _TILE_BG),
            _text(ws, 3, 4, 0, 6, size=9, bold=True, color=_MUTED),
            _text(ws, 4, 5, 0, 6, size=22, bold=True, color=_INK),
            _align(ws, 3, 5, 0, 6, "CENTER"),
            # Section headers
            _text(ws, 6, 7, 0, 2, size=11, bold=True),
            _text(ws, 13, 14, 0, 2, size=11, bold=True),
            _text(ws, 18, 19, 0, 3, size=11, bold=True),
            # Pipeline counts right-aligned
            _align(ws, 7, 12, 1, 2, "RIGHT"),
            _align(ws, 14, 17, 1, 2, "RIGHT"),
            # Follow-up mini-table header
            _text(ws, 19, 20, 0, 3, size=9, bold=True, color=_MUTED),
            _no_gridlines(ws),
        ]
        self._batch(req)

    # --- Applications -------------------------------------------------------

    def _write_applications(self, ws, apps: list[Application], now: datetime) -> None:
        def row_of(a: Application) -> list:
            return [
                a.company,
                a.role,
                _PROGRESS[a.status.value],
                _STATUS_LABEL[a.status.value],
                _fmt(a.applied_at),
                _fmt(a.last_update),
                days_silent(a, now),
                "⚠ Yes" if needs_followup(a, now, self.cfg.followup_days) else "",
                a.last_subject[:120],
                a.ats_source,
                a.notes,
                a.id,
            ]

        data = [HEADERS] + [row_of(a) for a in apps]
        nrows = len(data)
        ws.clear()
        ws.update(data, "A1")

        req: list[dict] = [
            _grid_size(ws, rows=max(nrows + 20, 60), cols=len(HEADERS)),
            *_col_widths(ws, _APP_COL_WIDTHS),
            _tab_color(ws, {"red": 0.16, "green": 0.16, "blue": 0.15}),
            _freeze(ws, rows=1, cols=1),
            # Dark header band
            _fill(ws, 0, 1, 0, len(HEADERS), _HEADER_BG),
            _text(ws, 0, 1, 0, len(HEADERS), size=10, bold=True,
                  color={"red": 1, "green": 1, "blue": 1}),
            # Hide app_id
            _hide_col(ws, len(HEADERS) - 1),
            # Alignment / clipping
            _align(ws, 1, nrows, 2, 3, "CENTER"),   # Progress
            _align(ws, 1, nrows, 3, 4, "CENTER"),   # Status
            _align(ws, 1, nrows, 6, 7, "RIGHT"),    # Days Silent
            _align(ws, 1, nrows, 7, 8, "CENTER"),   # Follow Up
            _clip(ws, 1, nrows, 8, 9),              # Latest Email
            _clip(ws, 1, nrows, 0, 2),              # Company / Role
            _text(ws, 1, nrows, 8, 9, size=9, color=_MUTED),
            _text(ws, 1, nrows, 9, 10, size=9, color=_MUTED),
            {"setBasicFilter": {"filter": {"range": {
                "sheetId": ws.id, "startRowIndex": 0, "endRowIndex": nrows,
                "startColumnIndex": 0, "endColumnIndex": len(HEADERS) - 1,
            }}}},
        ]
        # Subtle row banding (manual, since clear() drops banding ranges anyway)
        for i in range(1, nrows):
            if i % 2 == 0:
                req.append(_fill(ws, i, i + 1, 0, len(HEADERS) - 1, _BAND_BG))
        # Status chips + progress coloring
        for i, a in enumerate(apps):
            r = i + 1
            key = a.status.value
            req.append(_fill(ws, r, r + 1, 3, 4, _STATUS_TINT[key]))
            req.append(_text(ws, r, r + 1, 3, 4, size=10, bold=True, color=_INK))
            if key == "REJECTED":
                req.append(_text(ws, r, r + 1, 0, 2, color=_MUTED))
        self._batch(req)

    # --- Follow Up ----------------------------------------------------------

    def _write_followup(self, ws, apps: list[Application], now: datetime) -> None:
        fu = [a for a in apps if needs_followup(a, now, self.cfg.followup_days)]
        fu.sort(key=lambda a: -days_silent(a, now))
        data = [["Company", "Role", "Applied", "Days Silent", "Latest Email"]] + [
            [a.company, a.role, _fmt(a.applied_at), days_silent(a, now), a.last_subject[:120]]
            for a in fu
        ]
        ws.clear()
        ws.update(data, "A1")
        req = [
            _grid_size(ws, rows=max(len(data) + 10, 30), cols=5),
            *_col_widths(ws, [180, 260, 95, 90, 330]),
            _tab_color(ws, {"red": 0.98, "green": 0.70, "blue": 0.10}),
            _freeze(ws, rows=1),
            _fill(ws, 0, 1, 0, 5, _HEADER_BG),
            _text(ws, 0, 1, 0, 5, size=10, bold=True, color={"red": 1, "green": 1, "blue": 1}),
            _align(ws, 1, len(data), 3, 4, "RIGHT"),
            _clip(ws, 1, len(data), 4, 5),
            _text(ws, 1, len(data), 4, 5, size=9, color=_MUTED),
        ]
        self._batch(req)

    # --- Log ----------------------------------------------------------------

    def _write_log(self, ws, store: Store) -> None:
        data = [["Started", "Finished", "Fetched", "Candidates", "Classified",
                 "LLM Calls", "New", "Updated", "Needs Review", "Error"]]
        for r in store.recent_runs():
            data.append([
                (r["started_at"] or "")[:19].replace("T", " "),
                (r["finished_at"] or "")[:19].replace("T", " "),
                r["fetched"], r["candidates"], r["classified"], r["llm_calls"],
                r["new_apps"], r["updated_apps"], r["needs_review"], r["error"] or "",
            ])
        ws.clear()
        ws.update(data, "A1")
        req = [
            _grid_size(ws, rows=max(len(data) + 10, 30), cols=10),
            *_col_widths(ws, [150, 150, 80, 90, 85, 85, 60, 75, 100, 260]),
            _tab_color(ws, {"red": 0.55, "green": 0.55, "blue": 0.53}),
            _freeze(ws, rows=1),
            _fill(ws, 0, 1, 0, 10, _HEADER_BG),
            _text(ws, 0, 1, 0, 10, size=10, bold=True, color={"red": 1, "green": 1, "blue": 1}),
            _align(ws, 1, len(data), 2, 9, "RIGHT"),
        ]
        self._batch(req)

    def _batch(self, requests: list[dict]) -> None:
        try:
            self.doc.batch_update({"requests": requests})
        except Exception as exc:
            log.warning("Sheet formatting failed (non-fatal): %s", exc)


# --- request builders (Sheets API JSON) --------------------------------------


def _grid_size(ws, rows: int, cols: int) -> dict:
    return {"updateSheetProperties": {
        "properties": {"sheetId": ws.id, "gridProperties": {"rowCount": rows, "columnCount": cols}},
        "fields": "gridProperties.rowCount,gridProperties.columnCount",
    }}


def _col_widths(ws, widths: list[int]) -> list[dict]:
    return [{"updateDimensionProperties": {
        "range": {"sheetId": ws.id, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
        "properties": {"pixelSize": w},
        "fields": "pixelSize",
    }} for i, w in enumerate(widths)]


def _tab_color(ws, color: dict) -> dict:
    return {"updateSheetProperties": {
        "properties": {"sheetId": ws.id, "tabColorStyle": {"rgbColor": color}},
        "fields": "tabColorStyle",
    }}


def _freeze(ws, rows: int = 0, cols: int = 0) -> dict:
    return {"updateSheetProperties": {
        "properties": {"sheetId": ws.id,
                       "gridProperties": {"frozenRowCount": rows, "frozenColumnCount": cols}},
        "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
    }}


def _range(ws, r1: int, r2: int, c1: int, c2: int) -> dict:
    return {"sheetId": ws.id, "startRowIndex": r1, "endRowIndex": r2,
            "startColumnIndex": c1, "endColumnIndex": c2}


def _fill(ws, r1: int, r2: int, c1: int, c2: int, color: dict) -> dict:
    return {"repeatCell": {
        "range": _range(ws, r1, r2, c1, c2),
        "cell": {"userEnteredFormat": {"backgroundColor": color}},
        "fields": "userEnteredFormat.backgroundColor",
    }}


def _text(ws, r1: int, r2: int, c1: int, c2: int,
          size: int | None = None, bold: bool | None = None, color: dict | None = None) -> dict:
    fmt: dict = {}
    fields = []
    if size is not None:
        fmt["fontSize"] = size
        fields.append("userEnteredFormat.textFormat.fontSize")
    if bold is not None:
        fmt["bold"] = bold
        fields.append("userEnteredFormat.textFormat.bold")
    if color is not None:
        fmt["foregroundColorStyle"] = {"rgbColor": color}
        fields.append("userEnteredFormat.textFormat.foregroundColorStyle")
    return {"repeatCell": {
        "range": _range(ws, r1, r2, c1, c2),
        "cell": {"userEnteredFormat": {"textFormat": fmt}},
        "fields": ",".join(fields),
    }}


def _align(ws, r1: int, r2: int, c1: int, c2: int, how: str) -> dict:
    return {"repeatCell": {
        "range": _range(ws, r1, r2, c1, c2),
        "cell": {"userEnteredFormat": {"horizontalAlignment": how}},
        "fields": "userEnteredFormat.horizontalAlignment",
    }}


def _clip(ws, r1: int, r2: int, c1: int, c2: int) -> dict:
    return {"repeatCell": {
        "range": _range(ws, r1, r2, c1, c2),
        "cell": {"userEnteredFormat": {"wrapStrategy": "CLIP"}},
        "fields": "userEnteredFormat.wrapStrategy",
    }}


def _hide_col(ws, idx: int) -> dict:
    return {"updateDimensionProperties": {
        "range": {"sheetId": ws.id, "dimension": "COLUMNS", "startIndex": idx, "endIndex": idx + 1},
        "properties": {"hiddenByUser": True},
        "fields": "hiddenByUser",
    }}


def _no_gridlines(ws) -> dict:
    return {"updateSheetProperties": {
        "properties": {"sheetId": ws.id, "gridProperties": {"hideGridlines": True}},
        "fields": "gridProperties.hideGridlines",
    }}
