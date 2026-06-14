import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.fo_market_mood import (
    _tape_zone,
    _tradeability_zone,
    bust_fo_mood_cache,
    compute_fo_market_mood,
    fetch_macro_cached,
)


def _trending_snap(trend="uptrend", htf="bullish", chop=0.15, adx=32.0, proposed="LONG", confidence=0.8):
    return {
        "proposed": proposed,
        "confidence": confidence,
        "regime": {
            "trend": trend,
            "volatility": "normal",
            "htf_bias": htf,
            "chop_score": chop,
            "adx_proxy": adx,
        },
    }


def _chop_snap(chop=0.82):
    return {
        "proposed": "FLAT",
        "confidence": 0.0,
        "regime": {
            "trend": "ranging",
            "volatility": "low",
            "htf_bias": "neutral",
            "chop_score": chop,
            "adx_proxy": 14.0,
        },
    }


def _open_market(**overrides):
    base = {
        "is_market_open": True,
        "is_safe_trading_window": True,
        "is_trading_holiday": False,
        "is_eod_flatten_window": False,
        "within_pre_event_block_window": False,
        "trading_allowed": True,
        "engine_trading_allowed": True,
        "engine_state": "PAPER_MODE",
    }
    base.update(overrides)
    return base


def _clear_guards():
    return {
        "any_blocked": False,
        "symbols": {
            sym: {"allowed": True, "block_reason": ""}
            for sym in ("NIFTY", "BANKNIFTY", "SENSEX")
        },
    }


def _aggressive_posture():
    return {
        "portfolio": {"posture": "aggressive", "market_color": "green"},
        "per_symbol": {},
    }


class FoMarketMoodTests(unittest.TestCase):
    def setUp(self):
        bust_fo_mood_cache()

    def _compute(self, snaps, market=None, guards=None, posture=None, **kwargs):
        live = {sym: snaps for sym in ("NIFTY", "BANKNIFTY", "SENSEX")} if isinstance(snaps, dict) and "regime" in snaps else snaps
        return compute_fo_market_mood(
            live,
            market or _open_market(),
            guards or _clear_guards(),
            posture or _aggressive_posture(),
            **kwargs,
        )

    def test_tape_zone_boundaries(self):
        self.assertEqual(_tape_zone(10), "chop_trap")
        self.assertEqual(_tape_zone(30), "weak")
        self.assertEqual(_tape_zone(50), "neutral")
        self.assertEqual(_tape_zone(65), "trend_ok")
        self.assertEqual(_tape_zone(85), "extended")

    def test_tradeability_zone_boundaries(self):
        self.assertEqual(_tradeability_zone(20), "blocked")
        self.assertEqual(_tradeability_zone(45), "cautious")
        self.assertEqual(_tradeability_zone(75), "ready")

    def test_chop_trap_on_high_chop_ranging(self):
        mood = self._compute(_chop_snap(chop=0.88))
        self.assertEqual(mood["tape_zone"], "chop_trap")
        self.assertLess(mood["tape_mood"], 25)
        self.assertIn("chop", mood["human_summary"].lower())

    def test_trend_ok_on_clean_uptrend(self):
        snap = _trending_snap(chop=0.45, adx=22.0, htf="neutral", proposed="FLAT", confidence=0.0)
        mood = self._compute(snap)
        self.assertEqual(mood["tape_zone"], "trend_ok")
        self.assertGreaterEqual(mood["tape_mood"], 55)
        self.assertLess(mood["tape_mood"], 75)

    def test_extended_on_strong_one_sided_breakout(self):
        snap = _trending_snap(trend="uptrend", htf="bullish", chop=0.05, adx=38.0, proposed="LONG", confidence=0.92)
        mood = self._compute(snap)
        self.assertEqual(mood["tape_zone"], "extended")
        self.assertGreaterEqual(mood["tape_mood"], 75)

    def test_tradeability_blocked_when_guards_and_halt(self):
        guards = {
            "any_blocked": True,
            "portfolio_block_reason": "FO_CHOP_VETO: ranging",
            "symbols": {
                "NIFTY": {"allowed": False, "blocked_rule": "FO_CHOP_VETO"},
                "BANKNIFTY": {"allowed": False, "blocked_rule": "FO_CHOP_VETO"},
                "SENSEX": {"allowed": True},
            },
        }
        market = _open_market(
            trading_allowed=False,
            engine_trading_allowed=False,
            engine_state="EMERGENCY_HALT",
        )
        posture = {"portfolio": {"posture": "contingency", "market_color": "sideways"}}
        mood = self._compute(_chop_snap(), market=market, guards=guards, posture=posture)
        self.assertEqual(mood["tradeability_zone"], "blocked")
        self.assertLess(mood["tradeability"], 35)

    def test_tradeability_ready_in_green_session(self):
        mood = self._compute(_trending_snap())
        self.assertEqual(mood["tradeability_zone"], "ready")
        self.assertGreaterEqual(mood["tradeability"], 60)

    def test_mismatch_brother_onesided_vs_algo_ranging(self):
        brother_sheet = {
            "date": "2026-06-12",
            "indices": {
                "NIFTY": {
                    "call": {"strike": 23100, "entry": 180, "journal_status": "entered"},
                    "put": {},
                },
                "BANKNIFTY": {"call": {}, "put": {}},
                "SENSEX": {"call": {}, "put": {}},
            },
        }
        with patch("app.fo_market_mood._load_brother_sheet", return_value=brother_sheet):
            mood = self._compute(_chop_snap(chop=0.78))
        self.assertTrue(mood["mismatch"])
        self.assertIn("brother one-sided CE", mood["mismatch_detail"])
        self.assertIn("Mismatch", mood["human_summary"])
        self.assertEqual(mood["per_index"]["NIFTY"]["brother_bias"], "bullish")
        self.assertEqual(mood["per_index"]["NIFTY"]["algo_trend"], "ranging")

    def test_per_index_breakdown_present(self):
        snaps = {
            "NIFTY": _trending_snap(),
            "BANKNIFTY": _chop_snap(),
            "SENSEX": _trending_snap(trend="downtrend", htf="bearish", proposed="SHORT"),
        }
        mood = self._compute(snaps)
        for sym in ("NIFTY", "BANKNIFTY", "SENSEX"):
            self.assertIn(sym, mood["per_index"])
            self.assertIn("tape_mood", mood["per_index"][sym])
            self.assertIn("components", mood["per_index"][sym])
        self.assertTrue(any(c.get("scope") == "tape" for c in mood["components"]))

    def test_cache_and_bust(self):
        snap = _trending_snap()
        first = self._compute(snap)
        second = self._compute(snap)
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        bust_fo_mood_cache()
        third = self._compute(snap)
        self.assertFalse(third["cached"])

    @patch("app.fo_market_mood.fetch_macro_context", create=True)
    def test_fetch_macro_cached_ttl(self, mock_fetch):
        import app.fo_market_mood as mod

        mod._MACRO_CACHE["ts"] = 0.0
        mod._MACRO_CACHE["payload"] = None
        mock_fetch.return_value = {"vix": {"zone": "normal"}, "fetched_at": "t1"}

        with patch("app.nse_data.fetch_macro_context", mock_fetch):
            a = fetch_macro_cached(force=True)
            b = fetch_macro_cached()
        self.assertEqual(a, b)
        mock_fetch.assert_called_once()


if __name__ == "__main__":
    unittest.main()