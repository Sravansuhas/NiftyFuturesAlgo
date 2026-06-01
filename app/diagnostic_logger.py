"""
Diagnostic Logger for NiftyFuturesAlgo

Best practice for live algo trading observability.

Purpose:
- Every run creates its own timestamped log file in logs/run_YYYYMMDD_HHMMSS.log
- Rich, structured logging of every important decision (price fetches, ATR updates, signals, risk checks, snapshots).
- Easy for the developer (you) to diagnose: user can just zip and send the latest log file.
- Separate from audit events and trade ledger (those are for persistence/replay).
- Follows Kite best practices: log every external API call with timing and result.

Usage:
    from app.diagnostic_logger import diag
    diag.log_price_fetch(symbol, price, source, duration_ms=...)
    diag.log_signal_decision(symbol, decision, full_context_dict)
    ...

In paper mode (FORCE_DRY_RUN), we log at higher verbosity.
"""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


class DiagnosticLogger:
    def __init__(self):
        self.logger: Optional[logging.Logger] = None
        self.log_file_path: Optional[Path] = None
        self._run_id: Optional[str] = None

    def initialize(self, run_id: Optional[str] = None, level: int = logging.DEBUG) -> Path:
        """Initialize per-run file logging + console.

        Creates logs/run_YYYYMMDD_HHMMSS.log
        Returns the path to the log file (so main can print it).
        """
        if self.logger is not None:
            return self.log_file_path  # already initialized

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._run_id = run_id or timestamp

        logs_dir = Path("logs")
        logs_dir.mkdir(parents=True, exist_ok=True)

        self.log_file_path = logs_dir / f"run_{self._run_id}.log"

        # Root diagnostic logger
        self.logger = logging.getLogger("NiftyFuturesAlgo.Diag")
        self.logger.setLevel(level)

        # Remove existing handlers to avoid duplicates on re-init
        self.logger.handlers.clear()

        # File handler - detailed
        file_handler = logging.FileHandler(self.log_file_path, encoding="utf-8")
        file_formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        file_handler.setFormatter(file_formatter)
        file_handler.setLevel(logging.DEBUG)
        self.logger.addHandler(file_handler)

        # Console handler - slightly cleaner for terminal
        console_handler = logging.StreamHandler(sys.stdout)
        console_formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-5s | %(message)s",
            datefmt="%H:%M:%S"
        )
        console_handler.setFormatter(console_formatter)
        console_handler.setLevel(logging.INFO)
        self.logger.addHandler(console_handler)

        self.logger.info(f"=== DIAGNOSTIC SESSION STARTED ===")
        self.logger.info(f"Run ID: {self._run_id}")
        self.logger.info(f"Log file: {self.log_file_path.absolute()}")
        self.logger.info(f"Kite best practice: All external calls will be timed and logged.")

        return self.log_file_path

    def get_logger(self) -> logging.Logger:
        if self.logger is None:
            self.initialize()
        return self.logger

    def log_price_fetch(self, symbol: str, price: float, source: str, duration_ms: float, token: Any = None, error: str = None):
        """Log every attempt to get live price (core of 'is data hardcoded?' complaints)."""
        if error:
            self.get_logger().warning(f"[PRICE] {symbol} FETCH FAILED | source={source} | error={error} | duration={duration_ms:.1f}ms")
        else:
            self.get_logger().info(f"[PRICE] {symbol} = {price:.2f} | source={source} | token={token} | {duration_ms:.1f}ms")

    def log_atr_update(self, symbol: str, slow_atr: float, fast_atr: float, method: str):
        self.get_logger().debug(f"[ATR] {symbol} slow={slow_atr:.2f} fast={fast_atr:.2f} method={method}")

    def log_signal_decision(self, symbol: str, decision: str, context: Dict[str, Any]):
        """Log full context for every signal evaluation. This is gold for diagnosis."""
        ctx_str = " | ".join(f"{k}={v}" for k, v in context.items())
        level = logging.WARNING if decision in ("ACCEPTED", "ENTERED") else logging.INFO
        self.get_logger().log(level, f"[SIGNAL] {symbol} → {decision} | {ctx_str}")

    def log_snapshot(self, symbol: str, snapshot: Dict[str, Any]):
        """Log the exact dict that goes to dashboard and 3-index terminal."""
        self.get_logger().debug(f"[SNAPSHOT] {symbol} → {snapshot}")

    def log_risk_check(self, symbol: str, passed: bool, details: Dict[str, Any]):
        status = "PASS" if passed else "BLOCK"
        self.get_logger().info(f"[RISK] {symbol} {status} | {details}")

    def log_kite_call(self, method: str, params: Dict[str, Any], duration_ms: float, success: bool, result_size: int = 0):
        """Kite API best practice: log every external call with timing."""
        status = "OK" if success else "FAIL"
        self.get_logger().info(f"[KITE] {method} | params={params} | {duration_ms:.1f}ms | {status} | size={result_size}")

    def log_gui_event(self, event: str, details: Dict[str, Any] = None):
        self.get_logger().debug(f"[GUI] {event} | {details or ''}")

    def log_error(self, context: str, exc: Exception):
        self.get_logger().exception(f"[ERROR] {context}: {exc}")

    def shutdown(self):
        if self.logger:
            self.logger.info("=== DIAGNOSTIC SESSION ENDED ===\n")
            for handler in self.logger.handlers[:]:
                handler.close()
                self.logger.removeHandler(handler)


# Global singleton for easy import
diag = DiagnosticLogger()


def get_latest_log_file() -> Optional[Path]:
    """Helper so run.py can print the exact file the user should send."""
    logs_dir = Path("logs")
    if not logs_dir.exists():
        return None
    files = sorted(logs_dir.glob("run_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None
