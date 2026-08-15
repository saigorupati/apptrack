"""CLI entrypoint.

  python -m apptrack sync            one sync run (fetch, classify, sheet, digest)
  python -m apptrack sync --no-email   ... without sending the digest
  python -m apptrack sync --no-sheet   ... without writing the sheet
  python -m apptrack run             long-running nightly scheduler (container default)
  python -m apptrack digest          rebuild + send digest from current DB state
  python -m apptrack sheet           rewrite the sheet from current DB state
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import Config


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    parser = argparse.ArgumentParser(prog="apptrack")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sync = sub.add_parser("sync", help="run one sync now")
    p_sync.add_argument("--no-email", action="store_true")
    p_sync.add_argument("--no-sheet", action="store_true")

    sub.add_parser("run", help="nightly scheduler loop")
    sub.add_parser("digest", help="send digest from current state")
    sub.add_parser("sheet", help="rewrite sheet from current state")

    args = parser.parse_args()
    cfg = Config.from_env()

    if args.cmd == "sync":
        from .pipeline import run_sync

        stats = run_sync(cfg, send_email=not args.no_email, write_sheet=not args.no_sheet)
        return 1 if stats.get("error") else 0

    if args.cmd == "run":
        from .scheduler import run_forever

        run_forever(cfg)
        return 0

    if args.cmd == "digest":
        from .digest import build_digest, send_digest
        from .store import Store

        store = Store(cfg.db_path)
        subject, body, html_body = build_digest(cfg, store, {})
        send_digest(cfg, subject, body, html_body)
        store.close()
        return 0

    if args.cmd == "sheet":
        from .sheet import SheetWriter
        from .store import Store

        store = Store(cfg.db_path)
        writer = SheetWriter(cfg)
        writer.pull_notes(store)
        writer.write(store)
        store.close()
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
