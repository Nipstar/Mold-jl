"""CLI entrypoint. Stage 1: `python -m src.cli ingest-dbpr`."""
from __future__ import annotations

import argparse
import json

from . import config, db
from .clients import dbpr
from .ingest import licenses


def cmd_ingest_dbpr(args: argparse.Namespace) -> None:
    db.run_migrations()
    records, spend = dbpr.run_actor_extract(
        profession="mold-related-services",
        status="any",
        max_results=args.max_results,
        force_refresh=args.force_refresh,
    )
    summary = licenses.ingest(records)
    summary["estimated_spend_usd"] = round(spend, 4)
    summary["cost_per_record_usd"] = dbpr.COST_PER_RECORD

    print(json.dumps(summary, indent=2))

    conn = db.get_connection()
    print("\nSample companies:")
    for row in db.sample_companies(conn, args.sample):
        print(dict(row))
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="jl-mold-fl")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest-dbpr", help="Stage 1: pull + ingest FL DBPR mold licenses")
    p_ingest.add_argument("--max-results", type=int, default=20000)
    p_ingest.add_argument("--force-refresh", action="store_true")
    p_ingest.add_argument("--sample", type=int, default=10)
    p_ingest.set_defaults(func=cmd_ingest_dbpr)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
