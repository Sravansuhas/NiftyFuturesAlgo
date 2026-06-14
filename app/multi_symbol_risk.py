"""
Multi-Symbol Risk Manager for Monday Paper Trading (3 Indices)

Allows one open position per symbol (NIFTY, BANKNIFTY, SENSEX).
Global daily loss and drawdown limits enforced across all symbols.
"""

import os
import time
import logging
from typing import Dict, Optional
from dataclasses import dataclass, replace

from kiteconnect import KiteConnect

from .risk_gatekeeper import RiskGatekeeper, RiskConfig
from .state_machine import state_machine
from .audit_logger import audit_logger
from .micro_live import MicroLiveConfig, cap_order_quantity, load_micro_live_config

logger = logging.getLogger(__name__)

# Fallback lot sizes (2026 NSE/BSE) if instruments not loaded
_FALLBACK_LOTS = {"NIFTY": 65, "BANKNIFTY": 30, "SENSEX": 20}


def _match_futures_index(tradingsymbol: str) -> Optional[str]:
    """Map broker tradingsymbol to index key (BANKNIFTY before NIFTY substring match)."""
    sym = (tradingsymbol or "").upper()
    if not sym.endswith("FUT"):
        return None
    if sym.startswith("BANKNIFTY") or sym.startswith("BNF"):
        return "BANKNIFTY"
    if sym.startswith("SENSEX"):
        return "SENSEX"
    if sym.startswith("NIFTY"):
        return "NIFTY"
    return None


@dataclass
class SymbolPosition:
    symbol: Optional[str] = None
    quantity: int = 0
    avg_price: float = 0.0
    last_updated: Optional[float] = None


