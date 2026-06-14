import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.kite_auth import (
    _make_handler,
    _parse_redirect,
    _strip_env,
    normalize_redirect_url,
    preflight_login_check,
)


class KiteAuthTests(unittest.TestCase):
    def test_strip_env_quotes(self):
        self.assertEqual(_strip_env("'abc'"), "abc")
        self.assertEqual(_strip_env('"abc"'), "abc")

    def test_normalize_localhost(self):
        self.assertEqual(
            normalize_redirect_url("http://localhost:8765/callback"),
            "http://127.0.0.1:8765/callback",
        )

    def test_parse_redirect_defaults(self):
        host, port, path = _parse_redirect("http://127.0.0.1:8765/callback")
        self.assertEqual(host, "127.0.0.1")
        self.assertEqual(port, 8765)
        self.assertEqual(path, "/callback")

    def test_callback_accepts_root_and_callback_paths(self):
        from http.server import HTTPServer

        host, port, path = _parse_redirect("http://127.0.0.1:8765/callback")
        captured = []

        def on_token(token: str):
            captured.append(token)
            return True, "ok"

        def on_error(msg: str) -> None:
            captured.append(("err", msg))

        handler = _make_handler(path, on_token, on_error)
        server = HTTPServer(("127.0.0.1", port), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        time.sleep(0.15)

        for route, query in (
            ("/callback", {"request_token": "tok1", "status": "success"}),
            ("/", {"request_token": "tok2", "status": "success"}),
        ):
            url = f"http://127.0.0.1:{port}{route}?{urlencode(query)}"
            resp = urlopen(url, timeout=3)
            self.assertEqual(resp.status, 200)

        server.shutdown()
        self.assertIn("tok1", captured)

    def test_preflight_requires_credentials(self):
        with patch.dict(os.environ, {"KITE_API_KEY": "", "KITE_API_SECRET": ""}, clear=False):
            with self.assertRaises(RuntimeError):
                preflight_login_check(api_key="", api_secret="")


if __name__ == "__main__":
    unittest.main()