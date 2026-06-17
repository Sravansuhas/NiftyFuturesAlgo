"""Portal trading controls — runtime toggles without restart."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import trading_controls as tc


class TradingControlsTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._controls_path = Path(self._tmpdir.name) / "trading_controls.json"
        self._patch_file = patch.object(tc, "CONTROLS_FILE", self._controls_path)
        self._patch_file.start()

    def tearDown(self):
        self._patch_file.stop()
        self._tmpdir.cleanup()

    def test_defaults_follow_yaml_and_env_when_no_portal_file(self):
        with patch.dict(os.environ, {"OPTIONS_TRADING_ENABLED": "false"}, clear=False):
            with patch(
                "app.config_loader.get_options_config",
                return_value={"trading_enabled": True},
            ):
                self.assertFalse(tc.effective_options_trading_enabled())

    def test_portal_override_takes_precedence(self):
        tc.save_controls({"options_trading_enabled": True})
        with patch.dict(os.environ, {"OPTIONS_TRADING_ENABLED": "false"}, clear=False):
            self.assertTrue(tc.effective_options_trading_enabled())

    def test_patch_applies_immediately_without_restart(self):
        result = tc.update_trading_controls({"options_trading_enabled": True})
        self.assertTrue(result["success"])
        self.assertEqual(os.environ.get("OPTIONS_TRADING_ENABLED"), "true")
        self.assertTrue(tc.effective_options_trading_enabled())
        with open(self._controls_path, encoding="utf-8") as fh:
            persisted = json.load(fh)
        self.assertTrue(persisted["options_trading_enabled"])

    def test_reset_clears_portal_overrides(self):
        tc.update_trading_controls({"options_trading_enabled": True})
        reset = tc.reset_trading_controls()
        self.assertTrue(reset["success"])
        self.assertFalse(self._controls_path.exists())

    def test_options_eod_flatten_portal_toggle(self):
        tc.update_trading_controls({"options_eod_flatten_enabled": False})
        self.assertFalse(tc.effective_options_eod_flatten_enabled())
        self.assertEqual(os.environ.get("OPTIONS_EOD_FLATTEN"), "false")


if __name__ == "__main__":
    unittest.main()