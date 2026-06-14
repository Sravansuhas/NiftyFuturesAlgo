import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.overnight_context import _gap_regime, _session_hints


class OvernightContextTests(unittest.TestCase):
    def test_large_gap_regime(self):
        self.assertEqual(_gap_regime(0.65), "large_up")
        self.assertEqual(_gap_regime(-0.72), "large_down")

    def test_defensive_hints_on_large_gap(self):
        hints = _session_hints(0.65, "large_up")
        self.assertEqual(hints["posture_floor"], "defensive")
        self.assertLess(hints["max_trades_delta"], 0)


if __name__ == "__main__":
    unittest.main()