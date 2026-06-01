"""
Multi-Symbol Risk Manager for Monday Paper Trading (3 Indices)

This is a pragmatic wrapper created for safe paper trading across 
NIFTY, BANKNIFTY, and SENSEX futures.

Key design for Monday:
- Allows one open position per symbol (NIFTY, BANKNIFTY, SENSEX can trade independently)
- Global daily loss and drawdown limits still enforced
- Reuses existing RiskGatekeeper logic where possible
- Everything stays in paper mode by default

This is NOT the final production multi-symbol risk system.
"""

import os
import time
import logging
from typing import Dict, Optional
from dataclasses import dataclass

from kiteconnect import KiteConnect

from .risk_gatekeeper import RiskGatekeeper, RiskConfig
from .state_machine import state_machine
from .audit_logger import audit_logger

logger = logging.getLogger(__name__)


@dataclass
class SymbolPosition:
    symbol: Optional[str] = None
    quantity: int = 0
    avg_price: float = 0.0
    last_updated: Optional[float] = None


class MultiSymbolRiskManager:
    """
    Monday-safe multi-symbol risk manager for 3 indices futures paper trading.
    """

    def __init__(self, capital: float = 1_000_000.0):
        self.capital = capital
        self.daily_pnl = 0.0
        self.daily_loss = 0.0
        self.peak_equity = capital
        self.current_equity = capital
        self.consecutive_losses = 0
        self.trades_today = 0  # global

        # Per-symbol positions
        self.positions: Dict[str, SymbolPosition] = {
            "NIFTY": SymbolPosition(),
            "BANKNIFTY": SymbolPosition(),
            "SENSEX": SymbolPosition(),
        }

        # === New: Per-symbol risk tracking (Phase 1 improvement) ===
        self.symbol_daily_trades: Dict[str, int] = {"NIFTY": 0, "BANKNIFTY": 0, "SENSEX": 0}
        self.symbol_daily_pnl: Dict[str, float] = {"NIFTY": 0.0, "BANKNIFTY": 0.0, "SENSEX": 0.0}
        self.symbol_daily_loss: Dict[str, float] = {"NIFTY": 0.0, "BANKNIFTY": 0.0, "SENSEX": 0.0}

        # Per-symbol limits (can be made configurable later)
        self.max_trades_per_symbol_per_day = 3
        self.max_daily_loss_per_symbol_pct = 0.01  # 1% per symbol

        self.pending_orders: Dict[str, Dict] = {}

        # Global config (reuse same limits)
        self.config = RiskConfig(capital=capital, force_dry_run=True)

        # Underlying single gatekeeper for global limits & some calculations
        self._global_gate = RiskGatekeeper(capital=capital)

        logger.info("MultiSymbolRiskManager initialized for NIFTY + BANKNIFTY + SENSEX (Paper Mode) — with per-symbol limits")

    def get_position(self, symbol: str) -> SymbolPosition:
        key = self._normalize_symbol(symbol)
        return self.positions.get(key, SymbolPosition())

    def _normalize_symbol(self, symbol: str) -> str:
        s = symbol.upper()
        if "BANKNIFTY" in s or "BNF" in s:
            return "BANKNIFTY"
        elif "SENSEX" in s:
            return "SENSEX"
        else:
            return "NIFTY"

    def is_flat(self, symbol: Optional[str] = None) -> bool:
        if symbol:
            return self.get_position(symbol).quantity == 0
        # All flat?
        return all(p.quantity == 0 for p in self.positions.values())

    def has_open_position(self, symbol: Optional[str] = None) -> bool:
        if symbol:
            return self.get_position(symbol).quantity != 0
        return any(p.quantity != 0 for p in self.positions.values())

    def get_position_quantity(self, symbol: str) -> int:
        return self.get_position(symbol).quantity

    def calculate_order_quantity(self, symbol: str, entry_price: float, stop_price: float) -> int:
        """Calculate quantity using correct lot size for the symbol."""
        key = self._normalize_symbol(symbol)
        lot_size = self._get_lot_size(key)
        if self.capital <= 0:
            return lot_size

        stop_distance = abs(entry_price - stop_price)
        if stop_distance <= 1.0:
            stop_distance = 10.0

        multiplier = self.config.reduced_risk_multiplier if self.consecutive_losses >= self.config.loss_streak_threshold else 1.0
        risk_amount = self.capital * self.config.risk_per_trade_pct * multiplier

        lot_risk = stop_distance * lot_size
        if lot_risk <= 0:
            return lot_size

        lots = max(1, int(risk_amount / lot_risk))
        max_lots = min(4, self.config.max_lots)
        lots = min(lots, max_lots)
        return lots * lot_size

    def can_place_order(self, symbol: str, is_exit: bool = False) -> bool:
        """Enhanced with per-symbol limits."""
        key = self._normalize_symbol(symbol)

        if not state_machine.is_trading_allowed():
            return False

        # Global checks
        if self.daily_loss >= self.config.max_daily_loss_pct * self.capital:
            return False
        if self._current_drawdown_pct() >= self.config.max_drawdown_pct:
            return False

        # Per-symbol checks
        if self.symbol_daily_trades.get(key, 0) >= self.max_trades_per_symbol_per_day and not is_exit:
            logger.debug(f"[{key}] Max trades per symbol reached today")
            try:
                from app.diagnostic_logger import diag
                diag.log_risk_check(key, False, {"reason": "max_trades_per_symbol", "daily_trades": self.symbol_daily_trades.get(key, 0)})
            except Exception:
                pass
            return False

        if self.symbol_daily_loss.get(key, 0) >= self.max_daily_loss_per_symbol_pct * self.capital and not is_exit:
            logger.debug(f"[{key}] Max daily loss per symbol reached")
            try:
                from app.diagnostic_logger import diag
                diag.log_risk_check(key, False, {"reason": "max_daily_loss_per_symbol", "daily_loss": self.symbol_daily_loss.get(key, 0)})
            except Exception:
                pass
            return False

        # Only one open position per symbol
        if self.has_open_position(symbol) and not is_exit:
            try:
                from app.diagnostic_logger import diag
                diag.log_risk_check(key, False, {"reason": "already_has_position"})
            except Exception:
                pass
            return False

        return True

    def _get_lot_size(self, symbol: str) -> int:
        """Dynamic lot size - will be improved when we load from Kite instruments."""
        key = self._normalize_symbol(symbol)
        if key == "BANKNIFTY":
            return 30
        elif key == "SENSEX":
            return 20
        return 65  # NIFTY (2026 value)

    def can_place_order(self, symbol: str, is_exit: bool = False) -> bool:
        if not state_machine.is_trading_allowed():
            return False

        # Global daily loss / drawdown checks
        if self.daily_loss >= self.config.max_daily_loss_pct * self.capital:
            logger.debug("Global daily loss limit reached")
            return False

        # Simple drawdown check (reuse logic)
        if self._current_drawdown_pct() >= self.config.max_drawdown_pct:
            return False

        # Per-symbol: only one position per symbol for Monday
        if self.has_open_position(symbol) and not is_exit:
            return False

        return True

    def _current_drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return (self.peak_equity - self.current_equity) / self.peak_equity

    def place_guarded_order(self, kite: KiteConnect, symbol: str, quantity: int,
                            transaction_type: str, price: float = 0.0,
                            order_type: str = "MARKET", product: str = "MIS",
                            is_exit: bool = False, force_dry_run: bool = True) -> dict:

        sym = self._normalize_symbol(symbol)
        lot_size = self._get_lot_size(sym)

        # Basic validation
        if quantity % lot_size != 0:
            return {"success": False, "message": f"Quantity must be multiple of lot size {lot_size}"}

        if not self.can_place_order(sym, is_exit=is_exit):
            audit_logger.record("order.blocked", {"symbol": sym, "reason": "risk_gate"})
            return {"success": False, "message": "Blocked by multi-symbol risk rules"}

        # For Monday: everything is dry run
        dry_run = True

        # Update internal position (paper only)
        self._update_paper_position(sym, quantity, transaction_type, price, is_exit)

        if not is_exit:
            self.trades_today += 1

        audit_logger.record("order.paper_multi", {
            "symbol": sym,
            "quantity": quantity,
            "transaction_type": transaction_type,
            "is_exit": is_exit,
        })

        return {
            "success": True,
            "order_id": f"PAPER-{sym}-{int(time.time())}",
            "message": f"Paper order accepted for {sym}",
            "dry_run": True,
            "symbol": sym,
        }

    def _update_paper_position(self, symbol: str, quantity: int, side: str, avg_price: float, is_exit: bool):
        pos = self.positions[symbol]
        side = side.upper()

        if is_exit:
            if side == "SELL" and pos.quantity > 0:
                pos.quantity -= quantity
            elif side == "BUY" and pos.quantity < 0:
                pos.quantity += quantity
            if pos.quantity == 0:
                pos.symbol = None
                pos.avg_price = 0.0
        else:
            pos.symbol = symbol
            if side == "BUY":
                pos.quantity += quantity
            else:
                pos.quantity -= quantity

            if avg_price > 0:
                if abs(pos.quantity) == quantity:  # new position
                    pos.avg_price = avg_price
                else:
                    # simple average
                    total = (pos.avg_price * (abs(pos.quantity) - quantity)) + (avg_price * quantity)
                    pos.avg_price = total / abs(pos.quantity) if pos.quantity != 0 else 0

        pos.last_updated = time.time()

    def reset_daily(self):
        self.daily_loss = 0.0
        self.trades_today = 0
        self.consecutive_losses = 0
        self.symbol_daily_trades = {"NIFTY": 0, "BANKNIFTY": 0, "SENSEX": 0}
        self.symbol_daily_pnl = {"NIFTY": 0.0, "BANKNIFTY": 0.0, "SENSEX": 0.0}
        self.symbol_daily_loss = {"NIFTY": 0.0, "BANKNIFTY": 0.0, "SENSEX": 0.0}
        self._global_gate.reset_daily()
        logger.info("Multi-symbol daily risk counters reset (including per-symbol)")

    # Convenience methods for dashboard
    def get_all_positions_summary(self) -> dict:
        return {
            sym: {
                "quantity": p.quantity,
                "avg_price": round(p.avg_price, 2),
                "symbol": p.symbol,
                "daily_pnl": round(self.symbol_daily_pnl.get(sym, 0.0), 2),
                "daily_trades": self.symbol_daily_trades.get(sym, 0)
            }
            for sym, p in self.positions.items()
        }

    def get_per_symbol_status(self) -> dict:
        """Rich status for dashboard cards."""
        status = {}
        for sym in ["NIFTY", "BANKNIFTY", "SENSEX"]:
            pos = self.positions.get(sym, SymbolPosition())
            status[sym] = {
                "position": pos.quantity,
                "avg_price": round(pos.avg_price, 2),
                "daily_pnl": round(self.symbol_daily_pnl.get(sym, 0.0), 2),
                "daily_trades": self.symbol_daily_trades.get(sym, 0),
                "daily_loss": round(self.symbol_daily_loss.get(sym, 0.0), 2),
            }
        return status


# Global instance for the app
multi_risk_manager = MultiSymbolRiskManager()