class MultiSymbolRiskManager:
    """Multi-symbol risk manager for 3 index futures paper trading."""

    def __init__(self, capital: float = 1_000_000.0):
        self.capital = capital
        self.daily_pnl = 0.0
        self.daily_loss = 0.0
        self.peak_equity = capital
        self.current_equity = capital
        self.consecutive_losses = 0
        self.trades_today = 0

        self.positions: Dict[str, SymbolPosition] = {
            "NIFTY": SymbolPosition(),
            "BANKNIFTY": SymbolPosition(),
            "SENSEX": SymbolPosition(),
        }

        self.symbol_daily_trades: Dict[str, int] = {"NIFTY": 0, "BANKNIFTY": 0, "SENSEX": 0}
        self.symbol_daily_pnl: Dict[str, float] = {"NIFTY": 0.0, "BANKNIFTY": 0.0, "SENSEX": 0.0}
        self.symbol_daily_loss: Dict[str, float] = {"NIFTY": 0.0, "BANKNIFTY": 0.0, "SENSEX": 0.0}

        self.pending_orders: Dict[str, Dict] = {}
        self.max_trades_per_symbol_per_day = 3
        self.max_daily_loss_per_symbol_pct = 0.01
        self.last_loss_timestamp: float = 0.0
        self.broker_connected: bool = True
        self._regime_by_symbol: Dict[str, Dict] = {}
        self._signal_context_by_symbol: Dict[str, Dict] = {}
        from .adaptive_trade_budget import AdaptiveBudgetConfig, compute_trade_budget, portfolio_budget_summary
        self._budget_config = AdaptiveBudgetConfig()
        self._compute_trade_budget = compute_trade_budget
        self._portfolio_budget_summary = portfolio_budget_summary

        self.config = RiskConfig(capital=capital, force_dry_run=True)
        self._global_gate = RiskGatekeeper(capital=capital)
        self._lot_size_cache: Dict[str, int] = dict(_FALLBACK_LOTS)
        self._micro_live_config: MicroLiveConfig = load_micro_live_config()

        logger.info("MultiSymbolRiskManager initialized for NIFTY + BANKNIFTY + SENSEX")

    def set_force_dry_run(self, force_dry: bool) -> None:
        """Sync dry-run flag on multi manager + underlying gatekeeper."""
        self.config = replace(self.config, force_dry_run=force_dry)
        self._global_gate.set_force_dry_run(force_dry)

    def set_micro_live_config(self, config: MicroLiveConfig) -> None:
        """Inject micro-live caps (1 lot / 1 position) at runtime."""
        self._micro_live_config = config

    def count_open_positions(self) -> int:
        return sum(1 for p in self.positions.values() if p.quantity != 0)

    def get_position(self, symbol: str) -> SymbolPosition:
        key = self._normalize_symbol(symbol)
        return self.positions.get(key, SymbolPosition())

    def _normalize_symbol(self, symbol: str) -> str:
        s = symbol.upper()
        if "BANKNIFTY" in s or "BNF" in s:
            return "BANKNIFTY"
        if "SENSEX" in s:
            return "SENSEX"
        return "NIFTY"

    def is_flat(self, symbol: Optional[str] = None) -> bool:
        if symbol:
            return self.get_position(symbol).quantity == 0
        return all(p.quantity == 0 for p in self.positions.values())

    def has_open_position(self, symbol: Optional[str] = None) -> bool:
        if symbol:
            return self.get_position(symbol).quantity != 0
        return any(p.quantity != 0 for p in self.positions.values())

    def get_position_quantity(self, symbol: str) -> int:
        return self.get_position(symbol).quantity

    def warm_lot_size_cache(self) -> None:
        """Populate lot sizes once after Kite instruments load (avoids SSE spam)."""
        try:
            from .instruments_manager import instruments_manager
            for key in ("NIFTY", "BANKNIFTY", "SENSEX"):
                inst = instruments_manager.get_active_future(key)
                if inst and inst.get("lot_size"):
                    self._lot_size_cache[key] = int(inst["lot_size"])
        except Exception as exc:
            logger.debug(f"Lot size cache warm-up skipped: {exc}")

    def _get_lot_size(self, symbol: str) -> int:
        key = self._normalize_symbol(symbol)
        if key in self._lot_size_cache:
            return self._lot_size_cache[key]
        try:
            from .instruments_manager import instruments_manager
            if instruments_manager.kite:
                inst = instruments_manager.get_active_future(key)
                if inst and inst.get("lot_size"):
                    self._lot_size_cache[key] = int(inst["lot_size"])
                    return self._lot_size_cache[key]
        except Exception:
            pass
        return _FALLBACK_LOTS.get(key, 65)

    def calculate_order_quantity(self, symbol: str, entry_price: float, stop_price: float) -> int:
        key = self._normalize_symbol(symbol)
        lot_size = self._get_lot_size(key)
        if self.capital <= 0:
            return lot_size

        stop_distance = abs(entry_price - stop_price)
        if stop_distance <= 1.0:
            stop_distance = 10.0

        multiplier = (
            self.config.reduced_risk_multiplier
            if self.consecutive_losses >= self.config.loss_streak_threshold
            else 1.0
        )
        risk_amount = self.capital * self.config.risk_per_trade_pct * multiplier
        lot_risk = stop_distance * lot_size
        if lot_risk <= 0:
            return lot_size

        lots = max(1, int(risk_amount / lot_risk))
        lots = min(lots, self.config.max_lots)
        return lots * lot_size

    def seconds_since_last_loss(self) -> float:
        if self.last_loss_timestamp <= 0:
            return float("inf")
        return max(0.0, time.time() - self.last_loss_timestamp)

    def set_market_regime(self, symbol: str, regime: Dict, signal_context: Optional[Dict] = None) -> None:
        """Strategy publishes live regime so adaptive budget can gate extensions."""
        key = self._normalize_symbol(symbol)
        self._regime_by_symbol[key] = dict(regime or {})
        if signal_context:
            self._signal_context_by_symbol[key] = dict(signal_context)

    def _build_budget_context(self, symbol: str) -> dict:
        key = self._normalize_symbol(symbol)
        market = {}
        posture_max = self.max_trades_per_symbol_per_day
        try:
            from .market_calendar import get_market_status
            market = get_market_status() or {}
        except Exception:
            pass
        regime = self._regime_by_symbol.get(key, {})
        learning_mult = 1.0
        try:
            from .intelligence_loop import intelligence_loop
            learning_mult, _ = intelligence_loop.get_learning_risk_multiplier(
                key, regime.get("volatility", "normal")
            )
        except Exception:
            pass

        try:
            from .regime_orchestrator import posture_for_symbol

            promo = False
            try:
                from .intelligence_loop import intelligence_loop
                promo = bool(
                    (intelligence_loop._get_promotion_for(key) or {}).get("passed")
                )
            except Exception:
                pass
            live_posture = posture_for_symbol(
                key,
                regime,
                {
                    "daily_pnl": self.daily_pnl,
                    "capital": self.capital,
                    "consecutive_losses": self.consecutive_losses,
                    "params_promoted": promo,
                    "is_expiry_day": market.get("is_expiry_day", False),
                    "safe_trading_window": market.get("is_safe_trading_window", True),
                    "within_pre_event_block_window": market.get(
                        "within_pre_event_block_window", False
                    ),
                    "learning_mult": learning_mult,
                },
            )
            posture_max = int(
                live_posture.get(
                    "recommended_max_trades_per_day",
                    self.max_trades_per_symbol_per_day,
                )
            )
        except Exception:
            try:
                from .intelligence_loop import intelligence_loop
                brief = intelligence_loop.build_market_brief()
                posture_max = int(
                    (brief.get("posture") or {}).get(
                        "recommended_max_trades_per_day",
                        self.max_trades_per_symbol_per_day,
                    )
                )
            except Exception:
                pass

        signal_ctx = self._signal_context_by_symbol.get(key, {})
        return {
            "daily_pnl": self.daily_pnl,
            "symbol_daily_pnl": self.symbol_daily_pnl.get(key, 0.0),
            "capital": self.capital,
            "consecutive_losses": self.consecutive_losses,
            "recommended_max_trades": posture_max,
            "is_expiry_day": bool(market.get("is_expiry_day")),
            "safe_trading_window": bool(
                market.get("is_safe_trading_window", market.get("safe_trading_window", True))
            ),
            "learning_mult": learning_mult,
            "trades_used": self.symbol_daily_trades.get(key, 0),
            "portfolio_trades": self.trades_today,
            "vol_ok": signal_ctx.get("vol_ok"),
            "entry_confidence": signal_ctx.get("entry_confidence"),
        }

    def get_trade_budget(self, symbol: str):
        key = self._normalize_symbol(symbol)
        return self._compute_trade_budget(
            key,
            self._regime_by_symbol.get(key, {}),
            self._build_budget_context(key),
            self._budget_config,
        )

    def get_effective_trade_cap(self, symbol: str) -> int:
        return self.get_trade_budget(symbol).effective_cap

    def get_budget_summary(self) -> dict:
        budgets = {sym: self.get_trade_budget(sym) for sym in ["NIFTY", "BANKNIFTY", "SENSEX"]}
        summary = self._portfolio_budget_summary(budgets)
        summary["adaptive_enabled"] = self._budget_config.enabled
        return summary

    @staticmethod
    def _compute_chop_metrics(regime: Optional[Dict]) -> tuple:
        """Derive ADX proxy and chop score from strategy-published regime (with safe fallbacks)."""
        regime = regime or {}
        trend = regime.get("trend", "ranging")
        vol = regime.get("volatility", "normal")
        htf = regime.get("htf_bias", "neutral")

        if regime.get("adx_proxy") is not None:
            adx_proxy = float(regime["adx_proxy"])
        else:
            adx_proxy = {"uptrend": 32.0, "downtrend": 32.0, "ranging": 18.0}.get(trend, 20.0)
            if vol == "low":
                adx_proxy -= 6.0
            elif vol == "high":
                adx_proxy += 4.0

        if regime.get("chop_score") is not None:
            chop_score = float(regime["chop_score"])
        else:
            chop_score = 0.0
            if trend == "ranging":
                chop_score += 0.45
            if vol == "low":
                chop_score += 0.30
            if htf == "neutral":
                chop_score += 0.15
            chop_score = min(1.0, chop_score)

        return trend, vol, adx_proxy, chop_score

    def build_fo_rules_context(self, symbol: str) -> dict:
        """Context for retail failure-pattern rules (see data/knowledge_base/indian_fo_rules.json)."""
        key = self._normalize_symbol(symbol)
        budget = self.get_trade_budget(key)
        is_paper = self.config.force_dry_run
        slm_on = os.getenv("ENABLE_EXCHANGE_SLM", "true").strip().lower() not in {"0", "false", "no"}
        has_hard_sl = is_paper or (not is_paper and slm_on)
        regime = self._regime_by_symbol.get(key, {})
        trend, volatility, adx_proxy, chop_score = self._compute_chop_metrics(regime)
        ctx = {
            "has_hard_stop_loss": has_hard_sl,
            "uses_mental_stop": False,
            "is_paper_mode": is_paper,
            "broker_connected": self.broker_connected,
            "has_open_position": self.has_open_position(),
            "consecutive_losses": self.consecutive_losses,
            "seconds_since_last_loss": self.seconds_since_last_loss(),
            "trades_today": self.symbol_daily_trades.get(key, 0),
            "effective_max_trades": budget.effective_cap,
            "regime_score": budget.regime_score,
            "expected_slippage_bps": 8.0,
            "paper_live_fill_divergence_bps": 0.0,
            "is_breakout_entry": True,
            "trend": trend,
            "volatility": volatility,
            "adx_proxy": adx_proxy,
            "chop_score": chop_score,
        }
        try:
            from .market_calendar import get_market_status

            market_status = get_market_status() or {}
            ctx["safe_trading_window"] = bool(market_status.get("is_safe_trading_window", True))
            ctx["hours_to_high_impact_event"] = market_status.get("hours_to_high_impact_event")
            ctx["within_pre_event_block_window"] = market_status.get(
                "within_pre_event_block_window", False
            )
        except Exception as exc:
            logger.debug("Market status for FO context skipped: %s", exc)

        try:
            from .rolling_edge import assess_rolling_edge

            ctx.update(assess_rolling_edge())
        except Exception as exc:
            logger.warning("Rolling edge context skipped: %s", exc)
            ctx.update({
                "rolling_expectancy": 0.0,
                "rolling_edge_trade_count": 0,
                "rolling_edge_sufficient": False,
                "rolling_edge_halt": False,
            })
        return ctx

    def check_fo_rules(self, symbol: str) -> tuple:
        """Returns (allowed, reason, risk_multiplier)."""
        try:
            from .fo_rules_engine import fo_rules_engine
            if fo_rules_engine is None:
                return True, "", 1.0
            return fo_rules_engine.check_entry(symbol, self.build_fo_rules_context(symbol))
        except Exception as exc:
            logger.warning(f"FO rules check skipped: {exc}")
            return True, "", 1.0

    def is_long(self, symbol: str) -> bool:
        return self.get_position_quantity(symbol) > 0

    def is_short(self, symbol: str) -> bool:
        return self.get_position_quantity(symbol) < 0

    def can_place_order(self, symbol: str, is_exit: bool = False) -> bool:
        key = self._normalize_symbol(symbol)

        if not state_machine.is_trading_allowed(is_exit=is_exit):
            return False

        if not self.config.force_dry_run:
            from .token_manager import live_trading_token_ok

            if not live_trading_token_ok():
                audit_logger.record("order.blocked", {
                    "symbol": key,
                    "reason": "kite_token_invalid",
                    "source": "token_manager",
                })
                return False

        if self.daily_loss >= self.config.max_daily_loss_pct * self.capital:
            return False
        if self._current_drawdown_pct() >= self.config.max_drawdown_pct:
            return False

        if not is_exit:
            try:
                from .market_calendar import is_eod_flatten_window

                if is_eod_flatten_window():
                    audit_logger.record("order.blocked", {
                        "symbol": key,
                        "reason": "eod_mis_flatten_window",
                        "source": "eod_flatten",
                    })
                    return False
            except Exception:
                pass

            allowed, reason, _ = self.check_fo_rules(symbol)
            if not allowed:
                audit_logger.record("order.blocked", {"symbol": key, "reason": reason, "source": "fo_rules"})
                return False
            budget = self.get_trade_budget(key)
            if self.symbol_daily_trades.get(key, 0) >= budget.effective_cap:
                audit_logger.record("order.blocked", {
                    "symbol": key,
                    "reason": f"daily_cap_{budget.trades_used}_of_{budget.effective_cap}",
                    "source": "adaptive_trade_budget",
                    "status": budget.status,
                    "regime_score": round(budget.regime_score, 3),
                })
                return False
            if self.trades_today >= budget.portfolio_cap:
                audit_logger.record("order.blocked", {
                    "symbol": key,
                    "reason": f"portfolio_cap_{self.trades_today}_of_{budget.portfolio_cap}",
                    "source": "adaptive_trade_budget",
                })
                return False
            if self.symbol_daily_loss.get(key, 0) >= self.max_daily_loss_per_symbol_pct * self.capital:
                return False
            if self.has_open_position(symbol):
                return False

        return True

    def _current_drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return (self.peak_equity - self.current_equity) / self.peak_equity

    def _record_trade_closed(
        self,
        symbol: str,
        quantity: int,
        transaction_type: str,
        realized_pnl: float,
        *,
        paper: bool,
    ) -> None:
        """Append closed-trade P&L to trade_ledger for rolling expectancy."""
        try:
            from .trade_ledger import trade_ledger

            trade_ledger.record("trade.closed", {
                "symbol": symbol,
                "quantity": quantity,
                "transaction_type": str(transaction_type).upper(),
                "realized_pnl": round(float(realized_pnl), 2),
                "paper": paper,
            })
        except Exception as exc:
            logger.warning("trade_ledger trade.closed skipped: %s", exc)

    def _record_realized_pnl(self, symbol: str, pnl: float) -> None:
        key = self._normalize_symbol(symbol)
        self.daily_pnl += pnl
        self.symbol_daily_pnl[key] = self.symbol_daily_pnl.get(key, 0.0) + pnl
        self.current_equity = self.capital + self.daily_pnl
        if self.current_equity > self.peak_equity:
            self.peak_equity = self.current_equity
        if pnl < 0:
            loss = abs(pnl)
            self.daily_loss += loss
            self.symbol_daily_loss[key] = self.symbol_daily_loss.get(key, 0.0) + loss
            self.consecutive_losses += 1
            self.last_loss_timestamp = time.time()
        else:
            self.consecutive_losses = 0
        try:
            from .risk_gatekeeper import risk_gatekeeper
            risk_gatekeeper.update_daily_loss(pnl)
        except Exception:
            pass

    def place_guarded_order(
        self,
        kite: Optional[KiteConnect],
        symbol: str,
        quantity: int,
        transaction_type: str,
        price: float = 0.0,
        order_type: str = "MARKET",
        product: str = "MIS",
        is_exit: bool = False,
        force_dry_run: bool = None,
        protective_stop: float = None,
    ) -> dict:
        sym = self._normalize_symbol(symbol)
        lot_size = self._get_lot_size(sym)

        micro_cfg = self._micro_live_config
        if micro_cfg.enabled and not is_exit:
            open_count = self.count_open_positions()
            if open_count >= micro_cfg.max_open_positions and not self.has_open_position(sym):
                audit_logger.record("order.blocked", {
                    "symbol": sym,
                    "reason": "micro_live_max_open_positions",
                    "open_positions": open_count,
                })
                return {
                    "success": False,
                    "message": (
                        f"Micro-live: max open positions ({micro_cfg.max_open_positions}) reached"
                    ),
                }
            capped_qty = cap_order_quantity(sym, quantity, lot_size, open_count, micro_cfg)
            if capped_qty <= 0:
                audit_logger.record("order.blocked", {
                    "symbol": sym,
                    "reason": "micro_live_cap",
                    "requested_quantity": quantity,
                })
                return {"success": False, "message": "Blocked by micro-live quantity caps"}
            quantity = capped_qty

        if quantity % lot_size != 0:
            return {"success": False, "message": f"Quantity must be multiple of lot size {lot_size}"}

        if not self.can_place_order(sym, is_exit=is_exit):
            audit_logger.record("order.blocked", {"symbol": sym, "reason": "risk_gate"})
            return {"success": False, "message": "Blocked by multi-symbol risk rules"}

        use_dry = self.config.force_dry_run if force_dry_run is None else force_dry_run
        if not use_dry:
            if kite is None:
                return {"success": False, "message": "Kite client required for live orders"}
            exchange = RiskGatekeeper.resolve_exchange(symbol)
            result = self._global_gate.place_guarded_order(
                kite=kite,
                symbol=symbol,
                quantity=quantity,
                transaction_type=transaction_type,
                price=price,
                order_type=order_type,
                product=product,
                is_exit=is_exit,
                force_dry_run=False,
                exchange=exchange,
                protective_stop=protective_stop,
                index_key=sym,
                lot_size=lot_size,
                multi_symbol_entry=not is_exit,
            )
            audit_logger.record("order.live_route", {
                "symbol": sym,
                "exchange": exchange,
                "success": result.get("success"),
                "order_id": result.get("order_id"),
                "is_exit": is_exit,
            })
            return result

        pnl = self._update_paper_position(sym, quantity, transaction_type, price, is_exit)

        if not is_exit:
            self.trades_today += 1
            self.symbol_daily_trades[sym] = self.symbol_daily_trades.get(sym, 0) + 1
            self._persist_state()

        audit_logger.record("order.paper_multi", {
            "symbol": sym,
            "quantity": quantity,
            "transaction_type": transaction_type,
            "is_exit": is_exit,
            "realized_pnl": pnl,
        })
        if is_exit:
            self._record_trade_closed(sym, quantity, transaction_type, pnl, paper=True)

        return {
            "success": True,
            "order_id": f"PAPER-{sym}-{int(time.time())}",
            "message": f"Paper order accepted for {sym}",
            "dry_run": True,
            "symbol": sym,
            "realized_pnl": pnl,
        }

    def on_broker_fill(
        self, symbol: str, quantity: int, side: str, avg_price: float, is_exit: bool = False
    ) -> dict:
        """Apply broker-confirmed fill to per-symbol position (live mode only)."""
        sym = self._normalize_symbol(symbol)
        realized_pnl = self._update_paper_position(sym, quantity, side, avg_price, is_exit)

        if not is_exit:
            self.trades_today += 1
            self.symbol_daily_trades[sym] = self.symbol_daily_trades.get(sym, 0) + 1

        audit_logger.record("order.live_fill_multi", {
            "symbol": sym,
            "quantity": quantity,
            "transaction_type": side.upper(),
            "avg_price": avg_price,
            "is_exit": is_exit,
            "realized_pnl": realized_pnl,
        })
        if is_exit:
            self._record_trade_closed(sym, quantity, side, realized_pnl, paper=False)

        return {
            "position_updated": True,
            "symbol": sym,
            "realized_pnl": realized_pnl,
        }

    def _update_paper_position(
        self, symbol: str, quantity: int, side: str, avg_price: float, is_exit: bool
    ) -> float:
        pos = self.positions[symbol]
        side = side.upper()
        realized_pnl = 0.0

        if is_exit:
            closed_qty = min(quantity, abs(pos.quantity))
            if closed_qty > 0 and pos.avg_price > 0 and avg_price > 0:
                if pos.quantity > 0 and side == "SELL":
                    realized_pnl = (avg_price - pos.avg_price) * closed_qty
                elif pos.quantity < 0 and side == "BUY":
                    realized_pnl = (pos.avg_price - avg_price) * closed_qty

            if side == "SELL" and pos.quantity > 0:
                pos.quantity -= quantity
            elif side == "BUY" and pos.quantity < 0:
                pos.quantity += quantity

            if pos.quantity == 0:
                pos.symbol = None
                pos.avg_price = 0.0

            if realized_pnl != 0:
                self._record_realized_pnl(symbol, realized_pnl)
        else:
            pos.symbol = symbol
            prev_qty = pos.quantity
            if side == "BUY":
                pos.quantity += quantity
            else:
                pos.quantity -= quantity

            if avg_price > 0:
                if prev_qty == 0:
                    pos.avg_price = avg_price
                elif (prev_qty > 0 and side == "BUY") or (prev_qty < 0 and side == "SELL"):
                    new_abs = abs(pos.quantity)
                    old_abs = abs(prev_qty)
                    if new_abs > 0:
                        pos.avg_price = (pos.avg_price * old_abs + avg_price * quantity) / new_abs

        pos.last_updated = time.time()
        self._persist_state()
        return round(realized_pnl, 2)

    def _persist_state(self) -> None:
        """Write paper positions + daily counters to disk (crash-safe)."""
        if not self.config.force_dry_run:
            return
        try:
            from .risk_state_persistence import save_risk_state
            save_risk_state(self)
        except Exception as exc:
            logger.debug("risk state persist skipped: %s", exc)

    def restore_paper_state(self) -> bool:
        """Reload today's paper risk snapshot after restart."""
        try:
            from .risk_state_persistence import restore_risk_manager
            restored = restore_risk_manager(self)
            if restored:
                logger.info(
                    "[MULTI_RISK] Restored paper state — P&L=%.2f trades=%d open=%d",
                    self.daily_pnl,
                    self.trades_today,
                    self.count_open_positions(),
                )
            return restored
        except Exception as exc:
            logger.warning("[MULTI_RISK] Paper state restore failed: %s", exc)
            return False

    def detect_broker_mismatches(self, broker_net_positions: list) -> list:
        """Return list of mismatch descriptions (empty = clean). Only meaningful in live mode."""
        if self.config.force_dry_run:
            return []

        mismatches = []
        for index_key in self.positions.keys():
            matches = [
                p for p in broker_net_positions
                if _match_futures_index(str(p.get("tradingsymbol", ""))) == index_key
            ]
            broker_qty = int(sum(int(m.get("quantity", 0) or 0) for m in matches)) if matches else 0
            pos = self.positions[index_key]
            if pos.quantity != broker_qty:
                mismatches.append(
                    f"{index_key}: internal={pos.quantity} broker={broker_qty}"
                )
        return mismatches

    def sync_with_broker(self, broker_net_positions: list) -> None:
        """Sync per-symbol positions from Kite net positions (live only)."""
        if self.config.force_dry_run:
            return

        for index_key in self.positions.keys():
            matches = [
                p for p in broker_net_positions
                if _match_futures_index(str(p.get("tradingsymbol", ""))) == index_key
            ]
            pos = self.positions[index_key]
            if not matches:
                if pos.quantity != 0:
                    logger.warning(f"[MULTI_RISK] Broker flat but internal {index_key} qty={pos.quantity}")
                pos.symbol = None
                pos.quantity = 0
                pos.avg_price = 0.0
                continue

            broker_qty = int(sum(int(m.get("quantity", 0) or 0) for m in matches))
            lead = matches[0]
            broker_symbol = lead.get("tradingsymbol")
            broker_avg = float(lead.get("average_price", 0.0) or 0.0)

            if pos.quantity != broker_qty or (broker_symbol and pos.symbol != broker_symbol):
                logger.info(
                    f"[MULTI_RISK] Sync {index_key}: internal qty={pos.quantity} -> broker qty={broker_qty}"
                )
            pos.symbol = broker_symbol
            pos.quantity = broker_qty
            pos.avg_price = broker_avg
            pos.last_updated = time.time()

    def reset_daily(self) -> None:
        self.daily_pnl = 0.0
        self.daily_loss = 0.0
        self.trades_today = 0
        self.consecutive_losses = 0
        self.current_equity = self.capital
        self.peak_equity = self.capital
        self.symbol_daily_trades = {"NIFTY": 0, "BANKNIFTY": 0, "SENSEX": 0}
        self.symbol_daily_pnl = {"NIFTY": 0.0, "BANKNIFTY": 0.0, "SENSEX": 0.0}
        self.symbol_daily_loss = {"NIFTY": 0.0, "BANKNIFTY": 0.0, "SENSEX": 0.0}
        self.last_loss_timestamp = 0.0
        self._global_gate.reset_daily()
        try:
            from .risk_state_persistence import clear_risk_state
            clear_risk_state()
        except Exception:
            pass
        logger.info("Multi-symbol daily risk counters reset")

    def get_all_positions_summary(self) -> dict:
        return {
            sym: {
                "quantity": p.quantity,
                "avg_price": round(p.avg_price, 2),
                "symbol": p.symbol,
                "daily_pnl": round(self.symbol_daily_pnl.get(sym, 0.0), 2),
                "daily_trades": self.symbol_daily_trades.get(sym, 0),
            }
            for sym, p in self.positions.items()
        }

    def get_per_symbol_status(self) -> dict:
        status = {}
        for sym in ["NIFTY", "BANKNIFTY", "SENSEX"]:
            pos = self.positions.get(sym, SymbolPosition())
            budget = self.get_trade_budget(sym)
            status[sym] = {
                "position": pos.quantity,
                "avg_price": round(pos.avg_price, 2),
                "daily_pnl": round(self.symbol_daily_pnl.get(sym, 0.0), 2),
                "daily_trades": self.symbol_daily_trades.get(sym, 0),
                "daily_loss": round(self.symbol_daily_loss.get(sym, 0.0), 2),
                "lot_size": self._get_lot_size(sym),
                "trade_budget": budget.to_dict(),
            }
        return status

    def get_lot_sizes(self) -> Dict[str, int]:
        return {sym: self._get_lot_size(sym) for sym in ["NIFTY", "BANKNIFTY", "SENSEX"]}


multi_risk_manager = MultiSymbolRiskManager()