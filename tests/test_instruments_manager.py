from datetime import datetime

from app.instruments_manager import (
    _fallback_tradingsymbol,
    ltp_key,
    InstrumentsManager,
)


def test_ltp_key_uses_exchange_tradingsymbol():
    assert ltp_key("NIFTY26JUNFUT", "NFO") == "NFO:NIFTY26JUNFUT"
    assert ltp_key("SENSEX26JUNFUT", "BFO") == "BFO:SENSEX26JUNFUT"


def test_fallback_tradingsymbol_uses_current_month():
    assert _fallback_tradingsymbol("NIFTY", datetime(2026, 6, 11)) == "NIFTY26JUNFUT"
    assert _fallback_tradingsymbol("BANKNIFTY", datetime(2026, 7, 2)) == "BANKNIFTY26JULFUT"


def test_filter_unexpired_futures_skips_expired():
    mgr = InstrumentsManager()
    futures = [
        {"tradingsymbol": "NIFTY26MAYFUT", "expiry": datetime(2026, 5, 29)},
        {"tradingsymbol": "NIFTY26JUNFUT", "expiry": datetime(2026, 6, 26)},
        {"tradingsymbol": "NIFTY26JULFUT", "expiry": datetime(2026, 7, 31)},
    ]
    live = mgr._filter_unexpired_futures(futures)
    symbols = [f["tradingsymbol"] for f in live]
    assert "NIFTY26MAYFUT" not in symbols
    assert "NIFTY26JUNFUT" in symbols
    assert "NIFTY26JULFUT" in symbols