"""Long-running nightly loop: run sync at SYNC_HOUR local time each day,
with catch-up if the previous scheduled run was missed (container restart etc.).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from .config import Config
from .pipeline import run_sync
from .store import Store

log = logging.getLogger(__name__)


def _next_run(now: datetime, hour: int) -> datetime:
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def run_forever(cfg: Config) -> None:
    # Catch-up: if the last completed run is older than ~25h, sync immediately.
    store = Store(cfg.db_path)
    last = store.get_state("last_sync_at")
    store.close()
    if last:
        try:
            age = datetime.now().astimezone() - datetime.fromisoformat(last).astimezone()
            if age > timedelta(hours=25):
                log.info("Last sync was %s ago — running catch-up now", age)
                _sync_and_stamp(cfg)
        except ValueError:
            pass
    else:
        log.info("No previous sync recorded — running initial sync now")
        _sync_and_stamp(cfg)

    while True:
        now = datetime.now().astimezone()
        nxt = _next_run(now, cfg.sync_hour)
        log.info("Next sync at %s (%.1fh from now)", nxt.isoformat(), (nxt - now).total_seconds() / 3600)
        time.sleep(max(1.0, (nxt - now).total_seconds()))
        _sync_and_stamp(cfg)


def _sync_and_stamp(cfg: Config) -> None:
    try:
        run_sync(cfg)
    except Exception:
        log.exception("Scheduled sync crashed; will retry next cycle")
    store = Store(cfg.db_path)
    store.set_state("last_sync_at", datetime.now().astimezone().isoformat())
    store.close()
