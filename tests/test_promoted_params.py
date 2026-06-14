import sys
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.paper_trading_params import DEFAULT_PAPER_PARAMS
from app import promoted_params as pp


def _base_dict():
    return {f.name: getattr(DEFAULT_PAPER_PARAMS, f.name) for f in fields(DEFAULT_PAPER_PARAMS)}


class PromotedParamsTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._overlay_dir = Path(self._tmpdir.name) / "overlays"
        self._overlay_dir.mkdir()
        self._patch_overlay = patch.object(pp, "OVERLAY_DIR", self._overlay_dir)
        self._patch_overlay.start()

    def tearDown(self):
        self._patch_overlay.stop()
        self._tmpdir.cleanup()

    def test_sanitize_never_increases_risk(self):
        proposed = {
            "risk_per_trade_pct": 0.01,
            "max_trades_per_day": 10,
            "cooldown_minutes_after_trade": 5,
        }
        sanitized = pp._sanitize_overlay(_base_dict(), proposed)
        self.assertLessEqual(sanitized["risk_per_trade_pct"], DEFAULT_PAPER_PARAMS.risk_per_trade_pct)
        self.assertLessEqual(sanitized["max_trades_per_day"], DEFAULT_PAPER_PARAMS.max_trades_per_day)
        self.assertGreaterEqual(
            sanitized["cooldown_minutes_after_trade"],
            DEFAULT_PAPER_PARAMS.cooldown_minutes_after_trade,
        )

    def test_apply_requires_human_confirmation(self):
        promo = {
            "underlying": "NIFTY",
            "passed": True,
            "status": "promoted",
            "best_params": {"breakout_atr_mult": 0.72, "risk_per_trade_pct": 0.002},
        }
        with patch.object(pp, "_get_promoted_candidate", return_value=promo):
            result = pp.apply_promoted_overlay("NIFTY", human_confirmed=False)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "human_confirmation_required")

    def test_apply_writes_overlay_when_confirmed(self):
        promo = {
            "underlying": "NIFTY",
            "passed": True,
            "status": "promoted",
            "best_params": {"breakout_atr_mult": 0.72},
            "fold_pass_count": 2,
        }
        with patch.object(pp, "_get_promoted_candidate", return_value=promo):
            result = pp.apply_promoted_overlay("NIFTY", human_confirmed=True)
        self.assertTrue(result["success"])
        self.assertTrue((self._overlay_dir / "NIFTY.json").exists())

    def test_merge_skips_without_env_flag(self):
        merged, meta = pp.merge_paper_params(DEFAULT_PAPER_PARAMS, "NIFTY", use_overlay=False)
        self.assertEqual(merged.breakout_atr_mult, DEFAULT_PAPER_PARAMS.breakout_atr_mult)
        self.assertFalse(meta["overlay_applied"])


if __name__ == "__main__":
    unittest.main()