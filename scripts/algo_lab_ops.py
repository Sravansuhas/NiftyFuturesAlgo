#!/usr/bin/env python3
"""
Aegis — unified research + ops command center.

Usage:
    python scripts/algo_lab_ops.py status
    python scripts/algo_lab_ops.py compliance
    python scripts/algo_lab_ops.py data-health [--days N]
    python scripts/algo_lab_ops.py lunar [--refresh]
    python scripts/algo_lab_ops.py wfo-status
    python scripts/algo_lab_ops.py wfo-run [--days 180] [--indices NIFTY,BANKNIFTY]
    python scripts/algo_lab_ops.py preflight [--days N] [--skip-token]
    python scripts/algo_lab_ops.py insights [--refresh]
    python scripts/algo_lab_ops.py migrate-db
    python scripts/algo_lab_ops.py chain-archive [--indices NIFTY] [--force]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.branding import PROJECT_NAME  # noqa: E402
from app.ops_hub import (  # noqa: E402
    OPS_TAG,
    build_lunar_today,
    build_status_report,
    build_wfo_status_report,
    format_compliance_report,
    format_data_health_report,
    format_preflight_report,
    format_status_report,
    format_wfo_status_report,
    run_compliance_checks,
    run_data_health,
    run_db_migrate,
    run_preflight,
)


def cmd_status(args: argparse.Namespace) -> int:
    report = build_status_report(validate_token=not args.skip_token)
    print(format_status_report(report))
    return 0 if report.get("healthy") else 1


def cmd_compliance(args: argparse.Namespace) -> int:
    report = run_compliance_checks()
    print(format_compliance_report(report))
    return 0 if report.get("passed") else 1


def cmd_data_health(args: argparse.Namespace) -> int:
    report = run_data_health(audit_days=args.days)
    print(format_data_health_report(report))
    return 0 if report.get("healthy") else 1


def cmd_lunar(args: argparse.Namespace) -> int:
    result = build_lunar_today(refresh=args.refresh)
    print(f"{OPS_TAG} Lunar context saved: {result['path']}")
    print(result["summary"])
    print(f"{OPS_TAG} Note: research metadata only — not a primary trading signal.")
    return 0


def cmd_wfo_status(args: argparse.Namespace) -> int:
    report = build_wfo_status_report()
    print(format_wfo_status_report(report))
    return 0


def cmd_wfo_run(args: argparse.Namespace) -> int:
    from scripts.run_multi_index_wfo import main as run_multi_index_wfo_main

    argv: list[str] = ["--days", str(args.days), "--indices", args.indices]
    if args.cache_only:
        argv.append("--cache-only")
    if args.cost_multiplier is not None:
        argv.extend(["--cost-multiplier", str(args.cost_multiplier)])
    return int(run_multi_index_wfo_main(argv))


def cmd_preflight(args: argparse.Namespace) -> int:
    report = run_preflight(
        validate_token=not args.skip_token,
        audit_days=args.days,
    )
    print(format_preflight_report(report))
    return 0 if report.get("ready") else 1


def cmd_insights(args: argparse.Namespace) -> int:
    from app.agent_insights import (
        build_agent_insights,
        format_agent_insights_summary,
        save_agent_insights,
    )

    insights = build_agent_insights(refresh_lunar=args.refresh)
    path = save_agent_insights(insights)
    print(f"{OPS_TAG} Agent insights saved: {path}")
    print(format_agent_insights_summary(insights))
    return 0


def cmd_chain_archive(args: argparse.Namespace) -> int:
    from app.instruments_manager import instruments_manager
    from app.options_chain import options_chain_manager
    from app.options_chain_archive import save_chain_snapshot
    from config import KITE_API_KEY
    from kiteconnect import KiteConnect
    from app.token_manager import TokenManager

    indices = [s.strip().upper() for s in args.indices.split(",") if s.strip()]
    kite = KiteConnect(api_key=KITE_API_KEY)
    token_manager = TokenManager(kite)
    if not token_manager.token_valid:
        print(f"{OPS_TAG} Kite token invalid — run generate_token.py first")
        return 1

    instruments_manager.bind(kite, force=True)
    options_chain_manager.bind(kite)

    saved = []
    for index in indices:
        df = options_chain_manager.fetch_and_cache_chain(
            kite=kite,
            underlying=index,
            force_refresh=args.force,
        )
        if df is None or df.empty:
            print(f"{OPS_TAG} [CHAIN] No data for {index}")
            continue
        path = save_chain_snapshot(index, df, metadata={"source": "algo_lab_ops"})
        saved.append((index, path))
        print(f"{OPS_TAG} [CHAIN] Archived {index} → {path} ({len(df)} rows)")

    return 0 if saved else 1


def cmd_migrate_db(args: argparse.Namespace) -> int:
    result = run_db_migrate()
    if result.get("skipped"):
        print(f"{OPS_TAG} [DB] {result.get('detail')}")
        return 0
    if not result.get("ok"):
        print(f"{OPS_TAG} [DB] {result.get('detail')}")
        return 1
    for name in result.get("files") or []:
        print(f"{OPS_TAG} [DB] Applied {name}")
    print(f"{OPS_TAG} [DB] {result.get('detail')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=f"{PROJECT_NAME} ops hub — research + deployment command center",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="System health snapshot")
    p_status.add_argument(
        "--skip-token",
        action="store_true",
        help="Skip live Kite token validation",
    )
    p_status.set_defaults(func=cmd_status)

    p_compliance = sub.add_parser("compliance", help="Code-checkable COMPLIANCE.md items")
    p_compliance.set_defaults(func=cmd_compliance)

    p_data = sub.add_parser("data-health", help="Cache coverage + EOD audit summary")
    p_data.add_argument("--days", type=int, default=1, help="Rolling EOD audit days")
    p_data.set_defaults(func=cmd_data_health)

    p_lunar = sub.add_parser("lunar", help="Build lunar/panchang context for today")
    p_lunar.add_argument("--refresh", action="store_true", help="Ignore same-day cache")
    p_lunar.set_defaults(func=cmd_lunar)

    p_wfo = sub.add_parser("wfo-status", help="Promotion gate status per index")
    p_wfo.set_defaults(func=cmd_wfo_status)

    p_wfo_run = sub.add_parser("wfo-run", help="Run multi-index walk-forward optimization")
    p_wfo_run.add_argument("--days", type=int, default=180, help="Days of history to use")
    p_wfo_run.add_argument(
        "--indices",
        type=str,
        default="NIFTY,BANKNIFTY,SENSEX",
        help="Comma-separated indices",
    )
    p_wfo_run.add_argument(
        "--cache-only",
        action="store_true",
        help="Use historical_cache parquet only (no Kite fetch)",
    )
    p_wfo_run.add_argument(
        "--cost-multiplier",
        type=float,
        default=None,
        help="Cost stress multiplier (default: 1.0)",
    )
    p_wfo_run.set_defaults(func=cmd_wfo_run)

    p_insights = sub.add_parser(
        "insights",
        help="Build agent insights snapshot (promotion + WFO + proposals + lunar)",
    )
    p_insights.add_argument(
        "--refresh",
        action="store_true",
        help="Rebuild lunar context even if cached for today",
    )
    p_insights.set_defaults(func=cmd_insights)

    p_preflight = sub.add_parser(
        "preflight",
        help="Morning routine: status + compliance + data-health + wfo",
    )
    p_preflight.add_argument("--days", type=int, default=1, help="Rolling EOD audit days")
    p_preflight.add_argument(
        "--skip-token",
        action="store_true",
        help="Skip live Kite token validation",
    )
    p_preflight.set_defaults(func=cmd_preflight)

    p_migrate = sub.add_parser("migrate-db", help="Apply SQL migrations (DATABASE_URL)")
    p_migrate.set_defaults(func=cmd_migrate_db)

    p_chain = sub.add_parser("chain-archive", help="Archive today's option chains to parquet")
    p_chain.add_argument(
        "--indices",
        type=str,
        default="NIFTY",
        help="Comma-separated indices (NIFTY,BANKNIFTY,SENSEX)",
    )
    p_chain.add_argument(
        "--force",
        action="store_true",
        help="Force refresh from Kite instruments (ignore parquet cache)",
    )
    p_chain.set_defaults(func=cmd_chain_archive)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())