"""
Real-time diagnostic logging for live / paper trading.

This module exists because real algo trading fails in boring, expensive ways.

Real trader pain points observed across Zerodha users + algo communities:
- WebSocket/LTP delivering stale or wrong prices (especially Nifty spot & futures)
- Sudden rate limit hits or blackouts
- Token expiry + failed recon after disconnects
- Partial fills + position drift between internal state and broker
- No visibility into *why* the bot skipped a trade for 3 hours
- Running in silence until EOD, then discovering it did nothing (or too much)

Good diagnostics are not optional. They are survival equipment.
"""

import logging
import time
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger("NiftyFuturesAlgo.Diagnostics")

_rejection_throttle: Dict[str, float] = {}
_last_gate_summary: Dict[str, str] = {}
REJECTION_THROTTLE_SEC = 20.0


def _index_key(symbol: str) -> str:
    s = (symbol or "").upper()
    if "BANKNIFTY" in s or "BNF" in s:
        return "BANKNIFTY"
    if "SENSEX" in s:
        return "SENSEX"
    return "NIFTY"


def _humanize_rejection(reason: str, details: Optional[Dict[str, Any]] = None) -> str:
    details = details or {}
    if reason == "long_breakout_not_met":
        cur = details.get("current")
        prev = details.get("prev_high")
        buf = details.get("buffer")
        if cur is not None and prev is not None and buf is not None:
            need = float(prev) + float(buf)
            return f"Long breakout not met — LTP {cur} below band {need:.1f} (prev high + ATR buffer)"
        return "Long breakout not met — price below previous candle high + buffer"
    if reason == "short_breakout_not_met":
        cur = details.get("current")
        prev = details.get("prev_low")
        buf = details.get("buffer")
        if cur is not None and prev is not None and buf is not None:
            need = float(prev) - float(buf)
            return f"Short breakout not met — LTP {cur} above band {need:.1f} (prev low − buffer)"
        return "Short breakout not met — price above previous candle low − buffer"
    if reason in ("max_trades_per_day_reached", "max_trades_quality_gate", "max_trades_hard_ceiling"):
        used = details.get("trades_today")
        cap = details.get("effective_cap")
        hard = details.get("hard_ceiling")
        score = details.get("regime_score")
        reasons = details.get("budget_reasons") or []
        parts = []
        if used is not None and cap is not None:
            parts.append(f"{used}/{cap} trades used")
        if hard is not None and reason == "max_trades_hard_ceiling":
            parts.append(f"hard ceiling {hard}")
        if score is not None:
            parts.append(f"regime score {score}")
        if reasons:
            parts.append("; ".join(reasons[:3]))
        if parts:
            return "Trade budget full — " + " · ".join(parts)
        return "Trade budget full — max trades for today"
    return reason.replace("_", " ")


def get_gate_summary(index_or_symbol: str) -> Optional[str]:
    """Latest human-readable gate reason for dashboard (NIFTY / BANKNIFTY / SENSEX)."""
    key = _index_key(index_or_symbol)
    return _last_gate_summary.get(key)
if not logger.handlers:
    import os
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | [DIAG] %(message)s",
        datefmt="%H:%M:%S"
    )
    handler.setFormatter(formatter)
    handler.setLevel(
        logging.INFO
        if os.getenv("DIAG_VERBOSE", "").strip().lower() in {"1", "true", "yes", "on"}
        else logging.WARNING
    )
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)


def log_signal_rejected(
    reason: str,
    details: Dict[str, Any] = None,
    symbol: str = None,
):
    """Log why a potential trade signal was rejected.

    Throttled writes to trade_ledger feed the dashboard Execution Rationale panel.
    """
    details = details or {}
    sym = symbol or details.get("symbol") or "UNKNOWN"
    index_key = _index_key(sym)
    summary = _humanize_rejection(reason, details)
    _last_gate_summary[index_key] = summary

    msg = f"Signal REJECTED → {reason}"
    if details:
        msg += f" | {details}"
    logger.debug(msg)

    throttle_key = f"{index_key}:{reason}"
    now = time.time()
    if now - _rejection_throttle.get(throttle_key, 0) < REJECTION_THROTTLE_SEC:
        return
    _rejection_throttle[throttle_key] = now

    try:
        from app.trade_ledger import trade_ledger

        regime = details.get("regime")
        regime_label = regime.get("volatility") if isinstance(regime, dict) else regime
        trade_ledger.record(
            "signal.rejected",
            {
                "reason": reason,
                "message": summary,
                "symbol": sym,
                "index": index_key,
                "price": details.get("current") or details.get("ltp"),
                "regime": regime_label,
                "risk_mult": details.get("risk_mult"),
            },
        )
    except Exception:
        pass


def log_signal_accepted(side: str, price: float, details: Dict[str, Any] = None):
    """Log when a signal passes all filters and will attempt entry."""
    msg = f"Signal ACCEPTED → {side} @ {price:.2f}"
    if details:
        msg += f" | {details}"
    logger.warning(msg)  # Warning level so it stands out in logs


def log_ltp_issue(error: str, symbol: str = "NIFTY"):
    """Log problems fetching live price."""
    logger.warning(f"LTP fetch issue for {symbol}: {error}")


def log_reconciliation(status: str, details: Dict[str, Any] = None):
    """Log broker reconciliation results."""
    msg = f"Reconciliation: {status}"
    if details:
        msg += f" | {details}"
    logger.info(msg)


def log_daily_reset():
    logger.info("=== NEW TRADING DAY — Internal counters reset ===")


def log_expiry_warning(days_to_expiry: int):
    if days_to_expiry <= 1:
        logger.warning(f"⚠️  EXPIRY DAY or T-1 — Extreme caution mode active")
    else:
        logger.info(f"Expiry in {days_to_expiry} days — monitoring")


def log_system_status(status_dict: Dict[str, Any]):
    """Print a clean periodic health check."""
    lines = ["--- SYSTEM STATUS ---"]
    for k, v in status_dict.items():
        lines.append(f"  {k}: {v}")
    lines.append("---------------------")
    logger.info("\n".join(lines))


def log_risk_block(reason: str, details: Dict = None):
    msg = f"RISK GATE BLOCKED order → {reason}"
    if details:
        msg += f" | {details}"
    logger.warning(msg)


def log_paper_mode_start(params_summary: str):
    # Keep this very short. Full dataclass dump was too noisy on every start.
    logger.info("Paper trading session started (full params visible in dashboard)")
