"""
Kite Connect authentication helpers.

Retail Kite tokens expire at 6 AM IST daily. Refresh tokens are only available
to approved platforms — most personal accounts must re-login via browser.

This module automates everything *after* you log in:
  1. Starts a local callback server on your registered redirect URL
  2. Opens the Kite login page in your browser
  3. Captures request_token from the redirect automatically
  4. Exchanges it for access_token and persists to .env

One-time setup: set your Kite app's Redirect URL to one of (must match KITE_REDIRECT_URL in .env):
  http://127.0.0.1:8765/callback   # preferred — auto-login callback
  http://127.0.0.1:8765            # if portal rejects /callback path
  http://127.0.0.1                 # minimal form portal accepts; use --manual if needed

Postback URL is separate — requires real HTTPS (ngrok/tunnel). Leave blank for local paper trading.
"""

from __future__ import annotations

import os
import socket
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Callable, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv, set_key
from kiteconnect import KiteConnect
from kiteconnect.exceptions import TokenException

load_dotenv()

ENV_PATH = Path(".env")
DEFAULT_REDIRECT_URL = "http://127.0.0.1:8765/callback"
DEFAULT_CALLBACK_PORT = 8765


def _strip_env(value: Optional[str]) -> str:
    text = (value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1].strip()
    return text


def get_redirect_url() -> str:
    raw = os.getenv("KITE_REDIRECT_URL", DEFAULT_REDIRECT_URL).strip()
    return normalize_redirect_url(raw or DEFAULT_REDIRECT_URL)


def normalize_redirect_url(url: str) -> str:
    """Normalize localhost → 127.0.0.1 so browser redirect matches bind address."""
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "127.0.0.1").lower()
    if host == "localhost":
        host = "127.0.0.1"
    port = parsed.port or DEFAULT_CALLBACK_PORT
    path = parsed.path or "/callback"
    if not path.startswith("/"):
        path = f"/{path}"
    return f"http://{host}:{port}{path}"


def ensure_redirect_in_env(redirect_url: Optional[str] = None) -> str:
    """Persist KITE_REDIRECT_URL to .env when missing or stale."""
    redirect_url = normalize_redirect_url(redirect_url or get_redirect_url())
    current = normalize_redirect_url(os.getenv("KITE_REDIRECT_URL", DEFAULT_REDIRECT_URL))
    if current != redirect_url:
        ENV_PATH.touch(exist_ok=True)
        set_key(str(ENV_PATH), "KITE_REDIRECT_URL", redirect_url, quote_mode="never")
        os.environ["KITE_REDIRECT_URL"] = redirect_url
    return redirect_url


def _parse_redirect(url: str) -> tuple[str, int, str]:
    normalized = normalize_redirect_url(url)
    parsed = urlparse(normalized)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or DEFAULT_CALLBACK_PORT
    path = parsed.path or "/callback"
    if not path.startswith("/"):
        path = f"/{path}"
    return host, port, path


