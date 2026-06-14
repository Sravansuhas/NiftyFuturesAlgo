import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import state_persistence as sp


class StatePersistenceTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_dir = sp.STATE_DIR
        sp.STATE_DIR = Path(self._tmpdir.name) / "state"
        sp.LEGACY_STATE_FILE = Path(self._tmpdir.name) / "legacy.json"

    def tearDown(self):
        sp.STATE_DIR = self._orig_dir
        self._tmpdir.cleanup()

    def test_per_symbol_isolation(self):
        sp.save_symbol_state("NIFTY", {"entry_price": 25000.0, "symbol": "NIFTY26JUNFUT"})
        sp.save_symbol_state("BANKNIFTY", {"entry_price": 53000.0, "symbol": "BANKNIFTY26JUNFUT"})

        nifty = sp.load_symbol_state("NIFTY")
        bank = sp.load_symbol_state("BANKNIFTY")

        self.assertEqual(nifty["entry_price"], 25000.0)
        self.assertEqual(bank["entry_price"], 53000.0)

    def test_clear_symbol_state(self):
        sp.save_symbol_state("SENSEX", {"entry_price": 74000.0})
        sp.clear_symbol_state("SENSEX")
        self.assertIsNone(sp.load_symbol_state("SENSEX"))

    def test_load_all_symbol_states(self):
        sp.save_symbol_state("NIFTY", {"entry_price": 1.0})
        sp.save_symbol_state("SENSEX", {"entry_price": 2.0})
        all_states = sp.load_all_symbol_states()
        self.assertIn("NIFTY", all_states)
        self.assertIn("SENSEX", all_states)

    def test_atomic_write_leaves_valid_json(self):
        sp.save_symbol_state("NIFTY", {"entry_price": 25000.0, "symbol": "NIFTY26JUNFUT"})
        path = sp.STATE_DIR / "NIFTY.json"
        self.assertTrue(path.exists())
        self.assertFalse(path.with_suffix(".tmp").exists())
        loaded = sp.load_symbol_state("NIFTY")
        self.assertEqual(loaded["entry_price"], 25000.0)


if __name__ == "__main__":
    unittest.main()