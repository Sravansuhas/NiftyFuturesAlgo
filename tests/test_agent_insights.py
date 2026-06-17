import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import agent_insights as ai


class AgentInsightsTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.base = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _patch_paths(self):
        return patch.multiple(
            ai,
            AGENT_INSIGHTS_PATH=self.base / "agent_insights.json",
            MARKET_CONTEXT_PATH=self.base / "market_context.json",
            PROJECT_ROOT=self.base,
        )

    def test_build_agent_insights_aggregates_sections(self):
        promo = {
            "per_index": {
                "NIFTY": {"passed": True, "status": "promoted", "overlay_eligible": True},
                "BANKNIFTY": {"passed": False, "status": "rejected"},
                "SENSEX": {"passed": False, "status": "no_record"},
            },
            "any_passed": True,
            "all_passed": False,
        }
        wfo = {
            "has_report": True,
            "run_id": "multi_index_test",
            "finished_at": "2026-06-15T10:00:00",
            "summary": {"passed_count": 1, "index_count": 3},
            "per_index": {
                "NIFTY": {"has_record": True, "passed": True, "avg_pf": 1.3},
            },
        }
        lunar = {
            "date_ist": "2026-06-15",
            "panchang": {"paksha": "krishna", "tithi_name": "Dashami"},
            "astronomical": {"phase_name": "Waning Gibbous", "illumination_pct": 72.1},
            "session_hints": {"folklore_tag": "neutral"},
            "trading_context": {"is_trading_session": True, "is_expiry_day": False},
        }

        with self._patch_paths():
            with patch("app.ops_hub._promotion_snapshot", return_value=promo):
                with patch("app.ops_hub.run_multi_index_wfo_status", return_value=wfo):
                    with patch(
                        "app.improvement_loop.improvement_loop.list_pending_proposals",
                        return_value=[{"id": "prop_1", "status": "pending_review"}],
                    ):
                        with patch(
                            "app.lunar_calendar.load_lunar_context",
                            return_value=lunar,
                        ):
                            insights = ai.build_agent_insights()

        self.assertIn("promotion_status", insights)
        self.assertTrue(insights["promotion_status"]["NIFTY"]["passed"])
        self.assertTrue(insights["multi_index_wfo"]["has_report"])
        self.assertEqual(insights["pending_proposals"]["count"], 1)
        self.assertTrue(insights["lunar_context"]["available"])
        self.assertFalse(insights["market_context"]["available"])
        self.assertTrue(insights["human_gate_required"])
        self.assertGreaterEqual(len(insights["documentation_notes"]), 3)
        self.assertGreaterEqual(len(insights["founder_actions"]), 1)

    def test_save_and_load_round_trip(self):
        payload = {"generated_at": "2026-06-15T09:00:00", "date_ist": "2026-06-15"}

        with self._patch_paths():
            path = ai.save_agent_insights(payload)
            self.assertTrue(path.exists())
            loaded = ai.load_agent_insights()
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["date_ist"], "2026-06-15")
            self.assertEqual(loaded["saved_path"], str(path))

    def test_market_context_loaded_when_file_exists(self):
        market = {"vix_level": 14.2, "bias": "neutral"}
        (self.base / "market_context.json").write_text(
            json.dumps(market), encoding="utf-8"
        )

        with self._patch_paths():
            ctx = ai._load_market_context()

        self.assertTrue(ctx["available"])
        self.assertEqual(ctx["payload"]["vix_level"], 14.2)

    def test_format_summary_includes_promotion_lines(self):
        insights = {
            "generated_at": "2026-06-15T09:00:00",
            "date_ist": "2026-06-15",
            "promotion_status": {
                "NIFTY": {"passed": True, "status": "promoted", "overlay_eligible": True},
            },
            "multi_index_wfo": {"has_report": False},
            "pending_proposals": {"count": 0},
            "lunar_context": {"available": False},
            "market_context": {"available": False},
            "documentation_notes": ["test note"],
            "founder_actions": ["do something"],
        }
        text = ai.format_agent_insights_summary(insights)
        self.assertIn("AGENT INSIGHTS", text)
        self.assertIn("Promotion NIFTY", text)
        self.assertIn("test note", text)


if __name__ == "__main__":
    unittest.main()