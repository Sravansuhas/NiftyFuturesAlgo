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
from typing import Any, Dict

logger = logging.getLogger("NiftyFuturesAlgo.Diagnostics")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | [DIAG] %(message)s",
        datefmt="%H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def log_signal_rejected(reason: str, details: Dict[str, Any] = None):
    """Log why a potential trade signal was rejected.
    
    These are very frequent during closed hours or quiet markets.
    We use DEBUG level so the terminal isn't flooded. 
    The dashboard can still show them if needed.
    """
    msg = f"Signal REJECTED → {reason}"
    if details:
        msg += f" | {details}"
    logger.debug(msg)  # Changed from INFO to DEBUG to reduce terminal noise


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
