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


def test_get_lot_size_and_exchange_fallback():
    mgr = InstrumentsManager()
    assert mgr.get_lot_size("NIFTY") == 65
    assert mgr.get_lot_size("BANKNIFTY") == 30
    assert mgr.get_lot_size("SENSEX") == 20
    assert mgr.get_exchange("NIFTY") == "NFO"
    assert mgr.get_exchange("BANKNIFTY") == "NFO"
    assert mgr.get_exchange("SENSEX") == "BFO"


def test_bind_does_not_reload_instruments_for_new_client_same_token():
    class _FakeKite:
        def __init__(self, token: str):
            self.access_token = token

    mgr = InstrumentsManager()
    mgr._last_loaded = datetime(2026, 6, 17)
    mgr._instruments = {"NFO": [{"tradingsymbol": "NIFTY26JUNFUT"}]}

    first = _FakeKite("abc123")
    second = _FakeKite("abc123")
    mgr.bind(first, force=False)
    loaded_after_first = mgr._last_loaded
    mgr.bind(second, force=False)
    assert mgr._last_loaded == loaded_after_first


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