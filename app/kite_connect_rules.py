"""
Kite Connect operational rules — distilled from official FAQ + docs.

Sources:
- https://kite.trade/forum/discussion/4732/frequently-asked-questions-faqs
- https://kite.trade/forum/discussion/3224/session-expired
- https://kite.trade/forum/discussion/319/redirect-and-postback-url
- https://kite.trade/docs/connect/v3/exceptions/#api-rate-limit
"""

from __future__ import annotations

from datetime import datetime, time as dt_time
from typing import Any, Dict, List

from .market_calendar import IST, now_ist

# Official rate limits (requests per second)
RATE_LIMIT_QUOTE_RPS = 1.0
RATE_LIMIT_HISTORICAL_RPS = 3.0
RATE_LIMIT_DEFAULT_RPS = 10.0
RATE_LIMIT_ORDER_RPS = 10.0
# Safe rolling window — SEBI/broker burst guard (conservative retail target)
RATE_LIMIT_ORDERS_PER_10S = 80

# Token flush window (IST) — generate access token AFTER this window
TOKEN_FLUSH_START = dt_time(5, 30)
TOKEN_SAFE_AFTER = dt_time(7, 35)

# WebSocket limits per API key
WS_MAX_INSTRUMENTS_PER_CONN = 3000
WS_MAX_CONNECTIONS_PER_KEY = 3


def is_pre_token_flush_window(at: datetime | None = None) -> bool:
    """True during ~05:30–07:35 IST when yesterday's tokens may still be invalidating."""
    current = (at or now_ist()).astimezone(IST)
    t = current.time()
    return TOKEN_FLUSH_START <= t < TOKEN_SAFE_AFTER


def is_safe_to_generate_token(at: datetime | None = None) -> bool:
    """FAQ: generate access token after 07:35 IST on the trading day."""
    return not is_pre_token_flush_window(at)


def session_guidance(at: datetime | None = None) -> Dict[str, Any]:
    """Human-readable session tips for dashboard / CLI."""
    current = (at or now_ist()).astimezone(IST)
    pre_flush = is_pre_token_flush_window(current)
    return {
        "ist_now": current.strftime("%H:%M:%S IST"),
        "date_ist": current.strftime("%Y-%m-%d"),
        "pre_token_flush_window": pre_flush,
        "safe_to_generate_token": not pre_flush,
        "token_note": (
            "Wait until after 07:35 IST before generating today's access token."
            if pre_flush
            else "Safe window — generate token once, reuse all day until logout."
        ),
        "request_token_note": (
            "request_token is single-use and expires in ~2 minutes. "
            "Never call generate_session on every restart — store access_token in .env."
        ),
        "login_order_tip": (
            "For Kite Web + API same day: log in via generate_token.py first, "
            "then open Kite Web in the same browser (or tick 'Login to Kite Web also')."
        ),
        "postback_note": (
            "Postback URL needs HTTPS (ngrok for local). "
            "WebSocket order updates are enabled in this app when WS is on."
        ),
        "instruments_note": (
            "Refresh instrument master (NFO/BFO) each trading morning before 09:15."
        ),
    }


def faq_checklist() -> List[str]:
    return [
        "Access token: generate once per day after 07:35 IST; reuse from .env",
        "Request token: single-use ~2 min — do not re-exchange on every app restart",
        "Redirect URL: localhost OK (http://127.0.0.1:8765/callback)",
        "Postback URL: optional locally; use WS order updates or HTTPS tunnel",
        "TokenException (403): session expired — run generate_token.py, do not trade",
        "Instruments dump: refresh NFO/BFO each session (api.kite.trade/instruments)",
        "Quote API: max 1 req/sec | Historical: 3 req/sec | Orders: 10 req/sec",
        "WebSocket: up to 3000 instruments × 3 connections per API key",
        "Order updates via WS may arrive out of sequence — reconcile with orders()",
    ]


def on_token_exception(action: str = "api_call") -> None:
    """Central handler when Kite returns TokenException — disable trading."""
    try:
        from .state_machine import SystemState, state_machine
        state_machine.set_state(SystemState.TRADING_DISABLED)
    except Exception:
        pass
    try:
        from .token_manager import get_token_manager
        mgr = get_token_manager()
        if mgr:
            mgr.token_valid = False
            mgr.needs_relogin = True
    except Exception:
        pass