import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.kite_connect_rules import (
    faq_checklist,
    is_pre_token_flush_window,
    is_safe_to_generate_token,
    session_guidance,
)


class KiteConnectRulesTests(unittest.TestCase):
    def test_pre_flush_window_0630(self):
        at = datetime(2026, 6, 12, 6, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
        self.assertTrue(is_pre_token_flush_window(at))
        self.assertFalse(is_safe_to_generate_token(at))

    def test_safe_after_0735(self):
        at = datetime(2026, 6, 12, 8, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        self.assertFalse(is_pre_token_flush_window(at))
        self.assertTrue(is_safe_to_generate_token(at))

    def test_session_guidance_has_token_note(self):
        g = session_guidance()
        self.assertIn("token_note", g)
        self.assertIn("request_token_note", g)

    def test_faq_checklist_nonempty(self):
        items = faq_checklist()
        self.assertGreaterEqual(len(items), 5)
        self.assertTrue(any("07:35" in x for x in items))


if __name__ == "__main__":
    unittest.main()