def _port_available(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
        return True
    except OSError:
        return False


def _port_owner_hint(port: int) -> str:
    try:
        import subprocess

        out = subprocess.check_output(
            ["netstat", "-ano"],
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        for line in out.splitlines():
            if f":{port}" in line and "LISTENING" in line.upper():
                parts = line.split()
                pid = parts[-1] if parts else "?"
                return f"PID {pid} (run: taskkill /PID {pid} /F)"
    except Exception:
        pass
    return "unknown process"


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class _LoginState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.status = "idle"
        self.message = ""
        self.request_token: Optional[str] = None
        self.error: Optional[str] = None
        self.user_name: Optional[str] = None
        self.started_at: Optional[float] = None
        self.completed_at: Optional[float] = None
        self.login_url: Optional[str] = None
        self.redirect_url: Optional[str] = None
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._exchange_started = False

    def reset(self) -> None:
        with self.lock:
            self.status = "idle"
            self.message = ""
            self.request_token = None
            self.error = None
            self.user_name = None
            self.started_at = None
            self.completed_at = None
            self.login_url = None
            self.redirect_url = None
            self._exchange_started = False

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "status": self.status,
                "message": self.message,
                "error": self.error,
                "user_name": self.user_name,
                "login_url": self.login_url,
                "redirect_url": self.redirect_url,
                "started_at": self.started_at,
                "completed_at": self.completed_at,
            }

    def stop_server(self) -> None:
        server = self._server
        if server:
            threading.Thread(target=server.shutdown, daemon=True).start()


login_state = _LoginState()


def save_tokens(
    access_token: str,
    refresh_token: str = "",
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
) -> None:
    ENV_PATH.touch(exist_ok=True)
    if api_key:
        set_key(str(ENV_PATH), "KITE_API_KEY", _strip_env(api_key), quote_mode="never")
    if api_secret:
        set_key(str(ENV_PATH), "KITE_API_SECRET", _strip_env(api_secret), quote_mode="never")
    set_key(str(ENV_PATH), "KITE_ACCESS_TOKEN", _strip_env(access_token), quote_mode="never")
    if refresh_token:
        set_key(str(ENV_PATH), "KITE_REFRESH_TOKEN", _strip_env(refresh_token), quote_mode="never")
    os.environ["KITE_ACCESS_TOKEN"] = _strip_env(access_token)
    if refresh_token:
        os.environ["KITE_REFRESH_TOKEN"] = _strip_env(refresh_token)
    try:
        from .token_manager import get_token_manager

        mgr = get_token_manager()
        if mgr:
            mgr.reload_from_env()
    except Exception:
        pass


def exchange_request_token(
    request_token: str,
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
) -> dict:
    api_key = _strip_env(api_key or os.getenv("KITE_API_KEY", ""))
    api_secret = _strip_env(api_secret or os.getenv("KITE_API_SECRET", ""))
    request_token = _strip_env(request_token)
    if not api_key or not api_secret:
        raise RuntimeError("KITE_API_KEY and KITE_API_SECRET must be set in .env")
    if not request_token:
        raise RuntimeError("Empty request_token")

    kite = KiteConnect(api_key=api_key)
    session = kite.generate_session(request_token, api_secret=api_secret)
    access_token = session["access_token"]
    refresh_token = session.get("refresh_token", "") or ""

    save_tokens(access_token, refresh_token, api_key, api_secret)
    return session


def validate_access_token(
    api_key: Optional[str] = None,
    access_token: Optional[str] = None,
) -> tuple[bool, Optional[dict], Optional[str]]:
    api_key = _strip_env(api_key or os.getenv("KITE_API_KEY", ""))
    access_token = _strip_env(access_token or os.getenv("KITE_ACCESS_TOKEN", ""))
    if not api_key or not access_token:
        return False, None, "Missing API key or access token"

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    try:
        profile = kite.profile()
        return True, profile, None
    except TokenException as exc:
        return False, None, str(exc)
    except Exception as exc:
        return False, None, str(exc)


def preflight_login_check(
    *,
    redirect_url: Optional[str] = None,
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
) -> dict:
    """
    Validate credentials + callback port before opening the browser.
    Returns redirect_url, login_url, port, path for display.
    """
    api_key = _strip_env(api_key or os.getenv("KITE_API_KEY", ""))
    api_secret = _strip_env(api_secret or os.getenv("KITE_API_SECRET", ""))
    if not api_key:
        raise RuntimeError("KITE_API_KEY missing in .env")
    if not api_secret:
        raise RuntimeError("KITE_API_SECRET missing in .env")

    redirect_url = ensure_redirect_in_env(redirect_url)
    host, port, path = _parse_redirect(redirect_url)
    bind_host = "127.0.0.1" if host in {"localhost", "127.0.0.1"} else host

    if not _port_available(bind_host, port):
        owner = _port_owner_hint(port)
        raise RuntimeError(
            f"Callback port {port} is already in use ({owner}). "
            f"Stop that process, then retry. Do not change the port unless you also update "
            f"the Redirect URL in the Kite developer console to match KITE_REDIRECT_URL."
        )

    kite = KiteConnect(api_key=api_key)
    return {
        "redirect_url": redirect_url,
        "login_url": kite.login_url(),
        "bind_host": bind_host,
        "port": port,
        "path": path,
    }


def _make_handler(
    expected_path: str,
    on_token: Callable[[str], Tuple[bool, str]],
    on_error: Callable[[str], None],
):
    class CallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            return

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            has_token = bool((params.get("request_token") or [None])[0])

            path_ok = (
                parsed.path == expected_path
                or (expected_path != "/" and parsed.path.rstrip("/") == expected_path.rstrip("/"))
                or parsed.path in {"/", ""}
                or has_token
            )
            if not path_ok:
                self.send_response(404)
                self.end_headers()
                return

            with login_state.lock:
                if login_state._exchange_started or login_state.request_token:
                    self._respond("Login already captured. You can close this tab.", success=True)
                    return

            status = (params.get("status") or [""])[0]
            if status and status != "success":
                message = (params.get("message") or ["Kite login failed"])[0]
                on_error(message)
                self._respond(message, success=False)
                return

            request_token = (params.get("request_token") or [None])[0]
            if not request_token:
                self._respond(
                    "No request_token in redirect URL. "
                    "Ensure Kite developer console Redirect URL matches exactly: "
                    f"{login_state.redirect_url or expected_path}",
                    success=False,
                )
                return

            with login_state.lock:
                login_state._exchange_started = True

            success, message = on_token(request_token)
            self._respond(message, success=success)

        def do_HEAD(self) -> None:
            self.send_response(200)
            self.end_headers()

        def _respond(self, message: str, success: bool) -> None:
            color = "#10b981" if success else "#ef4444"
            body = f"""<!DOCTYPE html>
<html><head><title>Kite Login</title></head>
<body style="font-family:sans-serif;background:#09090b;color:#e4e4e7;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
<div style="text-align:center;padding:32px;border:1px solid #27272a;border-radius:16px;background:#18181b">
<h1 style="color:{color}">{'Success' if success else 'Error'}</h1>
<p>{message}</p>
<p style="color:#a1a1aa;font-size:0.875rem">Return to AG Quant terminal.</p>
</div></body></html>"""
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return CallbackHandler


def start_auto_login(
    *,
    open_browser: bool = True,
    timeout_seconds: int = 180,
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
) -> dict:
    """
    Start browser-based login with automatic request_token capture.
    Blocks until success, timeout, or error.
    """
    checks = preflight_login_check(api_key=api_key, api_secret=api_secret)
    redirect_url = checks["redirect_url"]
    bind_host = checks["bind_host"]
    port = checks["port"]
    path = checks["path"]
    login_url = checks["login_url"]

    api_key = _strip_env(api_key or os.getenv("KITE_API_KEY", ""))
    api_secret = _strip_env(api_secret or os.getenv("KITE_API_SECRET", ""))

    with login_state.lock:
        if login_state.status == "waiting":
            raise RuntimeError("Login already in progress")
        login_state.reset()
        login_state.status = "waiting"
        login_state.message = "Waiting for Zerodha login in browser..."
        login_state.login_url = login_url
        login_state.redirect_url = redirect_url
        login_state.started_at = time.time()

    done = threading.Event()
    result: dict = {"success": False}

    def on_token(token: str) -> Tuple[bool, str]:
        try:
            session = exchange_request_token(token, api_key, api_secret)
            with login_state.lock:
                login_state.status = "success"
                login_state.message = "Access token saved to .env"
                login_state.request_token = token
                login_state.user_name = session.get("user_name")
                login_state.completed_at = time.time()
            result["success"] = True
            result["session"] = session
            return True, "Login captured. You can close this tab."
        except Exception as exc:
            err = str(exc)
            with login_state.lock:
                login_state.status = "error"
                login_state.error = err
                login_state.message = f"Token exchange failed: {err}"
                login_state.completed_at = time.time()
            result["error"] = err
            hint = ""
            if "expired" in err.lower() or "invalid" in err.lower():
                hint = " Request tokens are single-use — run generate_token.py again and log in once."
            return False, f"Token exchange failed: {err}.{hint}"
        finally:
            done.set()
            login_state.stop_server()

    def on_error(message: str) -> None:
        with login_state.lock:
            login_state.status = "error"
            login_state.error = message
            login_state.message = message
            login_state.completed_at = time.time()
        result["error"] = message
        done.set()
        login_state.stop_server()

    handler = _make_handler(path, on_token, on_error)
    server = ThreadedHTTPServer((bind_host, port), handler)
    login_state._server = server
    login_state._thread = threading.Thread(target=server.serve_forever, daemon=True)
    login_state._thread.start()

    print("\n" + "=" * 60)
    print("KITE AUTO-LOGIN")
    print("=" * 60)
    print(f"Redirect URL : {redirect_url}")
    print(f"Listening on : http://{bind_host}:{port}{path}")
    print(f"Login URL    : {login_url}")
    print("Complete Zerodha login in the browser (2FA required once).")
    print("=" * 60 + "\n")

    if open_browser:
        webbrowser.open(login_url)

    if not done.wait(timeout=timeout_seconds):
        with login_state.lock:
            login_state.status = "error"
            login_state.error = "Login timed out"
            login_state.message = f"No callback received within {timeout_seconds}s"
            login_state.completed_at = time.time()
        login_state.stop_server()
        raise TimeoutError(
            f"Kite login timed out after {timeout_seconds}s. "
            f"No callback hit {redirect_url}. "
            "Check: (1) Redirect URL in Kite developer console matches exactly, "
            "(2) you completed login, (3) port is not blocked by firewall."
        )

    if not result.get("success"):
        raise RuntimeError(result.get("error", "Kite login failed"))

    return result["session"]


def start_auto_login_async(
    *,
    open_browser: bool = True,
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
) -> dict:
    """Non-blocking login for API/dashboard use. Poll get_login_status()."""

    def _worker() -> None:
        try:
            start_auto_login(
                open_browser=open_browser,
                timeout_seconds=int(os.getenv("KITE_LOGIN_TIMEOUT", "180")),
                api_key=api_key,
                api_secret=api_secret,
            )
        except Exception as exc:
            with login_state.lock:
                if login_state.status == "waiting":
                    login_state.status = "error"
                    login_state.error = str(exc)
                    login_state.message = str(exc)
                    login_state.completed_at = time.time()
            login_state.stop_server()

    with login_state.lock:
        if login_state.status == "waiting":
            return login_state.snapshot()

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    time.sleep(0.3)
    return login_state.snapshot()


def get_login_status() -> dict:
    return login_state.snapshot()