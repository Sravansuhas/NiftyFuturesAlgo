import sys
import tempfile
import unittest
import unittest.mock
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.options_chain_archive import (
    ARCHIVE_ROOT,
    list_snapshots,
    load_snapshot,
    save_chain_snapshot,
)

IST = ZoneInfo("Asia/Kolkata")


def _synthetic_chain():
    return [
        {
            "strike": 24000,
            "option_type": "CE",
            "ltp": 120.5,
            "oi": 150000,
            "iv": 14.2,
        },
        {
            "strike": 24000,
            "option_type": "PE",
            "ltp": 95.0,
            "oi": 180000,
            "iv": 15.1,
        },
        {
            "strike": 24050,
            "option_type": "CE",
            "ltp": 98.0,
            "oi": 90000,
            "iv": 13.8,
        },
    ]


class OptionsChainArchiveTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._archive_root = Path(self._tmpdir.name) / "options_chain"
        self._patch = unittest.mock.patch(
            "app.options_chain_archive.ARCHIVE_ROOT",
            self._archive_root,
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmpdir.cleanup()

    def test_save_chain_snapshot_creates_parquet(self):
        when = datetime(2026, 6, 15, 9, 20, tzinfo=IST)
        path = save_chain_snapshot("NIFTY", _synthetic_chain(), snapshot_at=when)
        self.assertTrue(path.exists())
        self.assertEqual(path.name, "NIFTY.parquet")
        self.assertEqual(path.parent.name, "2026-06-15")

    def test_list_snapshots_metadata(self):
        when = datetime(2026, 6, 15, 9, 20, tzinfo=IST)
        save_chain_snapshot("NIFTY", _synthetic_chain(), snapshot_at=when)
        rows = list_snapshots("NIFTY", days=7, as_of=when.date())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["index"], "NIFTY")
        self.assertEqual(rows[0]["row_count"], 3)
        self.assertIn("snapshot_at", rows[0])

    def test_load_snapshot_roundtrip(self):
        when = datetime(2026, 6, 14, 15, 25, tzinfo=IST)
        save_chain_snapshot("BANKNIFTY", _synthetic_chain(), snapshot_at=when)
        df = load_snapshot("BANKNIFTY", "2026-06-14")
        self.assertIsNotNone(df)
        self.assertEqual(len(df), 3)
        self.assertIn("index", df.columns)
        self.assertEqual(df["index"].iloc[0], "BANKNIFTY")

    def test_unsupported_index_raises(self):
        with self.assertRaises(ValueError):
            save_chain_snapshot("RELIANCE", _synthetic_chain())


if __name__ == "__main__":
    unittest.main()