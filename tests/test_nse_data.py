import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.nse_data import _parse_fii_dii_rows, fetch_fii_dii_flow


class FiiDiiParserTests(unittest.TestCase):
    def test_parse_new_category_array_format(self):
        rows = [
            {"category": "DII", "netValue": "5341.29", "date": "12-Jun-2026"},
            {"category": "FII/FPI", "netValue": "-1082.18", "date": "12-Jun-2026"},
        ]
        parsed = _parse_fii_dii_rows(rows)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["fii"], -1082.18)
        self.assertEqual(parsed["dii"], 5341.29)
        self.assertEqual(parsed["trade_date"], "12-Jun-2026")

    def test_parse_legacy_object_format(self):
        rows = {
            "fiiNetValue": "-900.5",
            "diiNetValue": "1200.25",
            "date": "11-Jun-2026",
        }
        parsed = _parse_fii_dii_rows(rows)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["fii"], -900.5)
        self.assertEqual(parsed["dii"], 1200.25)
        self.assertEqual(parsed["trade_date"], "11-Jun-2026")

    def test_parse_legacy_list_format(self):
        rows = [
            {
                "fiiNetValue": "450.0",
                "diiNetValue": "-100.0",
                "tradeDate": "10-Jun-2026",
            }
        ]
        parsed = _parse_fii_dii_rows(rows)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["fii"], 450.0)
        self.assertEqual(parsed["dii"], -100.0)
        self.assertEqual(parsed["trade_date"], "10-Jun-2026")

    def test_parse_category_array_picks_latest_date(self):
        rows = [
            {"category": "DII", "netValue": "100.0", "date": "10-Jun-2026"},
            {"category": "FII/FPI", "netValue": "-50.0", "date": "10-Jun-2026"},
            {"category": "DII", "netValue": "200.0", "date": "12-Jun-2026"},
            {"category": "FII/FPI", "netValue": "-75.0", "date": "12-Jun-2026"},
        ]
        parsed = _parse_fii_dii_rows(rows)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["trade_date"], "12-Jun-2026")
        self.assertEqual(parsed["fii"], -75.0)
        self.assertEqual(parsed["dii"], 200.0)

    @patch("app.nse_data._nse_session")
    def test_fetch_fii_dii_flow_new_api_payload(self, mock_session):
        session = MagicMock()
        mock_session.return_value = session
        session.get.return_value.raise_for_status = MagicMock()
        session.get.return_value.json.return_value = [
            {"category": "DII", "netValue": "5341.29", "date": "12-Jun-2026"},
            {"category": "FII/FPI", "netValue": "-1082.18", "date": "12-Jun-2026"},
        ]

        result = fetch_fii_dii_flow()
        self.assertTrue(result["available"])
        self.assertEqual(result["fii_net_crores"], -1082.18)
        self.assertEqual(result["dii_net_crores"], 5341.29)
        self.assertEqual(result["trade_date"], "12-Jun-2026")
        self.assertEqual(result["flow_bias"], "fii_selling")


if __name__ == "__main__":
    unittest.main()