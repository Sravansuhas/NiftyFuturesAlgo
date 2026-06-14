"""
Rolling trade expectancy from trade_ledger — feeds FO_ROLLING_EDGE_HALT.

Blocks new entries when the mean net P&L of the last N closed trades is negative.
When the ledger has fewer than min_trades samples (common in early paper runs), the
halt is not applied so trading can continue while history accumulates.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Union

from .trade_ledger import TradeLedger, trade_ledger

DEFAULT_WINDOW = 10
DEFAULT_MIN_TRADES = 10

# Events that may carry realized P&L (newest trade.closed preferred).
_PNL_EVENT_TYPES = frozenset({"trade.closed", "order.exit"})


def extract_recent_pnls(
    events: Sequence[Dict[str, Any]],
    *,
    symbol: Optional[str] = None,
) -> List[float]:
    """
    Pull realized P&L values from ledger events in chronological order.

    Skips events without a numeric realized_pnl. Optional symbol filter matches
    normalized index keys (NIFTY, BANKNIFTY, SENSEX) or full tradingsymbol.
    """
    pnls: List[float] = []
    sym_key = _normalize_symbol(symbol) if symbol else None

    for event in events:
        if event.get("event_type") not in _PNL_EVENT_TYPES:
            continue
        payload = event.get("payload") or {}
        raw_pnl = payload.get("realized_pnl")
        if raw_pnl is None:
            raw_pnl = payload.get("net_pnl")
        if raw_pnl is None:
            continue
        try:
            pnl = float(raw_pnl)
        except (TypeError, ValueError):
            continue

        if sym_key:
            event_sym = _normalize_symbol(payload.get("symbol"))
            if event_sym != sym_key:
                continue

        pnls.append(pnl)

    return pnls


def rolling_expectancy(pnls: Sequence[float], window: int = DEFAULT_WINDOW) -> Optional[float]:
    """Mean P&L over the last *window* trades. None when there are no trades."""
    if not pnls or window <= 0:
        return None
    sample = list(pnls)[-window:]
    return sum(sample) / len(sample)


def assess_rolling_edge(
    ledger: Union[TradeLedger, None] = None,
    *,
    window: int = DEFAULT_WINDOW,
    min_trades: int = DEFAULT_MIN_TRADES,
    symbol: Optional[str] = None,
    floor: float = 0.0,
) -> Dict[str, Any]:
    """
    Evaluate rolling edge for FO rules context.

    Returns keys used by indian_fo_rules.json FO_ROLLING_EDGE_HALT:
      rolling_expectancy, rolling_edge_trade_count, rolling_edge_sufficient,
      rolling_edge_halt
    """
    ledger = ledger or trade_ledger
    window = max(1, int(window))
    min_trades = max(1, int(min_trades))
    tail_n = max(window, min_trades) * 4

    events = ledger.tail(tail_n)
    pnls = extract_recent_pnls(events, symbol=symbol)
    window_pnls = pnls[-window:] if pnls else []
    trade_count = len(window_pnls)
    expectancy = rolling_expectancy(pnls, window=window)

    sufficient = trade_count >= min_trades
    halt = sufficient and expectancy is not None and expectancy < floor

    return {
        "rolling_expectancy": round(expectancy, 2) if expectancy is not None else 0.0,
        "rolling_edge_trade_count": trade_count,
        "rolling_edge_sufficient": sufficient,
        "rolling_edge_halt": halt,
    }


def _normalize_symbol(symbol: Optional[str]) -> str:
    s = (symbol or "").upper()
    if "BANKNIFTY" in s or "BNF" in s:
        return "BANKNIFTY"
    if "SENSEX" in s:
        return "SENSEX"
    if "NIFTY" in s:
        return "NIFTY"
    return s