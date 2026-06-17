import sys
import threading
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.candle_builder import CandleBuilder


IST = ZoneInfo("Asia/Kolkata")
TOKEN = 256265


def _ist(y, m, d, h, mi, s=0):
    return datetime(y, m, d, h, mi, s, tzinfo=IST)


class CandleBuilderTests(unittest.TestCase):
    def setUp(self):
        self.builder = CandleBuilder(intervals=["1m", "5m", "15m"], max_candles_per_token=5)

    def test_ohlcv_aggregation_within_bucket(self):
        t0 = _ist(2026, 6, 15, 9, 15, 10)
        self.builder.on_tick(TOKEN, 100.0, t0, volume=1000, oi=50000)
        self.builder.on_tick(TOKEN, 102.0, _ist(2026, 6, 15, 9, 15, 20), volume=1100, oi=50100)
        self.builder.on_tick(TOKEN, 99.0, _ist(2026, 6, 15, 9, 15, 40), volume=1250, oi=50050)

        candle = self.builder.get_latest_candle(TOKEN, "5m")
        self.assertIsNotNone(candle)
        self.assertEqual(candle["open"], 100.0)
        self.assertEqual(candle["high"], 102.0)
        self.assertEqual(candle["low"], 99.0)
        self.assertEqual(candle["close"], 99.0)
        self.assertEqual(candle["volume"], 250)
        self.assertEqual(candle["oi_open"], 50000)
        self.assertEqual(candle["oi_close"], 50050)
        self.assertEqual(candle["oi_delta"], 50)

    def test_ist_bucket_completion_on_roll(self):
        self.builder.on_tick(TOKEN, 100.0, _ist(2026, 6, 15, 9, 15, 0), volume=100, oi=1000)
        completed, forming = self.builder.on_tick(
            TOKEN, 101.0, _ist(2026, 6, 15, 9, 20, 0), volume=200, oi=1100
        )

        self.assertIn("5m", completed)
        self.assertEqual(completed["5m"]["open_time"], _ist(2026, 6, 15, 9, 15, 0))
        self.assertEqual(completed["5m"]["close"], 100.0)
        self.assertIn("5m", forming)
        self.assertEqual(forming["5m"]["open_time"], _ist(2026, 6, 15, 9, 20, 0))
        self.assertEqual(forming["5m"]["open"], 101.0)

        candles = self.builder.get_candles(TOKEN, "5m", n=10)
        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0]["open_time"], _ist(2026, 6, 15, 9, 15, 0))

    def test_15m_alignment(self):
        self.builder.on_tick(TOKEN, 200.0, _ist(2026, 6, 15, 9, 17, 0))
        candle = self.builder.get_latest_candle(TOKEN, "15m")
        self.assertEqual(candle["open_time"], _ist(2026, 6, 15, 9, 15, 0))

        completed, _ = self.builder.on_tick(TOKEN, 201.0, _ist(2026, 6, 15, 9, 30, 0))
        self.assertIn("15m", completed)
        self.assertEqual(completed["15m"]["open_time"], _ist(2026, 6, 15, 9, 15, 0))

    def test_get_oi_delta(self):
        self.builder.on_tick(TOKEN, 100.0, _ist(2026, 6, 15, 9, 15, 0), oi=10000)
        self.builder.on_tick(TOKEN, 101.0, _ist(2026, 6, 15, 9, 16, 0), oi=10300)
        self.assertEqual(self.builder.get_oi_delta(TOKEN, "5m"), 300)

    def test_rolling_max_candles_per_token(self):
        builder = CandleBuilder(intervals=["1m"], max_candles_per_token=3)
        base = _ist(2026, 6, 15, 9, 15, 0)
        for minute in range(5):
            builder.on_tick(TOKEN, 100.0 + minute, base.replace(minute=15 + minute))

        candles = builder.get_candles(TOKEN, "1m", n=10)
        self.assertEqual(len(candles), 3)
        self.assertEqual(candles[0]["open_time"], _ist(2026, 6, 15, 9, 16, 0))
        self.assertEqual(candles[-1]["open_time"], _ist(2026, 6, 15, 9, 18, 0))

    def test_naive_timestamp_treated_as_ist(self):
        naive = datetime(2026, 6, 15, 9, 15, 0)
        self.builder.on_tick(TOKEN, 50.0, naive)
        candle = self.builder.get_latest_candle(TOKEN, "5m")
        self.assertEqual(candle["open_time"].tzinfo.key, "Asia/Kolkata")

    def test_thread_safe_concurrent_ticks(self):
        errors = []

        def worker(offset: float):
            try:
                for i in range(50):
                    ts = _ist(2026, 6, 15, 9, 15, 0).replace(second=i % 59)
                    self.builder.on_tick(TOKEN, 100.0 + offset + i * 0.01, ts, volume=1000 + i)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        self.assertEqual(errors, [])
        candle = self.builder.get_latest_candle(TOKEN, "1m")
        self.assertIsNotNone(candle)
        self.assertGreater(float(candle["high"]), float(candle["low"]))


if __name__ == "__main__":
    unittest.main()