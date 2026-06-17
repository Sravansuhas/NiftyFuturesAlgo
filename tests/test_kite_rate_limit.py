import sys
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.kite_rate_limit import OrderBurstTracker


class OrderBurstTrackerTests(unittest.TestCase):
    def test_acquire_within_limit(self):
        tracker = OrderBurstTracker(max_per_10s=3)
        for _ in range(3):
            allowed, count = tracker.try_acquire()
            self.assertTrue(allowed)
            self.assertLessEqual(count, 3)

    def test_rejects_when_burst_exceeded(self):
        tracker = OrderBurstTracker(max_per_10s=2)
        self.assertTrue(tracker.try_acquire()[0])
        self.assertTrue(tracker.try_acquire()[0])
        allowed, count = tracker.try_acquire()
        self.assertFalse(allowed)
        self.assertEqual(count, 2)

    def test_orders_last_10s_property(self):
        tracker = OrderBurstTracker(max_per_10s=5)
        tracker.try_acquire()
        tracker.try_acquire()
        self.assertEqual(tracker.orders_last_10s, 2)

    def test_prunes_old_timestamps(self):
        tracker = OrderBurstTracker(max_per_10s=2)
        tracker._timestamps.append(time.time() - 15)
        tracker._timestamps.append(time.time())
        self.assertEqual(tracker.orders_last_10s, 1)


if __name__ == "__main__":
    unittest.main()