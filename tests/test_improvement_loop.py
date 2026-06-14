import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.improvement_loop import ImprovementLoop


class ImprovementLoopTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        base = Path(self._tmpdir.name)
        self.loop = ImprovementLoop(
            earn_dir=base / "earn_reports",
            proposals_dir=base / "improvement_proposals",
            applied_dir=base / "improvement_proposals" / "applied",
            failure_proposals_dir=base / "knowledge_base" / "proposals",
            sessions_stub_dir=base / "sessions",
            audit_path=base / "audit_events.json",
        )
        self.loop.failure_proposals_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _minimal_report(self, **overrides) -> dict:
        report = {
            "session_summary": {
                "reports_found": 1,
                "avg_quality_score": 80.0,
                "quality_trend": "stable",
                "total_pnl_rs": 0.0,
                "total_trades": 2,
                "recon_halts": 0,
            },
            "fill_calibration": {
                "fills_analyzed": 10,
                "cost_premium_pct": 5.0,
                "has_snapshot": True,
            },
            "wfa_summary": {
                "memory_runs": 5,
                "indices": {
                    "NIFTY": {"passed": True, "runs": 2, "avg_pf": 1.3},
                    "BANKNIFTY": {"passed": True, "runs": 1, "avg_pf": 1.1},
                    "SENSEX": {"passed": True, "runs": 1, "avg_pf": 1.0},
                },
            },
            "failure_patterns_pending": {"count": 0, "top_rule_ids": []},
        }
        report.update(overrides)
        return report

    def test_weekly_report_structure(self):
        with patch.object(self.loop, "_load_session_reports", return_value=[]):
            with patch.object(self.loop, "_build_wfa_summary", return_value={
                "indices": {"NIFTY": {"runs": 0, "avg_pf": None, "passed": False}},
                "memory_runs": 0,
                "memory_trades": 0,
            }):
                with patch.object(self.loop, "_build_fill_calibration", return_value={
                    "fills_analyzed": 0,
                    "cost_premium_pct": None,
                    "notes": [],
                    "has_snapshot": False,
                }):
                    report = self.loop.build_weekly_earn_report(weeks_back=1)

        required = {
            "wfa_summary",
            "session_summary",
            "fill_calibration",
            "failure_patterns_pending",
            "improvement_proposals",
            "founder_actions",
            "documentation_notes",
            "week_label",
        }
        self.assertTrue(required.issubset(report.keys()))
        self.assertIn("avg_quality_score", report["session_summary"])
        self.assertIn("quality_trend", report["session_summary"])
        self.assertIn("recon_halts", report["session_summary"])

        path = self.loop.save_weekly_report(report)
        self.assertTrue(path.exists())
        self.assertRegex(path.name, r"^\d{4}-W\d{2}\.json$")

    def test_proposals_require_human_gate(self):
        report = self._minimal_report()
        report["session_summary"]["avg_quality_score"] = 55.0
        report["fill_calibration"]["cost_premium_pct"] = 20.0
        report["session_summary"]["recon_halts"] = 2
        report["wfa_summary"]["indices"]["SENSEX"]["passed"] = False

        proposals = self.loop.generate_improvement_proposals(report)
        self.assertGreaterEqual(len(proposals), 3)
        for proposal in proposals:
            self.assertTrue(proposal["human_gate_required"])
            self.assertEqual(proposal["status"], "pending_review")
            self.assertIn("id", proposal)
            self.assertIn("severity", proposal)

    def test_apply_rejected_without_confirmation(self):
        outcome = self.loop.apply_improvement_proposal(
            "reduce_max_trades_per_day",
            human_confirmed=False,
        )
        self.assertFalse(outcome["applied"])
        self.assertEqual(outcome["reason"], "human_confirmation_required")
        self.assertIn("preview", outcome)
        self.assertIsNone(outcome["manifest_path"])
        self.assertFalse(any(self.loop.applied_dir.glob("*.json")))

    def test_apply_records_manifest_when_confirmed(self):
        proposal_path = self.loop.record_improvement_proposal({
            "id": "reduce_max_trades_per_day",
            "severity": "high",
            "target": "risk_gatekeeper",
            "description": "test",
        })
        self.assertTrue(proposal_path.exists())

        outcome = self.loop.apply_improvement_proposal(
            "reduce_max_trades_per_day",
            human_confirmed=True,
        )
        self.assertTrue(outcome["applied"])
        self.assertIsNotNone(outcome["manifest_path"])
        self.assertTrue(Path(outcome["manifest_path"]).exists())
        self.assertIn("preview", outcome)

        with proposal_path.open("r", encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual(saved["status"], "applied")

    def test_small_sample_low_confidence_notes(self):
        with patch.object(self.loop, "_load_session_reports", return_value=[
            {"date_ist": "2026-06-09", "quality_score": 72},
        ]):
            with patch.object(self.loop, "_build_wfa_summary", return_value={
                "memory_runs": 1,
                "indices": {"NIFTY": {"passed": False}},
            }):
                with patch.object(self.loop, "_build_fill_calibration", return_value={
                    "fills_analyzed": 2,
                    "cost_premium_pct": None,
                    "has_snapshot": True,
                    "notes": [],
                }):
                    report = self.loop.build_weekly_earn_report()

        notes = " ".join(report["documentation_notes"]).lower()
        self.assertIn("small session sample", notes)
        self.assertIn("low confidence", notes)
        self.assertIn("wfa memory thin", notes)

    def test_sessions_stub_reads_when_tracker_empty(self):
        self.loop.sessions_stub_dir.mkdir(parents=True, exist_ok=True)
        stub = {
            "date_ist": "2026-06-10",
            "quality_score": 78,
            "quality_grade": "B",
            "risk_snapshot": {"daily_pnl": 500, "trades_today": 1},
        }
        (self.loop.sessions_stub_dir / "2026-06-10.json").write_text(
            json.dumps(stub), encoding="utf-8"
        )

        with patch(
            "app.improvement_loop.ImprovementLoop._load_session_reports",
            wraps=self.loop._load_session_reports,
        ):
            with patch("app.session_tracker.session_tracker") as mock_tracker:
                mock_tracker.list_reports_since.return_value = []
                reports = self.loop._load_session_reports(days=7)

        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["quality_score"], 78)

    def test_failure_patterns_pending_top_rule_ids(self):
        proposal = {
            "rule_id": "FO_TEST_RULE",
            "status": "pending_review",
            "description": "test",
        }
        path = self.loop.failure_proposals_dir / "proposal_test.json"
        path.write_text(json.dumps(proposal), encoding="utf-8")

        pending = self.loop._build_failure_patterns_pending()
        self.assertEqual(pending["count"], 1)
        self.assertEqual(pending["top_rule_ids"], ["FO_TEST_RULE"])

    def test_recon_halts_counted_from_audit(self):
        now = time.time()
        self.loop.audit_path.parent.mkdir(parents=True, exist_ok=True)
        events = [
            {"ts": now - 100, "event_type": "recon.mismatch_halt", "payload": {}},
            {"ts": now - 200, "event_type": "order.placed", "payload": {}},
            {"ts": now - 999999, "event_type": "recon.mismatch_halt", "payload": {}},
        ]
        self.loop.audit_path.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n",
            encoding="utf-8",
        )
        self.assertEqual(self.loop._count_recon_halts(days=7), 1)


if __name__ == "__main__":
    unittest.main()