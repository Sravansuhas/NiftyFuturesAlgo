from datetime import date
from unittest.mock import MagicMock, patch

from app.options_desk_tickers import _ticker_cache, get_index_option_tickers


def _clear_ticker_cache():
    _ticker_cache["payload"] = None
    _ticker_cache["ts"] = 0.0


def _mock_inst(index: str, option_type: str, strike: float, token: int) -> dict:
    return {
        "tradingsymbol": f"{index}26JUN{int(strike)}{option_type}",
        "instrument_token": token,
        "strike": strike,
        "exchange": "BFO" if index == "SENSEX" else "NFO",
        "option_type": option_type,
    }


def test_get_index_option_tickers_kite_unavailable():
    _clear_ticker_cache()
    payload = get_index_option_tickers(kite=None, ws_feed=None)
    assert payload["error"] == "kite_unavailable"
    assert payload["available"] is False
    assert set(payload["indices"].keys()) == {"NIFTY", "BANKNIFTY", "SENSEX"}


def test_get_index_option_tickers_rest_batch():
    _clear_ticker_cache()
    kite = MagicMock()
    expiry = date(2026, 6, 26)

    def quote_side_effect(keys):
        data = {}
        for key in keys:
            if key == "NSE:NIFTY 50":
                data[key] = {"last_price": 23105.5, "ohlc": {"close": 23080.0}, "net_change": 25.5}
            elif key == "NSE:NIFTY BANK":
                data[key] = {"last_price": 54820.0, "ohlc": {"close": 54700.0}, "net_change": 120.0}
            elif key == "BSE:SENSEX":
                data[key] = {"last_price": 76210.0, "ohlc": {"close": 76100.0}, "net_change": 110.0}
            elif key.endswith("CE"):
                data[key] = {"last_price": 182.5, "ohlc": {"close": 175.0}, "oi": 12000}
            elif key.endswith("PE"):
                data[key] = {"last_price": 165.0, "ohlc": {"close": 160.0}, "oi": 9800}
        return data

    kite.quote.side_effect = quote_side_effect

    with patch("app.options_desk_tickers.instruments_manager") as mgr, patch(
        "app.options_desk_tickers.options_chain_manager"
    ) as chain:
        mgr.bind = MagicMock()
        chain.bind = MagicMock()
        chain.set_spot_price = MagicMock()
        chain.resolve_expiry.return_value = expiry
        chain.get_atm_strike.side_effect = lambda idx, **_: {
            "NIFTY": 23100.0,
            "BANKNIFTY": 54800.0,
            "SENSEX": 76200.0,
        }[idx]

        def _option_inst(index, option_type, _expiry, strike):
            tokens = {
                ("NIFTY", "CE"): 101,
                ("NIFTY", "PE"): 102,
                ("BANKNIFTY", "CE"): 201,
                ("BANKNIFTY", "PE"): 202,
                ("SENSEX", "CE"): 301,
                ("SENSEX", "PE"): 302,
            }
            token = tokens[(index, option_type)]
            return _mock_inst(index, option_type, strike, token)

        mgr.get_option_instruments.side_effect = _option_inst

        payload = get_index_option_tickers(kite=kite, ws_feed=None)

    assert payload["available"] is True
    assert payload["error"] is None
    assert payload["indices"]["NIFTY"]["spot"] == 23105.5
    assert payload["indices"]["NIFTY"]["atm_strike"] == 23100.0
    assert payload["indices"]["NIFTY"]["ce"]["ltp"] == 182.5
    assert payload["indices"]["NIFTY"]["pe"]["oi"] == 9800
    assert payload["indices"]["NIFTY"]["data_source"] == "REST"
    assert kite.quote.call_count == 2


def test_get_index_option_tickers_prefers_ws_ltp():
    _clear_ticker_cache()
    kite = MagicMock()
    expiry = date(2026, 6, 26)
    kite.quote.return_value = {
        "NSE:NIFTY 50": {"last_price": 23105.5, "ohlc": {"close": 23080.0}},
        "NFO:NIFTY26JUN23100CE": {"last_price": 180.0, "ohlc": {"close": 175.0}, "oi": 5000},
        "NFO:NIFTY26JUN23100PE": {"last_price": 160.0, "ohlc": {"close": 155.0}, "oi": 4000},
    }

    ws = MagicMock()
    ws.get_last_price_with_age.side_effect = lambda token: {
        101: (195.25, 0.8),
        102: (158.0, 1.1),
    }[token]

    with patch("app.options_desk_tickers.instruments_manager") as mgr, patch(
        "app.options_desk_tickers.options_chain_manager"
    ) as chain:
        mgr.bind = MagicMock()
        chain.bind = MagicMock()
        chain.set_spot_price = MagicMock()
        chain.resolve_expiry.return_value = expiry
        chain.get_atm_strike.return_value = 23100.0
        mgr.get_option_instruments.side_effect = lambda idx, opt, _exp, strike: _mock_inst(
            idx,
            opt,
            strike,
            101 if opt == "CE" else 102,
        )

        payload = get_index_option_tickers(kite=kite, ws_feed=ws)

    nifty = payload["indices"]["NIFTY"]
    assert nifty["ce"]["ltp"] == 195.25
    assert nifty["pe"]["ltp"] == 158.0
    assert nifty["ce"]["data_source"] == "WS"
    assert nifty["pe"]["data_source"] == "WS"
    assert nifty["data_source"] == "WS"


def test_get_index_option_tickers_serves_cached_payload():
    _clear_ticker_cache()
    kite = MagicMock()
    kite.quote.return_value = {}
    with patch("app.options_desk_tickers.instruments_manager") as mgr, patch(
        "app.options_desk_tickers.options_chain_manager"
    ) as chain:
        mgr.bind = MagicMock()
        chain.bind = MagicMock()
        chain.set_spot_price = MagicMock()
        chain.resolve_expiry.return_value = None

        first = get_index_option_tickers(kite=kite, ws_feed=None)
        calls_after_first = kite.quote.call_count
        second = get_index_option_tickers(kite=kite, ws_feed=None)

    assert first is second
    assert calls_after_first >= 1
    assert kite.quote.call_count == calls_after_first