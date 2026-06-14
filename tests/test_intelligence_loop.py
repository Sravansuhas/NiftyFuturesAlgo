import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.intelligence_loop import IntelligenceLoop, _MIN_LEARNING_MULT


class IntelligenceLoopTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.loop = IntelligenceLoop(briefs_dir=Path(self._tmpdir.name) / "briefs")

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_learning_multiplier_never_exceeds_one(self):
        with patch.object(self.loop, "_get_promotion_for", return_value=None):
            mult, _ = self.loop.get_learning_risk_multiplier("NIFTY", "normal")
        self.assertLessEqual(mult, 1.0)
        self.assertGreaterEqual(mult, _MIN_LEARNING_MULT)

    def test_promoted_candidate_no_unvalidated_penalty(self):
        with patch.object(
            self.loop,
            "_get_promotion_for",
            return_value={"underlying": "NIFTY", "passed": True, "status": "promoted"},
        ):
            with patch.object(self.loop, "_regime_memory_adjustment", return_value=(1.0, "")):
                mult, reasons = self.loop.get_learning_risk_multiplier("NIFTY", "normal")
        self.assertEqual(mult, 1.0)
        self.assertFalse(any("unvalidated" in r for r in reasons))

    def test_negative_regime_memory_de_risks(self):
        with patch.object(
            self.loop,
            "_get_promotion_for",
            return_value={"underlying": "NIFTY", "passed": True, "status": "promoted"},
        ):
            with patch.object(
                self.loop,
                "_regime_memory_adjustment",
                return_value=(0.70, "memory: negative"),
            ):
                mult, _ = self.loop.get_learning_risk_multiplier("NIFTY", "high")
        self.assertAlmostEqual(mult, 0.70)

    def test_brief_save_and_format(self):
        brief = self.loop.build_market_brief()
        path = self.loop.save_market_brief(brief)
        self.assertTrue(path.exists())
        text = self.loop.format_brief_text(brief)
        self.assertIn("FO MARKET BRIEF", text)

    def test_safe_deploy_paper_warns_without_token(self):
        with patch.dict("os.environ", {"FORCE_DRY_RUN": "true"}, clear=False):
            with patch.object(self.loop, "_check_token_valid", return_value=False):
                result = self.loop.run_safe_deploy_checklist()
        self.assertTrue(result["ready"])
        self.assertEqual(result["mode"], "paper")
        self.assertTrue(any("token" in w.lower() for w in result["warnings"]))
        self.assertFalse(any("token" in b.lower() for b in result["blockers"]))

    def test_safe_deploy_live_blocks_without_token(self):
        with patch.dict(
            "os.environ",
            {"FORCE_DRY_RUN": "false", "LIVE_TRADING_CONFIRMED": "true"},
            clear=False,
        ):
            with patch.object(self.loop, "_check_token_valid", return_value=False):
                result = self.loop.run_safe_deploy_checklist()
        self.assertFalse(result["ready"])
        self.assertEqual(result["mode"], "live")
        self.assertTrue(any("token" in b.lower() for b in result["blockers"]))


if __name__ == "__main__":
    unittest.main()