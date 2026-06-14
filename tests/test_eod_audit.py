import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtesting.bse_eod_client import BseEodClient
from backtesting.eod_audit import compare_eod_bars, official_daily_ohlc, run_eod_audit
from backtesting.nse_eod_client import FoBhavRow


UDIFF_SAMPLE = """TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4
2025-03-12,2025-03-12,FO,BSE,IDF,1,,SENSEX,,2025-03-18,2025-03-18,,,SENSEX25318FUT,74400.15,74430.35,73663.95,74140.25,74140.25,74000.00,74029.76,74140.25,9280,100,15380,1000,100,F1,20,,,,,
2025-03-12,2025-03-12,FO,BSE,IDF,2,,SENSEX,,2025-03-25,2025-03-25,,,SENSEX25MARFUT,74775.10,74775.10,73800.00,74236.70,74236.70,74500.00,74029.76,74236.70,5200,50,2300,500,50,F1,20,,,,,
2025-03-12,2025-03-12,FO,BSE,IDO,3,,SENSEX,,2025-03-18,2025-03-18,74000,CE,SENSEX2531874000CE,700.00,780.95,276.00,478.50,478.50,617.70,74029.76,478.50,265880,0,8984980,1000,100,F1,20,,,,,
"""

LEGACY_MS_SAMPLE = """Market Summary Date,Session ID,Series ID,Series Code,Product Type,Product Code,Asset Code,Expiry Date,Strike Price,Option Type (Call/Put),Previous Close Price,Open Price,High Price,Low Price,Close Price,Total Traded Quantity,Total Traded Value (in Thousands)(absolute),Average Traded Price,No. of Trades,Filler1,Filler2,Open Interest,Underlying Asset Close Price
12 Mar 2025,0,1,SENSEX25318FUT,IF,BSXFUT,BSX,18 Mar 2025,0,,74000,74400.15,74430.35,73663.95,74140.25,15380,1000,74140,100,0,0,9280,74029.76
12 Mar 2025,0,2,SENSEX25MARFUT,IF,BSXFUT,BSX,25 Mar 2025,0,,74500,74775.10,74775.10,73800.00,74236.70,2300,500,74236,50,0,0,5200,74029.76
12 Mar 2025,0,3,SENSEX2531874000CE,IO,BSXOPT,BSX,18 Mar 2025,74000,CE,617.7,700.00,780.95,276.00,478.50,8984980,1000,74420,100,0,0,265880,74029.76
"""


class EodAuditTests(unittest.TestCase):
    def test_match_within_tolerance(self):
        cache = {"open": 100.0, "high": 105.0, "low": 99.0, "close": 103.0, "volume": 1000, "bar_count": 75}
        official = {"open": 100.05, "high": 105.02, "low": 99.01, "close": 103.0, "volume": 1010}
        result = compare_eod_bars(cache, official)
        self.assertEqual(result["status"], "match")

    def test_mismatch_on_price(self):
        cache = {"open": 100.0, "high": 105.0, "low": 99.0, "close": 110.0, "volume": 1000, "bar_count": 75}
        official = {"open": 100.0, "high": 105.0, "low": 99.0, "close": 103.0, "volume": 1000}
        result = compare_eod_bars(cache, official)
        self.assertEqual(result["status"], "mismatch")


class BseEodClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = BseEodClient(cache_dir=Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_udiff_sensex_futures(self):
        path = Path(self.tmp.name) / "udiff.csv"
        path.write_text(UDIFF_SAMPLE, encoding="utf-8")
        rows = self.client._rows_from_udiff(self.client.parse_fo_bhavcopy(path), "SENSEX")
        self.assertEqual(len(rows), 2)
        symbols = {r.tradingsymbol for r in rows}
        self.assertEqual(symbols, {"SENSEX25318FUT", "SENSEX25MARFUT"})
        front = max(rows, key=lambda r: r.open_interest)
        self.assertEqual(front.tradingsymbol, "SENSEX25318FUT")
        self.assertAlmostEqual(front.close, 74140.25)

    def test_parse_legacy_ms_sensex_futures(self):
        path = Path(self.tmp.name) / "legacy.csv"
        path.write_text(LEGACY_MS_SAMPLE, encoding="utf-8")
        rows = self.client._rows_from_legacy_ms(self.client.parse_fo_bhavcopy(path), "SENSEX")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].volume, 15380)

    def test_get_front_month_eod_from_cached_udiff(self):
        trade_date = date(2025, 3, 12)
        cached = self.client._cache_path(trade_date)
        cached.write_text(UDIFF_SAMPLE, encoding="utf-8")
        row = self.client.get_front_month_eod(trade_date, "SENSEX")
        self.assertIsNotNone(row)
        self.assertEqual(row.tradingsymbol, "SENSEX25318FUT")
        self.assertEqual(row.open_interest, 9280)

    @patch("backtesting.bse_eod_client.requests.Session")
    def test_download_fo_bhavcopy_uses_udiff_url(self, mock_session_cls):
        trade_date = date(2025, 3, 12)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = UDIFF_SAMPLE.encode("utf-8")
        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        mock_session_cls.return_value = mock_session

        path = self.client.download_fo_bhavcopy(trade_date, force_refresh=True)
        self.assertIsNotNone(path)
        mock_session.get.assert_called_once()
        called_url = mock_session.get.call_args[0][0]
        self.assertIn("BhavCopy_BSE_FO_0_0_0_20250312_F_0000.CSV", called_url)
        self.assertTrue(path.exists())

    @patch("backtesting.bse_eod_client.requests.Session")
    def test_download_fo_bhavcopy_falls_back_to_ms(self, mock_session_cls):
        trade_date = date(2025, 3, 12)
        miss = MagicMock(status_code=404, content=b"")
        hit = MagicMock(status_code=200, content=LEGACY_MS_SAMPLE.encode("utf-8"))
        mock_session = MagicMock()
        mock_session.get.side_effect = [miss, hit]
        mock_session_cls.return_value = mock_session

        path = self.client.download_fo_bhavcopy(trade_date, force_refresh=True)
        self.assertIsNotNone(path)
        self.assertEqual(mock_session.get.call_count, 2)
        fallback_url = mock_session.get.call_args_list[1][0][0]
        self.assertIn("MS_20250312-01.csv", fallback_url)


class SensexEodAuditIntegrationTests(unittest.TestCase):
    def test_official_daily_ohlc_sensex(self):
        mock_row = FoBhavRow(
            tradingsymbol="SENSEX25318FUT",
            underlying="SENSEX",
            open=74400.15,
            high=74430.35,
            low=73663.95,
            close=74140.25,
            settle=74140.25,
            volume=15380,
            open_interest=9280,
        )
        with patch("backtesting.bse_eod_client.bse_eod_client.get_front_month_eod", return_value=mock_row):
            result = official_daily_ohlc(date(2025, 3, 12), "SENSEX")
        self.assertIsNotNone(result)
        self.assertEqual(result["source"], "bse_bhavcopy")
        self.assertEqual(result["tradingsymbol"], "SENSEX25318FUT")
        self.assertAlmostEqual(result["close"], 74140.25)

    def test_run_eod_audit_includes_sensex_compare(self):
        trade_date = date(2025, 3, 12)
        cache_bar = {
            "tradingsymbol": "SENSEX25318FUT",
            "open": 74400.15,
            "high": 74430.35,
            "low": 73663.95,
            "close": 74140.25,
            "volume": 15380,
            "bar_count": 75,
            "source": "historical_cache",
        }
        official_bar = {
            "tradingsymbol": "SENSEX25318FUT",
            "open": 74400.15,
            "high": 74430.35,
            "low": 73663.95,
            "close": 74140.25,
            "settle": 74140.25,
            "volume": 15380,
            "open_interest": 9280,
            "source": "bse_bhavcopy",
        }
        with patch("backtesting.eod_audit.cache_daily_ohlc", return_value=cache_bar), patch(
            "backtesting.eod_audit.official_daily_ohlc", return_value=official_bar
        ), patch("backtesting.eod_audit.Path.exists", return_value=False):
            report = run_eod_audit(trade_date=trade_date, underlyings=("SENSEX",), save=False)

        sensex = report["indices"]["SENSEX"]
        self.assertEqual(sensex["status"], "match")
        self.assertNotEqual(sensex.get("reason"), "BSE — use BSE bhavcopy in phase 2")
        self.assertEqual(report["overall"], "healthy")


if __name__ == "__main__":
    unittest.main()