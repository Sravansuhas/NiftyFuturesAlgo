import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.improvement_loop import ImprovementLoop


class ImprovementWfoBridgeTests(unittest.TestCase):
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

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_submit_wfo_candidate_writes_pending_proposal(self):
        params = {
            "risk_per_trade_pct": 0.0035,
            "breakout_atr_mult": 0.75,
            "avg_pf": 1.42,
            "avg_return": 1.8,
            "wfo_mode": "rolling_purged",
            "fold_pass_count": 2,
        }

        path = self.loop.submit_wfo_candidate(params, "NIFTY")
        self.assertTrue(path.exists())
        self.assertEqual(path.parent, self.loop.proposals_dir)

        with path.open("r", encoding="utf-8") as handle:
            saved = json.load(handle)

        self.assertEqual(saved["proposal_type"], "wfo_candidate")
        self.assertEqual(saved["underlying"], "NIFTY")
        self.assertEqual(saved["status"], "pending_review")
        self.assertTrue(saved["human_gate_required"])
        self.assertFalse(saved.get("auto_apply", True))
        self.assertEqual(saved["params"]["avg_pf"], 1.42)
        self.assertIn("wfo_candidate_nifty", saved["id"])

    def test_submit_wfo_candidate_never_auto_applies(self):
        params = {"avg_pf": 1.1, "avg_return": 0.5}
        path = self.loop.submit_wfo_candidate(params, "BANKNIFTY")

        proposal_id = json.loads(path.read_text(encoding="utf-8"))["id"]
        outcome = self.loop.apply_improvement_proposal(proposal_id, human_confirmed=True)

        self.assertTrue(outcome["applied"])
        self.assertIsNotNone(outcome.get("manifest_path"))
        manifest_path = Path(outcome["manifest_path"])
        self.assertTrue(manifest_path.exists())
        self.assertEqual(manifest_path.parent, self.loop.applied_dir)
        self.assertFalse(any(self.loop.proposals_dir.glob("*.yaml")))
        self.assertFalse(any(self.loop.proposals_dir.glob("*.py")))

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["manifest_type"], "improvement_proposal_approval")
        self.assertIn("no automatic", manifest["note"].lower())

    def test_submit_wfo_candidate_rejects_unknown_underlying(self):
        with self.assertRaises(ValueError):
            self.loop.submit_wfo_candidate({"avg_pf": 1.0}, "FOO")

    def test_pending_list_includes_wfo_candidate(self):
        self.loop.submit_wfo_candidate({"avg_pf": 1.2}, "SENSEX")
        pending = self.loop.list_pending_proposals()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["proposal_type"], "wfo_candidate")
        self.assertEqual(pending[0]["underlying"], "SENSEX")


if __name__ == "__main__":
    unittest.main()