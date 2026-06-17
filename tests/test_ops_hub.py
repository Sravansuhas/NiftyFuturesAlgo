import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import ops_hub as hub
from app.branding import DEFAULT_ALGO_ID


class OpsHubStatusTests(unittest.TestCase):
    def test_build_status_report_paper_mode_token_warning(self):
        env = {
            "FORCE_DRY_RUN": "true",
            "ALGO_ID": "TESTOPS",
            "ENABLE_WEBSOCKET": "true",
        }
        with patch.dict(os.environ, env, clear=False):
            with patch(
                "app.kite_auth.validate_access_token",
                return_value=(False, None, "missing token"),
            ):
                with patch(
                    "app.intelligence_loop.intelligence_loop.run_safe_deploy_checklist",
                    return_value={
                        "ready": True,
                        "mode": "paper",
                        "blockers": [],
                        "warnings": ["Kite access token invalid"],
                        "checks": [],
                    },
                ):
                    with patch.object(hub, "_promotion_snapshot", return_value={
                        "per_index": {
                            "NIFTY": {"passed": False, "status": "no_record"},
                            "BANKNIFTY": {"passed": False, "status": "no_record"},
                            "SENSEX": {"passed": False, "status": "no_record"},
                        },
                    }):
                        report = hub.build_status_report()

        self.assertTrue(report["healthy"])
        self.assertEqual(report["mode"], "paper")
        self.assertEqual(report["algo_id"], "TESTOPS")
        self.assertFalse(report["token"]["valid"])
        self.assertTrue(any("token" in w.lower() for w in report["warnings"]))

    def test_build_status_report_live_blocks_bad_token(self):
        env = {
            "FORCE_DRY_RUN": "false",
            "LIVE_TRADING_CONFIRMED": "true",
            "ALGO_ID": "LIVEOPS",
        }
        with patch.dict(os.environ, env, clear=False):
            with patch(
                "app.kite_auth.validate_access_token",
                return_value=(False, None, "expired"),
            ):
                with patch(
                    "app.intelligence_loop.intelligence_loop.run_safe_deploy_checklist",
                    return_value={
                        "ready": False,
                        "mode": "live",
                        "blockers": ["Kite access token invalid"],
                        "warnings": [],
                        "checks": [],
                    },
                ):
                    with patch.object(hub, "_promotion_snapshot", return_value={
                        "per_index": {
                            "NIFTY": {"passed": True, "status": "promoted"},
                            "BANKNIFTY": {"passed": False, "status": "rejected"},
                            "SENSEX": {"passed": False, "status": "rejected"},
                        },
                    }):
                        report = hub.build_status_report()

        self.assertFalse(report["healthy"])
        self.assertEqual(report["mode"], "live")
        self.assertTrue(any("token" in b.lower() for b in report["blockers"]))


class OpsHubComplianceTests(unittest.TestCase):
    def test_run_compliance_checks_core_modules_pass(self):
        with patch.dict(os.environ, {"FORCE_DRY_RUN": "true"}, clear=False):
            with patch.object(
                hub,
                "run_startup_checks",
                return_value={"outbound_ip": "203.0.113.10", "algo_id": DEFAULT_ALGO_ID},
            ):
                report = hub.run_compliance_checks()

        self.assertIn("algo_id_resolved", [c["id"] for c in report["checks"]])
        self.assertIn("risk_gatekeeper", [c["id"] for c in report["checks"]])
        self.assertIn("order_burst_tracker", [c["id"] for c in report["checks"]])
        self.assertTrue(report["automated_passed"] >= 15)
        failed = report.get("failed_ids") or []
        self.assertNotIn("risk_gatekeeper", failed)
        self.assertNotIn("order_rate_limiter", failed)

    def test_compliance_live_without_confirmation_fails(self):
        with patch.dict(
            os.environ,
            {"FORCE_DRY_RUN": "false", "LIVE_TRADING_CONFIRMED": ""},
            clear=False,
        ):
            with patch.object(
                hub,
                "run_startup_checks",
                return_value={"outbound_ip": None, "algo_id": DEFAULT_ALGO_ID},
            ):
                report = hub.run_compliance_checks()

        self.assertFalse(report["passed"])
        self.assertIn("live_trading_confirmed_gate", report["failed_ids"])


class OpsHubDataHealthTests(unittest.TestCase):
    def test_run_data_health_cache_summary(self):
        datasets = [
            {
                "symbol": "NIFTY26JUNFUT",
                "rows": 500,
                "actual_to": "2026-06-10",
            },
            {
                "symbol": "BANKNIFTY26JUNFUT",
                "rows": 450,
                "actual_to": "2026-06-10",
            },
            {
                "symbol": "SENSEX26JUNFUT",
                "rows": 420,
                "actual_to": "2026-06-09",
            },
        ]
        fake_audit = lambda trade_date=None: {
            "overall": "healthy",
            "indices": {"NIFTY": {"status": "match"}},
            "trade_date": (trade_date or date(2026, 6, 10)).isoformat(),
        }

        with patch(
            "backtesting.data_loader.list_available_cached_datasets",
            return_value=datasets,
        ):
            report = hub.run_data_health(audit_runner=fake_audit, trade_date=date(2026, 6, 10))

        self.assertTrue(report["healthy"])
        self.assertEqual(report["cache"]["dataset_count"], 3)
        self.assertEqual(report["cache"]["by_index"]["NIFTY"]["total_rows"], 500)
        self.assertEqual(len(report["eod_audits"]), 1)

    def test_run_data_health_unhealthy_eod(self):
        with patch.object(hub, "_summarize_cache", return_value={"healthy": True, "dataset_count": 3}):
            report = hub.run_data_health(
                audit_runner=lambda trade_date=None: {"overall": "mismatch", "indices": {}},
            )

        self.assertFalse(report["healthy"])
        self.assertEqual(report["unhealthy_audit_days"], 1)


class OpsHubWfoAndPreflightTests(unittest.TestCase):
    def test_build_wfo_status_empty_candidates(self):
        with patch("backtesting.promotion_gates.load_candidates", return_value=[]):
            with patch(
                "app.promoted_params.preview_promoted_overlay",
                return_value={"eligible": False, "reason": "no promotion"},
            ):
                report = hub.build_wfo_status_report()

        self.assertFalse(report["any_passed"])
        self.assertTrue(any("run_promotion_wfo" in a for a in report["actions"]))
        self.assertIn("multi_index_run", report)

    def test_run_preflight_combines_sections(self):
        status = {
            "healthy": True,
            "mode": "paper",
            "blockers": [],
            "warnings": [],
            "algo_id": DEFAULT_ALGO_ID,
            "state": "BOOTING",
            "token": {"valid": True},
            "market": {"session_status": "CLOSED", "ist_time": "08:00:00 IST"},
            "websocket": {"detail": "engine not running"},
            "database": {"detail": "JSONL default"},
            "promotion": {"per_index": {}},
        }
        compliance = {
            "passed": True,
            "automated_passed": 20,
            "automated_total": 20,
            "checks": [],
            "failed_ids": [],
        }
        data_health = {"healthy": True, "cache": {"dataset_count": 1, "by_index": {}}, "eod_audits": []}
        wfo = {"indices": {}, "actions": [], "any_passed": False, "all_passed": False}

        with patch.object(hub, "build_status_report", return_value=status):
            with patch.object(hub, "run_compliance_checks", return_value=compliance):
                with patch.object(hub, "run_data_health", return_value=data_health):
                    with patch.object(hub, "build_wfo_status_report", return_value=wfo):
                        report = hub.run_preflight(validate_token=False)

        self.assertTrue(report["ready"])
        self.assertIn("status", report)
        self.assertIn("compliance", report)
        self.assertIn("data_health", report)
        self.assertIn("wfo", report)

    def test_run_preflight_fails_on_compliance(self):
        with patch.object(hub, "build_status_report", return_value={"healthy": True, "blockers": [], "warnings": [], "mode": "paper"}):
            with patch.object(
                hub,
                "run_compliance_checks",
                return_value={"passed": False, "automated_passed": 18, "automated_total": 20, "failed_ids": ["x"]},
            ):
                with patch.object(hub, "run_data_health", return_value={"healthy": True, "cache": {}, "unhealthy_audit_days": 0}):
                    with patch.object(hub, "build_wfo_status_report", return_value={"indices": {}, "actions": []}):
                        report = hub.run_preflight(validate_token=False)

        self.assertFalse(report["ready"])
        self.assertTrue(any("compliance" in b for b in report["blockers"]))


class OpsHubFormatterTests(unittest.TestCase):
    def test_format_status_includes_ops_tag(self):
        text = hub.format_status_report({
            "mode": "paper",
            "healthy": True,
            "algo_id": DEFAULT_ALGO_ID,
            "state": "BOOTING",
            "token": {"valid": False},
            "market": {"session_status": "CLOSED", "ist_time": "08:00 IST"},
            "websocket": {"detail": "engine not running"},
            "database": {"detail": "ok"},
            "promotion": {"per_index": {idx: {"passed": False, "status": "no_record"} for idx in hub.INDICES}},
            "blockers": [],
            "warnings": [],
        })
        self.assertIn(hub.OPS_TAG, text)
        self.assertIn("SYSTEM STATUS", text)


class OpsHubMigrateTests(unittest.TestCase):
    def test_run_db_migrate_skips_without_url(self):
        with patch("app.ops_hub.get_database_url", return_value=None):
            result = hub.run_db_migrate()
        self.assertTrue(result["ok"])
        self.assertTrue(result["skipped"])


if __name__ == "__main__":
    unittest.main()