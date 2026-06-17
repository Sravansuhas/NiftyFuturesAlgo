from abc import ABC, abstractmethod
import os
import time
import random
import datetime
from typing import Optional
from kiteconnect import KiteConnect
from config import KITE_API_KEY, KITE_ACCESS_TOKEN
from .state_machine import state_machine, SystemState
from .risk_gatekeeper import risk_gatekeeper
from .audit_logger import audit_logger
from .paper_trading_params import PaperTradingParams, DEFAULT_PAPER_PARAMS
from .diagnostics import (
    log_signal_rejected,
    log_signal_accepted,
    log_ltp_issue,
    log_risk_block,
    get_gate_summary,
)
from .trade_ledger import trade_ledger
from .breakout_core import (
    ExitState,
    build_exit_config_from_atr,
    build_exit_config_from_fixed,
    should_exit_position,
)
from .state_persistence import (
    save_symbol_state,
    load_symbol_state,
    clear_symbol_state,
    save_strategy_state,
    load_strategy_state,
    clear_strategy_state,
)
from .market_calendar import is_market_open, is_real_market_open, now_ist, is_expiry_day
from .config_loader import apply_symbol_config_to_paper_params

import logging
logger = logging.getLogger(__name__)

# Reuse LTP within a main-loop tick (run_once + get_signal_snapshot) to avoid duplicate Kite calls.
_PRICE_CACHE_TTL_SEC = float(os.getenv("PRICE_CACHE_TTL_SEC", "3"))


class DataFeedError(Exception):
    """Raised when live market data (LTP) is unavailable in LIVE_MODE.
    Strategy must never silently simulate prices when real capital is at risk.
    """
    pass


class BaseStrategy(ABC):
    def __init__(self, kite: KiteConnect, symbol: str = None, quantity: int = 75, risk_manager=None):
        self.kite = kite
        self.symbol = symbol
        self.instrument_token = None
        self.quantity = quantity
        self.position = 0
        self.entry_price = 0.0
        self.risk_manager = risk_manager  # Optional multi-symbol risk manager for 3 indices

    @abstractmethod
    def should_enter_long(self) -> bool:
        pass

    @abstractmethod
    def should_enter_short(self) -> bool:
        pass

    @abstractmethod
    def should_exit(self) -> bool:
        pass

    def run(self):
        """Standalone infinite loop (demo / legacy). Prefer using main.py driver or run_once()."""
        logger = logging.getLogger(__name__)
        logger.debug(f"Strategy Started → {self.__class__.__name__} (PAPER_MODE)")
        self._initialize_nifty_future()   # Will now properly seed for NIFTY
        last_status_time = time.time()

        while True:
            try:
                self.run_once()
                current_time = time.time()
                if current_time - last_status_time >= 30:
                    current_price = self._get_current_price()
                    pos = "FLAT" if self.position == 0 else ("LONG" if self.position > 0 else "SHORT")
                    logger.debug(f"LTP: {current_price:.2f} | Position: {pos}")
                    last_status_time = current_time
            except Exception as e:
                logger.error(f"Strategy Error: {e}")
            time.sleep(5)

    def run_once(self):
        """Clean single iteration for paper/live use.
        
        Strategy only decides direction. All order placement and state updates
        go through the Risk Gatekeeper (single source of truth).
        For multi-symbol (3 indices), prefers self.risk_manager if provided.
        """
        rg = self.risk_manager or risk_gatekeeper

        # Always prefer the (per-symbol capable) gatekeeper's view of reality
        if rg.is_flat(self.symbol if self.risk_manager else None):
            if self.should_enter_long():
                self._place_order("BUY")
            elif self.should_enter_short():
                self._place_order("SELL")
        else:
            # We have a position according to the gatekeeper
            if self.should_exit():
                self._place_order_exit()

    def get_signal_snapshot(self) -> dict:
        """
        Always returns useful data for the dashboard.
        Even when no signal, it shows current state, last known values, and a "heartbeat".
        ALWAYS refreshes price so held positions show live ticking LTP + correct ATR (fixes frozen cards for BANKNIFTY/SENSEX).
        """
        # Prefer cached tick from run_once in the same loop iteration (avoids duplicate REST/WS).
        try:
            current_price = self._get_current_price(use_cache=True)
        except Exception:
            current_price = getattr(self, '_last_known_price', 0) or 0
        self._last_known_price = current_price if current_price else getattr(self, '_last_known_price', 0)

        # Staleness tracking (especially useful for diagnosing SENSEX lag)
        self._last_price_update_ts = time.time()

        # For live demo feel, strongly prefer the responsive fast_atr over the slow seeded 5-min ATR
        slow_atr = getattr(self, 'current_atr', 0) or 0
        fast = getattr(self, 'fast_atr', 0) or 0

        # Force a lightweight fast ATR tick using the just-fetched price so higher-priced indices (BNF/SX) get realistic movement
        self._update_fast_atr_only(current_price)

        fast = getattr(self, 'fast_atr', 0) or 0
        atr = fast if fast > slow_atr * 0.25 else max(slow_atr, fast, 1.0)  # more tolerant threshold for larger-point indices

        # Never show obviously broken 0 LTP; prefer last successful real over magic defaults
        if current_price < 100 and not (state_machine.get_state() == SystemState.LIVE_MODE):
            base = getattr(self, '_last_successful_real_price', None) or getattr(self, '_last_known_price', 0) or 0
            if base >= 100:
                current_price = base
            else:
                # Last resort, clearly mark as no-data in caller via data_source
                current_price = 0.0

        proposed = "FLAT"
        target = 0
        sl = 0
        entry_confidence = 0.0   # only from breakout condition (old behavior)

        regime = self.get_market_regime() if hasattr(self, 'get_market_regime') else {}

        if current_price > 0 and atr > 0:
            profit_mult = getattr(self.paper_params, 'profit_target_atr_mult', 2.0)
            stop_mult = getattr(self.paper_params, 'stop_loss_atr_mult', 1.1)

            vol = regime.get("volatility", "normal") if isinstance(regime, dict) else "normal"
            trend = regime.get("trend", "flat") if isinstance(regime, dict) else "flat"

            adj_profit = profit_mult
            adj_stop = stop_mult
            if vol == "high":
                adj_profit *= 0.9
                adj_stop *= 0.85
            if trend == "ranging":
                adj_profit *= 0.8
                adj_stop *= 0.9

            breakout_buffer = atr * getattr(self.paper_params, 'breakout_atr_mult', 0.75)

            if self.prev_high > 0 and current_price > self.prev_high + breakout_buffer:
                proposed = "LONG"
                target = round(current_price + (atr * adj_profit))
                sl = round(current_price - (atr * adj_stop))
                strength = (current_price - self.prev_high) / max(atr, 1)
                entry_confidence = min(0.92, 0.45 + strength * 0.25)

            elif self.prev_low > 0 and current_price < self.prev_low - breakout_buffer:
                proposed = "SHORT"
                target = round(current_price - (atr * adj_profit))
                sl = round(current_price + (atr * adj_stop))
                strength = (self.prev_low - current_price) / max(atr, 1)
                entry_confidence = min(0.92, 0.45 + strength * 0.25)

            # Gate reason comes from run_once() — do not re-run should_enter_* here (avoids duplicate logs)
            if proposed != "FLAT" and getattr(self, 'entry_price', 0) == 0:
                try:
                    from .diagnostic_logger import diag
                    now_ts = time.time()
                    last_ts = getattr(self, "_last_proposed_diag_ts", 0.0)
                    if now_ts - last_ts >= 30.0:
                        self._last_proposed_diag_ts = now_ts
                        diag.log_signal_decision(self.symbol, "PROPOSED_BUT_REJECTED_BY_GATES", {
                            "proposed": proposed,
                            "gate_summary": get_gate_summary(self.symbol),
                            "current_atr": round(self.current_atr, 2),
                            "fast_atr": round(getattr(self, 'fast_atr', 0), 2),
                            "prev_high": round(self.prev_high, 2),
                            "prev_low": round(self.prev_low, 2),
                            "regime": regime,
                            "risk_mult": round(self.get_risk_multiplier(), 2),
                        })
                except Exception:
                    pass

        # Compute dynamic position health first (for held positions) so "confidence" key is never stale 92%
        health_conf = None
        if getattr(self, 'entry_price', 0) > 0:
            health_conf = self._compute_position_health_conf(current_price)

        # Accurate data source tracking (last fetch, not "ever had real data")
        data_source = getattr(self, '_last_price_source', 'SIMULATED')

        # Warming up state for better UX during first 90 seconds
        is_warming_up = (data_source == "SIMULATED") and (time.time() - getattr(self, '_start_time', 0) < 90)

        spot_ltp = None
        spot_basis = None
        try:
            from .instruments_manager import instruments_manager
            now_ts = time.time()
            last_spot_ts = getattr(self, "_last_spot_fetch_ts", 0.0)
            if now_ts - last_spot_ts >= 30.0 and self._index_key:
                self._last_spot_fetch_ts = now_ts
                spot_ltp = instruments_manager.fetch_index_spot_ltp(self._index_key)
                if spot_ltp and current_price:
                    spot_basis = round(current_price - spot_ltp, 2)
        except Exception:
            pass

        snap = {
            "symbol": self.symbol,
            "index_key": self._index_key or "",
            "contract": self.symbol,
            "exchange": getattr(self, "exchange", None),
            "instrument_token": self.instrument_token,
            "ltp": round(current_price, 2) if current_price else 0,
            "spot_ltp": round(spot_ltp, 2) if spot_ltp else None,
            "spot_basis": spot_basis,
            "atr": round(atr, 2),
            "fast_atr": round(getattr(self, 'fast_atr', 0), 2),
            "regime": regime if isinstance(regime, dict) else {"volatility": "normal"},
            # Only populate proposed signal details when flat (no actual position) to avoid confusing the GUI cards
            "proposed": proposed if not (getattr(self, 'entry_price', 0) > 0) else 'HOLD',
            "target": target if not (getattr(self, 'entry_price', 0) > 0) else 0,
            "stop_loss": sl if not (getattr(self, 'entry_price', 0) > 0) else 0,
            "confidence": round(health_conf if health_conf is not None else entry_confidence, 2),
            "timestamp": time.time(),
            "last_update": datetime.datetime.now().strftime("%H:%M:%S"),
            "data_source": data_source,
            "is_warming_up": is_warming_up,
            # Active trade exits (set at entry) take precedence for held positions in UI
            "active_target": getattr(self, 'active_target', 0) or 0,
            "active_stop_loss": getattr(self, 'active_stop_loss', 0) or 0,
            "entry_price": getattr(self, 'entry_price', 0) or 0,
            # Best-effort live P&L for the open position (used by GUI cards for realistic daily/unrealized)
            "unrealized_pnl": round((current_price - (getattr(self, 'entry_price', 0) or current_price)) * (1 if (getattr(self, 'position', 0) or 0) >= 0 else -1), 2) if getattr(self, 'entry_price', 0) else 0.0,
            # Dynamic confidence for held positions (distance to SL vs Target) so it doesn't stay stuck at the entry-time 92%
            "position_health_conf": health_conf,
            # Data age in seconds (great for diagnosing SENSEX lag in GUI and logs)
            "data_age_seconds": round(time.time() - getattr(self, '_last_price_update_ts', time.time()), 1),
            "gate_summary": get_gate_summary(self._index_key or self.symbol or ""),
            "prev_candle_source": getattr(self, "_prev_candle_source", "unknown"),
            "prev_high": round(self.prev_high, 2) if self.prev_high else 0,
            "prev_low": round(self.prev_low, 2) if self.prev_low else 0,
            "last_update": datetime.datetime.now().strftime("%H:%M:%S"),
        }

        # === HEAVY DIAGNOSTIC LOGGING (every snapshot is recorded to file for easy post-run analysis) ===
        try:
            from .diagnostic_logger import diag
            diag.log_snapshot(self.symbol, snap)

            # SENSEX-specific staleness warning
            if "SENSEX" in (self.symbol or "").upper():
                age = snap.get('data_age_seconds', 0)
                if age > 45:
                    diag.get_logger().warning(f"[SENSEX_STALENESS] SENSEX data age: {age}s | LTP: {snap.get('ltp')}")
        except Exception:
            pass

        return snap

    def _initialize_index_future(self, index_name: str = "NIFTY"):
        """
        Uses the central InstrumentsManager for proper multi-exchange (NFO + BFO) contract selection.
        This is the correct way to get the active future for NIFTY, BANKNIFTY, and SENSEX.

        CRITICAL: This method now also performs full per-symbol seeding of:
          - prev_high / prev_low / prev_volume   (from recent 5min historical candle)
          - ATR state (current_atr + _tr_values) from historical + optional cache
          - fast_atr and price history reset

        This guarantees true isolation between the three strategy instances created in main.py.
        Previously, seeding happened in __init__ (always as NIFTY), then symbol was changed
        without re-seeding → BANKNIFTY/SENSEX inherited Nifty's ~23k levels (see 2026-06-01 logs).

        Call this immediately after constructing the strategy for the desired index.
        """
        previous_symbol = getattr(self, 'symbol', None)

        try:
            from .instruments_manager import instruments_manager
            instruments_manager.bind(self.kite)
            active = instruments_manager.get_active_future(index_name)

            if active:
                self.symbol = active["tradingsymbol"]
                self.instrument_token = active["instrument_token"]
                self.exchange = active.get("exchange") or (
                    "BFO" if index_name.upper() == "SENSEX" else "NFO"
                )
                if not self.instrument_token and self.symbol:
                    self.instrument_token = instruments_manager.get_instrument_token(
                        self.symbol, self.exchange
                    )
                logging.getLogger(__name__).info(
                    f"Using active {index_name} Future via InstrumentsManager: "
                    f"{self.symbol} (token={self.instrument_token}, exchange={self.exchange})"
                )
            else:
                raise ValueError(f"No active future returned by InstrumentsManager for {index_name}")

        except Exception as e:
            logger.warning(f"InstrumentsManager failed for {index_name}: {e}. Using fallback.")
            from .instruments_manager import _fallback_tradingsymbol
            self.symbol = _fallback_tradingsymbol(index_name.upper())
            self.exchange = "BFO" if index_name.upper() == "SENSEX" else "NFO"
            self.instrument_token = None
            try:
                from .instruments_manager import instruments_manager
                self.instrument_token = instruments_manager.get_instrument_token(
                    self.symbol, self.exchange
                )
            except Exception:
                pass

        # === ALWAYS re-seed after symbol is finalized (this is the key fix) ===
        self._seed_previous_candle()

        # Dev/testing path: try realistic ATR from local cache for this specific symbol
        try:
            self._try_load_realistic_vol_from_cache()
        except Exception:
            pass

        logger.info(f"[INIT] Seeding complete for {self.symbol} (was {previous_symbol}) | "
                    f"prev_high={self.prev_high:.2f} prev_low={self.prev_low:.2f} "
                    f"current_atr={self.current_atr:.2f}")

        self._index_key = index_name.upper()
        # Per-symbol YAML overrides (min ATR, breakout mult, session bounds, etc.)
        self.paper_params = apply_symbol_config_to_paper_params(
            self._index_key, self.paper_params
        )

    def restore_from_persistence(self, index_key: str) -> None:
        """Restore per-symbol trade context after broker reconciliation on startup."""
        self._index_key = index_key.upper()
        rg = self._rg
        persisted = load_symbol_state(index_key)
        rg_has_position = False
        try:
            rg_has_position = rg.get_position_quantity(self.symbol) != 0 if hasattr(rg, "get_position_quantity") else False
        except Exception:
            pass

        force_paper = getattr(getattr(rg, "config", None), "force_dry_run", True)
        persisted_qty = int(persisted.get("quantity", 0) or 0) if persisted else 0
        has_open = rg_has_position or persisted_qty != 0
        if persisted and has_open and (force_paper or rg_has_position):
            self.entry_price = float(persisted.get("entry_price", 0.0) or 0.0)
            self._entry_time = float(persisted.get("entry_time", time.time()) or time.time())
            self._best_price_in_trade = float(persisted.get("best_price", self.entry_price) or self.entry_price)
            self.active_target = float(persisted.get("active_target", 0.0) or 0.0)
            self.active_stop_loss = float(persisted.get("active_stop_loss", 0.0) or 0.0)
            logger.info(f"[STATE] Restored {index_key} context from disk (entry={self.entry_price:.2f})")

        if self.entry_price == 0 and rg_has_position:
            try:
                pos = rg.get_position(self.symbol) if hasattr(rg, "get_position") else None
                if pos and pos.avg_price:
                    self.entry_price = pos.avg_price
                    logger.debug(f"Restored entry_price from risk manager for {self.symbol}")
            except Exception as exc:
                logger.debug(f"Could not pull entry from risk manager: {exc}")

    def persist_state(self) -> None:
        """Persist open-trade context on shutdown or checkpoint."""
        state_key = self._index_key or self.symbol
        if not state_key:
            return
        rg = self._rg
        pos_qty = 0
        pos_avg = 0.0
        side = None
        try:
            if hasattr(rg, "get_position"):
                p = rg.get_position(self.symbol)
                pos_qty = int(getattr(p, "quantity", 0) or 0)
                pos_avg = float(getattr(p, "avg_price", 0) or 0)
                if pos_qty > 0:
                    side = "BUY"
                elif pos_qty < 0:
                    side = "SELL"
        except Exception:
            pass
        entry = float(self.entry_price or 0)
        if entry <= 0 and pos_qty == 0:
            return
        save_symbol_state(state_key, {
            "entry_price": entry,
            "entry_time": self._entry_time,
            "best_price": getattr(self, "_best_price_in_trade", entry),
            "active_target": getattr(self, "active_target", 0.0),
            "active_stop_loss": getattr(self, "active_stop_loss", 0.0),
            "quantity": pos_qty,
            "avg_price": pos_avg,
            "side": side,
            "symbol": self.symbol,
            "tradingsymbol": self.symbol,
        })

    def _initialize_nifty_future(self):
        """Backward compatible method."""
        self._initialize_index_future("NIFTY")

    def _enter(self, side: str):
        rg = self._rg
        result = rg.place_guarded_order(kite=self.kite, symbol=self.symbol, quantity=self.quantity, transaction_type=side, force_dry_run=True)
        if result["success"]:
            self.position = self.quantity if side == "BUY" else -self.quantity
            self.entry_price = result.get("price", 24550.0)
            logger.info(f"{side} Entry @ {self.entry_price} → {result.get('order_id')}")

    def _exit(self):
        if self.position == 0:
            return
        side = "SELL" if self.position > 0 else "BUY"
        rg = self._rg
        result = rg.place_guarded_order(kite=self.kite, symbol=self.symbol, quantity=abs(self.position), transaction_type=side, is_exit=True, force_dry_run=True)
        if result["success"]:
            logger.info(f"Exit Executed → {result.get('order_id')}")
            self.position = 0
            self.entry_price = 0.0


class PreviousCandleBreakoutStrategy(BaseStrategy):
    """
    Previous Candle Breakout strategy (deterministic rules).

    Entry: Break of previous 5-min candle high (long) / low (short) + optional volume filter.
    Sizing: Dynamic via RiskGatekeeper (risk % of capital, lot aligned, loss streak adjusted).
    Exits: Fixed point target / stop (mental; consider SL orders for production).
    Edge cases covered:
      - No silent price simulation in LIVE_MODE (raises DataFeedError)
      - Previous candle properly seeded from real historical on startup + rolled intraday
      - Respects market_calendar entry window and basic expiry-day caution
      - Daily reset of has_entered_today at new trading day
      - Uses risk-based quantity instead of fixed lots
      - Volume confirmation gracefully degraded when real-time volume unavailable
    """

    def __init__(self, kite: KiteConnect, profit_target: float = 25.0, stop_loss: float = 15.0,
                 vol_confirmation: bool = True, paper_params: PaperTradingParams = None,
                 risk_manager=None):
        super().__init__(kite, risk_manager=risk_manager)
        self.has_entered_today = False
        self.prev_high = 0.0
        self.prev_low = 0.0
        self.prev_volume = 0
        self.profit_target = profit_target
        self.stop_loss = stop_loss
        self.entry_price = 0.0
        self.active_target = 0.0
        self.active_stop_loss = 0.0
        self._ltp_warning_count = 0
        # Always prefer the injected per-symbol risk manager (multi_risk) over the legacy global single-symbol gatekeeper
        self._rg = risk_manager or risk_gatekeeper
        self._last_candle_update = 0
        self._last_seed_time = 0
        self._last_known_price = 0.0
        self._last_successful_real_price = None   # used for accurate data_source tracking
        self._last_price_source = "SIMULATED"     # "REAL" or "SIMULATED" for last fetch
        self._last_price_update_ts = 0.0  # for staleness detection (SENSEX lag diagnosis)
        self._unchanged_real_count = 0            # detect stale frozen LTP during dev/paper
        self._start_time = time.time()            # for warming_up state
        self._current_candle = {"start_ts": 0, "high": 0.0, "low": 0.0, "open": 0.0}
        self._last_candle_close = 0.0
        self.vol_confirmation = (
            paper_params.volume_confirmation if paper_params is not None else vol_confirmation
        )
        self._last_day = None
        self.trades_today = 0
        self.last_trade_time = 0.0          # for cooldown

        # ATR state for live adaptive logic
        self.current_atr = 0.0              # Short-term ATR (14) — 5-min roll
        self.fast_atr = 0.0                 # Lightweight fast ATR updated on every tick (for live UI feel)
        self._tr_values = []                # For short-term ATR

        self.long_term_atr = 0.0            # Longer-term ATR (~50)
        self._long_tr_values = []           # For regime detection

        # For trend / higher timeframe bias (lightweight)
        self._ema_values = []
        self._price_history = []

        # Paper trading specific parameters
        self.paper_params = paper_params or DEFAULT_PAPER_PARAMS

        # IMPORTANT: We deliberately do NOT seed prev_high/prev_low/ATR here.
        # Seeding must happen AFTER the correct symbol is set via _initialize_index_future(sym).
        # Previously this caused all multi-index instances (BANKNIFTY, SENSEX) to inherit Nifty's
        # previous candle levels (see run logs from 2026-06-01 showing prev_high=23469 for 53k/74k symbols).
        #
        # The correct flow is:
        #   1. strat = PreviousCandleBreakoutStrategy(...)
        #   2. strat._initialize_index_future("BANKNIFTY")   <-- this now does full seeding
        #
        # This guarantees true per-symbol isolation of prev_high, prev_low, _tr_values, ATR state, etc.

        self._index_key: Optional[str] = None
        self.ws_feed = None
        self._prev_candle_source = "unknown"
        self._last_ws_prev_open_time = None

    def _log_reject(self, reason: str, details: Optional[dict] = None, symbol: Optional[str] = None):
        log_signal_rejected(reason, details, symbol=symbol or self._index_key or self.symbol)

    def _passes_sheet_gate(self, side: str, market_regime: dict) -> bool:
        """Options sheet bias / confirm mode — manual analysis feeds futures entries."""
        try:
            from .sheet_algo_bridge import check_sheet_allows_futures_entry
            result = check_sheet_allows_futures_entry(
                self._index_key or "NIFTY",
                side,
                algo_trend=(market_regime or {}).get("trend"),
            )
            if not result.allowed:
                reason_key = (result.reason or "sheet_gate").split(":")[0].strip()
                self._log_reject(reason_key, {
                    "message": result.reason,
                    "sheet_bias": result.bias,
                    "sheet_mode": result.mode,
                    "side": side,
                    "filter": result.reason,
                })
                return False
            if result.advisory_only and result.bias not in ("none", ""):
                logger.info(
                    "[SHEET] %s %s — sheet bias %s (%s)",
                    self._index_key, side, result.bias, result.detail,
                )
        except Exception as exc:
            logger.debug("Sheet gate skipped (non-fatal): %s", exc)
        return True

    def _trades_today_for(self, rg, index_key: str) -> int:
        if hasattr(rg, "symbol_daily_trades"):
            return int(rg.symbol_daily_trades.get(index_key, 0))
        return int(getattr(rg, "trades_today", 0))

    def _trade_cap_reject_details(
        self,
        rg,
        index_key: str,
        trades_today: int,
        market_regime: dict,
        budget=None,
        *,
        vol_ok: Optional[bool] = None,
        entry_conf: Optional[float] = None,
    ) -> dict:
        details = {
            "trades_today": trades_today,
            "regime": market_regime,
        }
        if budget is None and hasattr(rg, "get_trade_budget"):
            budget = rg.get_trade_budget(index_key)
        if budget is not None:
            details.update({
                "effective_cap": budget.effective_cap,
                "hard_ceiling": budget.hard_ceiling,
                "base_cap": budget.base_cap,
                "bonus_granted": budget.bonus_granted,
                "bonus_available": budget.bonus_available,
                "regime_score": round(budget.regime_score, 3),
                "status": budget.status,
                "budget_reasons": budget.reasons,
            })
        else:
            details["effective_cap"] = self.paper_params.max_trades_per_day
        if vol_ok is not None:
            details["vol_ok"] = vol_ok
        if entry_conf is not None:
            details["entry_confidence"] = entry_conf
        return details

    def _passes_hard_trade_ceiling(self, rg, index_key: str, trades_today: int, market_regime: dict) -> bool:
        """Block only at absolute per-symbol ceiling before signal quality is scored."""
        if hasattr(rg, "get_trade_budget"):
            budget = rg.get_trade_budget(index_key)
            if trades_today >= budget.hard_ceiling:
                self._log_reject(
                    "max_trades_hard_ceiling",
                    self._trade_cap_reject_details(rg, index_key, trades_today, market_regime, budget),
                )
                return False
            return True
        max_trades = (
            rg.get_effective_trade_cap(index_key)
            if hasattr(rg, "get_effective_trade_cap")
            else self.paper_params.max_trades_per_day
        )
        if trades_today >= max_trades:
            self._log_reject(
                "max_trades_per_day_reached",
                self._trade_cap_reject_details(rg, index_key, trades_today, market_regime)
                | {"effective_cap": max_trades},
            )
            return False
        return True

    def _apply_posture_breakout_adjustments(
        self,
        breakout_buffer: float,
        index_key: str,
        market_regime: dict,
    ) -> float:
        """Widen breakout buffer from overnight GIFT gap + live posture."""
        try:
            from .regime_orchestrator import posture_for_symbol

            posture_ctx: dict = {"learning_mult": 1.0}
            if self.risk_manager is not None:
                rm = self.risk_manager
                posture_ctx.update({
                    "daily_pnl": getattr(rm, "daily_pnl", 0),
                    "capital": getattr(rm, "capital", 1_000_000),
                    "consecutive_losses": getattr(rm, "consecutive_losses", 0),
                })
            posture = posture_for_symbol(index_key, market_regime, posture_ctx)
            mult = float(posture.get("breakout_buffer_mult", 1.0) or 1.0)
            if posture.get("breakout_buffer_bias") == "wider" and mult <= 1.0:
                mult = 1.15
            return breakout_buffer * mult
        except Exception:
            return breakout_buffer

    def _passes_effective_trade_cap(
        self,
        rg,
        index_key: str,
        trades_today: int,
        market_regime: dict,
        vol_ok: bool,
        entry_conf: float,
    ) -> bool:
        """Full adaptive cap after volume + breakout confidence are known."""
        if hasattr(rg, "set_market_regime"):
            rg.set_market_regime(
                index_key,
                market_regime,
                {"vol_ok": vol_ok, "entry_confidence": entry_conf},
            )
        if hasattr(rg, "get_trade_budget"):
            budget = rg.get_trade_budget(index_key)
            if trades_today >= budget.effective_cap:
                self._log_reject(
                    "max_trades_quality_gate",
                    self._trade_cap_reject_details(
                        rg,
                        index_key,
                        trades_today,
                        market_regime,
                        budget,
                        vol_ok=vol_ok,
                        entry_conf=entry_conf,
                    ),
                )
                return False
            return True
        if hasattr(rg, "get_effective_trade_cap"):
            max_trades = rg.get_effective_trade_cap(index_key)
            if trades_today >= max_trades:
                self._log_reject(
                    "max_trades_quality_gate",
                    self._trade_cap_reject_details(
                        rg,
                        index_key,
                        trades_today,
                        market_regime,
                        vol_ok=vol_ok,
                        entry_conf=entry_conf,
                    )
                    | {"effective_cap": max_trades},
                )
                return False
        return True

    def _get_candle_builder(self):
        """Resolve CandleBuilder from ws_feed (preferred) or direct injection."""
        ws_feed = getattr(self, "ws_feed", None)
        if ws_feed is not None and hasattr(ws_feed, "get_candle_builder"):
            return ws_feed.get_candle_builder()
        return getattr(self, "candle_builder", None)

    def _apply_previous_candle(self, candle: dict, source: str) -> None:
        """Set breakout reference levels from a completed 5m bar."""
        self.prev_high = float(candle.get("high", 0) or 0)
        self.prev_low = float(candle.get("low", 0) or 0)
        if "volume" in candle:
            self.prev_volume = int(candle.get("volume", 0) or 0)
        if candle.get("close") is not None:
            self._last_candle_close = float(candle["close"])
        if candle.get("open_time") is not None:
            self._last_ws_prev_open_time = candle["open_time"]
        self._prev_candle_source = source
        self._last_seed_time = time.time()

    def _log_prev_candle_diag(self, source: str, *, rolled: bool = False) -> None:
        try:
            from .diagnostic_logger import diag
            event = "PREV_CANDLE_ROLL" if rolled else "PREV_CANDLE_SEED"
            diag.log_signal_decision(self.symbol, event, {
                "prev_candle_source": source,
                "prev_high": round(self.prev_high, 2),
                "prev_low": round(self.prev_low, 2),
                "prev_close": round(getattr(self, "_last_candle_close", 0) or 0, 2),
            })
        except Exception:
            pass

    def _bootstrap_atr_from_historical_candles(self, candles: list) -> None:
        """Bootstrap ATR from Kite-style historical candle rows."""
        if not candles or len(candles) < 2:
            return
        trs = []
        for i in range(1, min(len(candles), 25)):
            high = float(candles[-i].get("high", 0))
            low = float(candles[-i].get("low", 0))
            prev_close = (
                float(candles[-i - 1].get("close", candles[-i].get("close", 0)))
                if i + 1 < len(candles)
                else float(candles[-i].get("close", 0))
            )
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            trs.append(tr)

        if not trs:
            return
        self._tr_values = trs[-14:]
        self.current_atr = sum(self._tr_values) / len(self._tr_values)
        if "BANKNIFTY" in (self.symbol or "").upper():
            self.current_atr = max(self.current_atr, 45.0)
        elif "SENSEX" in (self.symbol or "").upper():
            self.current_atr = max(self.current_atr, 65.0)

    def _sync_previous_candle_from_builder(self) -> bool:
        """Prefer completed WebSocket 5m candles for prev_high/low when available."""
        if not self.instrument_token:
            return False
        from .candle_builder import get_previous_candle_for_symbol

        builder = self._get_candle_builder()
        ws_prev = get_previous_candle_for_symbol(int(self.instrument_token), builder)
        if not ws_prev:
            return False

        open_time = ws_prev.get("open_time")
        changed = open_time != getattr(self, "_last_ws_prev_open_time", None)
        if changed or self._prev_candle_source != "ws_candle":
            self._apply_previous_candle(ws_prev, "ws_candle")
            if changed:
                logger.info(
                    f"[WS_CANDLE] {self.symbol}: prev_high={self.prev_high:.2f} "
                    f"prev_low={self.prev_low:.2f} (completed 5m bar)"
                )
                self._log_prev_candle_diag("ws_candle", rolled=True)
        return True

    def _seed_previous_candle(self):
        """
        Seed prev_high/low/volume + initial ATR.

        Prefers completed WebSocket 5m candles when CandleBuilder has data; otherwise
        falls back to Kite REST historical seeding.
        """
        if not self.symbol:
            self.prev_high = 0.0
            self.prev_low = 0.0
            logger.warning("[?] Cannot seed previous candle - no symbol")
            return

        from .candle_builder import get_previous_candle_for_symbol

        builder = self._get_candle_builder()
        ws_prev = get_previous_candle_for_symbol(self.instrument_token, builder) if self.instrument_token else None
        if ws_prev:
            self._apply_previous_candle(ws_prev, "ws_candle")
            logger.info(
                f"[SEED] {self.symbol}: prev_high={self.prev_high:.2f} prev_low={self.prev_low:.2f} "
                f"prev_candle_source=ws_candle"
            )
            self._log_prev_candle_diag("ws_candle")
            if self.kite:
                try:
                    now = datetime.datetime.now(datetime.timezone.utc)
                    from_date = (now - datetime.timedelta(hours=3)).strftime("%Y-%m-%d")
                    to_date = now.strftime("%Y-%m-%d")
                    candles = self.kite.historical_data(
                        instrument_token=self.instrument_token or self.symbol,
                        from_date=from_date,
                        to_date=to_date,
                        interval="5minute",
                        continuous=False,
                        oi=False,
                    )
                    self._bootstrap_atr_from_historical_candles(candles)
                    if self.current_atr > 0:
                        logger.info(
                            f"[SEED] {self.symbol}: ATR={self.current_atr:.2f} "
                            f"(ATR from rest_historical, prev from ws_candle)"
                        )
                except Exception as atr_exc:
                    logger.debug(f"[{self.symbol}] ATR bootstrap after ws prev skipped: {atr_exc}")
            return

        if not self.kite:
            self.prev_high = 0.0
            self.prev_low = 0.0
            logger.warning(f"[{self.symbol}] Cannot seed previous candle - no kite or WS candles")
            return

        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            from_date = (now - datetime.timedelta(hours=3)).strftime("%Y-%m-%d")
            to_date = now.strftime("%Y-%m-%d")

            candles = self.kite.historical_data(
                instrument_token=self.instrument_token or self.symbol,
                from_date=from_date,
                to_date=to_date,
                interval="5minute",
                continuous=False,
                oi=False,
            )

            if candles and len(candles) >= 2:
                prev = candles[-2]
                self._apply_previous_candle({
                    "high": prev.get("high", prev.get("close", 0)),
                    "low": prev.get("low", prev.get("close", 0)),
                    "close": prev.get("close", 0),
                    "volume": prev.get("volume", 100000),
                }, "rest_historical")
                self._bootstrap_atr_from_historical_candles(candles)

                logger.info(
                    f"[SEED] {self.symbol}: prev_high={self.prev_high:.2f} prev_low={self.prev_low:.2f} "
                    f"ATR={self.current_atr:.2f} prev_candle_source=rest_historical"
                )
                self._log_prev_candle_diag("rest_historical")

            else:
                logger.warning(f"[{self.symbol}] Insufficient historical candles for seeding. Using conservative defaults.")
                base = self._last_known_price or 0
                self.prev_high = base + 20
                self.prev_low = base - 20
                self.current_atr = 20.0
                self._prev_candle_source = "rest_historical"

        except Exception as e:
            logger.warning(
                f"[{self.symbol}] Could not seed previous candle from Kite history: {e}. "
                "Breakout logic will be weak until first 5-min roll."
            )
            self.prev_high = 0.0
            self.prev_low = 0.0
            self.current_atr = 15.0
            self._prev_candle_source = "rest_historical"

    # ========================================================================
    # CLOSED-MARKET DEV TESTING: Realistic volatility from local cache
    # ========================================================================
    def _try_load_realistic_vol_from_cache(self) -> bool:
        """
        When in dev mode (DEV_USE_CACHED_VOL or DEV_SESSION_ACTIVE), try to load
        the most recent local historical cache file for THIS specific symbol and
        derive a realistic recent ATR.

        This fixes the previous situation where only Nifty got good ATR values
        and BNF/SENSEX were stuck with Nifty-derived numbers.

        Symbol matching is now robust (similar to MultiSymbolRiskManager).
        """
        import os
        dev_active = (
            os.getenv("DEV_USE_CACHED_VOL", "false").strip().lower() in {"1", "true", "yes", "on"}
            or os.getenv("DEV_SESSION_ACTIVE") == "true"
        )
        if not dev_active:
            return False

        if not self.symbol:
            return False

        try:
            from pathlib import Path
            import pandas as pd

            cache_dir = Path("data/historical_cache")
            if not cache_dir.exists():
                return False

            # Robust symbol key (same logic as MultiSymbolRiskManager)
            s = self.symbol.upper()
            if "BANKNIFTY" in s or "BNF" in s:
                target = "BANKNIFTY"
            elif "SENSEX" in s:
                target = "SENSEX"
            else:
                target = "NIFTY"

            candidates = []
            for f in cache_dir.glob("*_5minute.parquet"):
                name = f.stem.upper()
                if target == "BANKNIFTY" and "BANKNIFTY" in name:
                    candidates.append(f)
                elif target == "SENSEX" and "SENSEX" in name:
                    candidates.append(f)
                elif target == "NIFTY" and "NIFTY" in name and "BANK" not in name:
                    candidates.append(f)

            if not candidates:
                logger.debug(f"[{self.symbol}] No matching cache file found for dev vol seeding")
                return False

            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            latest = candidates[0]

            df = pd.read_parquet(latest)
            if len(df) < 30:
                return False

            # Compute ATR from the most recent ~60 bars in the cache
            recent = df.tail(60)
            trs = []
            closes = recent["close"].values
            highs = recent["high"].values
            lows = recent["low"].values
            for i in range(1, len(recent)):
                tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
                trs.append(tr)

            if len(trs) >= 8:
                atr = sum(trs[-14:]) / min(len(trs), 14)
                self.current_atr = float(atr)
                self._tr_values = trs[-14:]
                self.fast_atr = float(trs[-1]) * 0.7

                logger.info(f"[DEV] {self.symbol}: Loaded realistic ATR={self.current_atr:.2f} from cache {latest.name}")
                return True

        except Exception as e:
            logger.debug(f"[{self.symbol}] Cache vol seeding skipped: {e}")
        return False

    def _roll_previous_candle_if_needed(self, current_price: float):
        """5-min candle roller + ATR calculation; prev levels prefer WebSocket builder."""
        ws_active = self._sync_previous_candle_from_builder()

        now = time.time()
        bucket = int(now // 300) * 300

        if self._current_candle["start_ts"] == 0:
            self._current_candle = {"start_ts": bucket, "open": current_price, "high": current_price, "low": current_price}
            if not ws_active:
                self._last_candle_close = current_price
            return

        if bucket > self._current_candle["start_ts"]:
            prev_close = self._last_candle_close if self._last_candle_close > 0 else self._current_candle["open"]
            tr = max(
                self._current_candle["high"] - self._current_candle["low"],
                abs(self._current_candle["high"] - prev_close),
                abs(self._current_candle["low"] - prev_close)
            )

            self._tr_values.append(tr)
            if len(self._tr_values) > 14:
                self._tr_values.pop(0)
            self.current_atr = sum(self._tr_values) / len(self._tr_values) if self._tr_values else tr

            try:
                from .diagnostic_logger import diag
                diag.log_atr_update(self.symbol or "?", round(self.current_atr, 2), round(getattr(self, 'fast_atr', 0), 2), "5min_roll")
            except Exception:
                pass

            self._long_tr_values.append(tr)
            if len(self._long_tr_values) > 50:
                self._long_tr_values.pop(0)
            self.long_term_atr = sum(self._long_tr_values) / len(self._long_tr_values) if self._long_tr_values else self.current_atr

            if not ws_active and self._current_candle["high"] > 0:
                self.prev_high = self._current_candle["high"]
                self.prev_low = self._current_candle["low"]
                self.prev_volume = max(self.prev_volume, 80000)
                self._prev_candle_source = "polling_roll"
                self._log_prev_candle_diag("polling_roll", rolled=True)

            if not ws_active:
                self._last_candle_close = current_price
            self._current_candle = {"start_ts": bucket, "open": current_price, "high": current_price, "low": current_price}
            self._last_candle_update = now

            if not ws_active and now - self._last_seed_time > 900:
                self._seed_previous_candle()

    def _update_atr_and_ema_for_live(self, current_price: float):
        """Lightweight update for EMA and price history used by regime detection.
        Also maintains a fast ATR so the UI doesn't show a locked 33.3 value."""
        self._price_history.append(current_price)
        if len(self._price_history) > 50:
            self._price_history.pop(0)

        # Fast ATR — simple exponential smoothing of true range for responsive live feel (much better than locked seeded value)
        if len(self._price_history) >= 2:
            tr = abs(self._price_history[-1] - self._price_history[-2])
            alpha = 0.2  # fast reaction
            if self.fast_atr <= 0:
                self.fast_atr = tr
            else:
                self.fast_atr = alpha * tr + (1 - alpha) * self.fast_atr

        if len(self._price_history) >= 5:
            ema = self._price_history[-1]
            k = 2 / (20 + 1)
            for p in self._price_history[-20:]:
                ema = p * k + ema * (1 - k)
            self._ema_values.append(ema)
            if len(self._ema_values) > 20:
                self._ema_values.pop(0)

    def _update_fast_atr_only(self, current_price: float):
        """Ultra-lightweight fast ATR tick. Safe to call from snapshot path without full EMA/candle side effects.
        Critical for BANKNIFTY (~2x point ATR of Nifty) and SENSEX to show realistic live movement instead of stuck values."""
        if not hasattr(self, '_price_history') or self._price_history is None:
            self._price_history = []
        self._price_history.append(current_price)
        if len(self._price_history) > 50:
            self._price_history.pop(0)

        if len(self._price_history) >= 2:
            tr = abs(self._price_history[-1] - self._price_history[-2])
            slow = getattr(self, 'current_atr', 0) or 0
            atr_floor = max(slow * 0.15, 1.0)
            # Scale-aware initialization: larger indices need larger initial fast_atr
            if self.fast_atr <= 0:
                self.fast_atr = max(tr, atr_floor) * (1.6 if "BANKNIFTY" in (self.symbol or "") else (2.2 if "SENSEX" in (self.symbol or "") else 1.0))
            elif tr < 0.01:
                # Stale/frozen LTP: hold fast ATR near seeded value instead of decaying to zero
                self.fast_atr = max(self.fast_atr, atr_floor)
            else:
                alpha = 0.25
                self.fast_atr = max(alpha * tr + (1 - alpha) * self.fast_atr, atr_floor)

        # Also opportunistically log ATR evolution to the diagnostic file (gold for "ATR stuck?" diagnosis)
        try:
            from .diagnostic_logger import diag
            if len(self._price_history) % 4 == 0:  # log every ~4 ticks to avoid spam
                diag.log_atr_update(self.symbol or "?", round(getattr(self, 'current_atr', 0), 2), round(self.fast_atr, 2), "fast_tick")
        except Exception:
            pass

    def _update_daily_flags(self):
        """Reset any strategy-level daily flags. 
        Note: The authoritative trade count lives in risk_gatekeeper.
        """
        today = datetime.date.today()
        if self._last_day != today:
            self.has_entered_today = False   # legacy flag, kept for compatibility
            self.trades_today = 0
            self._last_day = today
            # We no longer print here to avoid noise; main loop handles daily reset logging
            pass

    def get_volatility_regime(self) -> str:
        """
        Lightweight volatility regime detection.
        Returns: 'low', 'normal', or 'high'
        """
        if self.long_term_atr < 1.0 or self.current_atr < 1.0:
            return "normal"

        ratio = self.current_atr / self.long_term_atr

        if ratio < 0.65:
            return "low"
        elif ratio > 1.45:
            return "high"
        else:
            return "normal"

    def get_trend_regime(self) -> str:
        """
        Very lightweight trend regime using price vs EMA + recent structure.
        Returns: 'uptrend', 'downtrend', 'ranging'
        """
        if len(self._ema_values) < 5 or len(self._price_history) < 10:
            return "ranging"

        ema = self._ema_values[-1]
        current = self._price_history[-1]

        # Simple direction
        if current > ema * 1.002:
            # Check if making higher highs recently
            recent_highs = [p for p in self._price_history[-5:]]
            if max(recent_highs) == recent_highs[-1]:
                return "uptrend"
        elif current < ema * 0.998:
            recent_lows = [p for p in self._price_history[-5:]]
            if min(recent_lows) == recent_lows[-1]:
                return "downtrend"

        return "ranging"

    def get_adx_proxy(self) -> float:
        """
        Directional-strength proxy when true ADX is unavailable (lower = more chop).
        Used by FO_CHOP_VETO and adaptive trade-budget gating.
        """
        trend = self.get_trend_regime()
        vol = self.get_volatility_regime()
        base = {"uptrend": 34.0, "downtrend": 34.0, "ranging": 16.0}.get(trend, 20.0)

        if self.long_term_atr > 1.0 and self.current_atr > 0:
            ratio = self.current_atr / self.long_term_atr
            if trend == "ranging":
                base -= max(0.0, (1.0 - ratio)) * 10.0

        if vol == "low":
            base -= 5.0
        elif vol == "high":
            base += 4.0

        return max(5.0, min(50.0, base))

    def get_chop_score(self) -> float:
        """0–1 chop intensity for ranging/low-vol fake-breakout windows."""
        score = 0.0
        trend = self.get_trend_regime()
        vol = self.get_volatility_regime()
        htf = self.get_higher_timeframe_bias()

        if trend == "ranging":
            score += 0.45
        if vol == "low":
            score += 0.30
        if htf == "neutral":
            score += 0.15

        if len(self._ema_values) >= 1 and len(self._price_history) >= 1:
            ema = self._ema_values[-1]
            price = self._price_history[-1]
            if ema > 0:
                dist = abs(price - ema) / ema
                if dist < 0.0015:
                    score += 0.12

        return min(1.0, score)

    def get_market_regime(self) -> dict:
        """Combined regime view for decision making."""
        return {
            "volatility": self.get_volatility_regime(),
            "trend": self.get_trend_regime(),
            "htf_bias": self.get_higher_timeframe_bias(),
            "adx_proxy": self.get_adx_proxy(),
            "chop_score": self.get_chop_score(),
        }

    def get_higher_timeframe_bias(self, lookback: int = 20) -> str:
        """
        Simple higher timeframe bias using the existing EMA.
        Returns: 'bullish', 'bearish', or 'neutral'
        """
        if len(self._ema_values) < lookback:
            return "neutral"

        ema = self._ema_values[-1]
        price = self._price_history[-1]

        # Stronger bias detection
        if price > ema * 1.003:
            return "bullish"
        elif price < ema * 0.997:
            return "bearish"
        else:
            return "neutral"

    def get_risk_multiplier(self) -> float:
        """
        Regime-based sizing + closed-loop learning de-risk (Phase 2).

        Learning layer may only reduce multiplier — never increase above regime base.
        """
        vol = self.get_volatility_regime()
        trend = self.get_trend_regime()

        multiplier = 1.0

        if vol == "high":
            multiplier *= 0.65
        elif vol == "low":
            multiplier *= 0.85

        if trend == "ranging":
            multiplier *= 0.75

        base = max(0.4, min(1.2, multiplier))

        try:
            from .intelligence_loop import intelligence_loop
            from .regime_orchestrator import posture_for_symbol

            index_key = self._index_key or "NIFTY"
            learning_mult, reasons = intelligence_loop.get_learning_risk_multiplier(
                index_key, vol
            )
            posture_ctx = {"learning_mult": learning_mult}
            if self.risk_manager is not None:
                rm = self.risk_manager
                posture_ctx.update({
                    "daily_pnl": getattr(rm, "daily_pnl", 0),
                    "capital": getattr(rm, "capital", 1_000_000),
                    "consecutive_losses": getattr(rm, "consecutive_losses", 0),
                })
            posture = posture_for_symbol(
                index_key,
                self.get_market_regime(),
                posture_ctx,
            )
            posture_hint = float(posture.get("risk_multiplier_hint", 1.0) or 1.0)
            combined = base * learning_mult
            if posture.get("posture") == "aggressive" and posture_hint > combined:
                combined = min(1.2, posture_hint)
            elif posture.get("posture") in ("defensive", "contingency"):
                combined = min(combined, posture_hint)
            if reasons:
                logger.debug(
                    "[LEARNING] %s risk: mult=%.3f posture=%s — %s",
                    index_key,
                    combined,
                    posture.get("posture"),
                    "; ".join(reasons[:2]),
                )
            return max(0.4, min(1.2, combined))
        except Exception as exc:
            logger.debug(f"Learning risk multiplier skipped: {exc}")
            return base

    def should_enter_long(self) -> bool:
        """God-level decision function with ATR awareness + cooldown."""
        self._update_daily_flags()

        if self._is_edge_case():
            self._log_reject("edge_case_filter")
            return False

        rg = self.risk_manager or risk_gatekeeper
        index_key = self._index_key or "NIFTY"
        market_regime = self.get_market_regime()
        if hasattr(rg, "set_market_regime"):
            rg.set_market_regime(index_key, market_regime)

        trades_today = self._trades_today_for(rg, index_key)
        if not self._passes_hard_trade_ceiling(rg, index_key, trades_today, market_regime):
            return False

        # Cooldown after recent trade
        if time.time() - self.last_trade_time < (self.paper_params.cooldown_minutes_after_trade * 60):
            self._log_reject("cooldown_active")
            return False

        current_price = self._get_current_price()
        if current_price <= 0:
            self._log_reject("invalid_price")
            return False

        self._roll_previous_candle_if_needed(current_price)
        self._update_atr_and_ema_for_live(current_price)   # ensure EMA/price history for regime

        # Minimum volatility filter
        if self.current_atr < self.paper_params.min_atr_points:
            self._log_reject("insufficient_volatility", {"current_atr": round(self.current_atr, 2)})
            try:
                from .diagnostic_logger import diag
                diag.log_signal_decision(self.symbol, "REJECTED", {
                    "reason": "insufficient_volatility",
                    "current_atr": round(self.current_atr, 2),
                    "min_required": self.paper_params.min_atr_points,
                    "regime": self.get_market_regime()
                })
            except Exception:
                pass
            return False

        vol_ok = self._volume_confirmation_passes()
        entry_conf = round(
            0.45 + ((current_price - self.prev_high) / max(self.current_atr or 1, 1)) * 0.25, 2
        ) if self.prev_high else 0.5
        if not self._passes_effective_trade_cap(
            rg, index_key, trades_today, market_regime, vol_ok, entry_conf
        ):
            return False

        if not rg.can_place_order(self.symbol if self.risk_manager else None, is_exit=False):
            self._log_reject("risk_gatekeeper_denied_entry")
            return False

        if not self._passes_sheet_gate("BUY", market_regime):
            return False

        # Dynamic breakout buffer
        if self.paper_params.use_atr_breakout and self.current_atr > 0:
            breakout_buffer = self.current_atr * self.paper_params.breakout_atr_mult
        else:
            breakout_buffer = self.paper_params.breakout_buffer_points

        # Regime-aware logic
        risk_mult = self.get_risk_multiplier()
        htf_bias = market_regime["htf_bias"]

        # Adjust breakout requirement based on regime
        if market_regime["volatility"] == "high":
            breakout_buffer *= 1.30
        elif market_regime["volatility"] == "low":
            breakout_buffer *= 0.85

        if market_regime["trend"] == "ranging":
            breakout_buffer *= 1.15

        # Higher Timeframe Bias Filter (important for robustness)
        # In should_enter_long
        if htf_bias == "bearish":
            breakout_buffer *= 1.35  # Make it harder to go long against HTF bias

        breakout_buffer = self._apply_posture_breakout_adjustments(
            breakout_buffer, index_key, market_regime
        )

        if not (self.prev_high > 0 and current_price > self.prev_high + breakout_buffer and vol_ok):
            self._log_reject("long_breakout_not_met", {
                "current": round(current_price, 2),
                "prev_high": round(self.prev_high, 2),
                "buffer": round(breakout_buffer, 2),
                "vol_ok": vol_ok,
                "atr": round(self.current_atr, 2),
                "regime": market_regime,
                "risk_mult": round(risk_mult, 2)
            })
            # Granular diagnostic for diagnosis
            try:
                from .diagnostic_logger import diag
                diag.log_signal_decision(self.symbol, "REJECTED_LONG", {
                    "reason": "long_breakout_not_met",
                    "ltp": round(current_price, 2),
                    "prev_high": round(self.prev_high, 2),
                    "buffer": round(breakout_buffer, 2),
                    "atr": round(self.current_atr, 2),
                    "fast_atr": round(getattr(self, 'fast_atr', 0), 2),
                    "regime": market_regime,
                    "risk_mult": round(risk_mult, 2)
                })
            except Exception:
                pass
            return False

        log_signal_accepted("LONG", current_price, {
            "buffer": round(breakout_buffer, 2),
            "vol_ok": vol_ok,
            "atr": round(self.current_atr, 2),
            "regime": market_regime,
            "risk_mult": round(risk_mult, 2)
        })

        # Granular accepted log with full context
        try:
            from .diagnostic_logger import diag
            diag.log_signal_decision(self.symbol, "ACCEPTED_LONG", {
                "ltp": round(current_price, 2),
                "buffer": round(breakout_buffer, 2),
                "atr": round(self.current_atr, 2),
                "fast_atr": round(getattr(self, 'fast_atr', 0), 2),
                "regime": market_regime,
                "risk_mult": round(risk_mult, 2),
                "entry_confidence": round(0.45 + ((current_price - self.prev_high) / max(self.current_atr or 1, 1)) * 0.25, 2)
            })
        except Exception:
            pass

        trade_ledger.record("signal.accepted", {
            "symbol": self.symbol,
            "side": "LONG",
            "price": current_price,
            "atr": round(self.current_atr, 2),
            "buffer": round(breakout_buffer, 2),
            "vol_ok": vol_ok,
            "regime": market_regime,
            "suggested_risk_mult": round(risk_mult, 2)
        })
        return True

    def should_enter_short(self) -> bool:
        """God-level decision function with ATR awareness + cooldown."""
        self._update_daily_flags()

        if self._is_edge_case():
            self._log_reject("edge_case_filter")
            return False

        rg = self.risk_manager or risk_gatekeeper
        index_key = self._index_key or "NIFTY"
        market_regime = self.get_market_regime()
        if hasattr(rg, "set_market_regime"):
            rg.set_market_regime(index_key, market_regime)

        trades_today = self._trades_today_for(rg, index_key)
        if not self._passes_hard_trade_ceiling(rg, index_key, trades_today, market_regime):
            return False

        if time.time() - self.last_trade_time < (self.paper_params.cooldown_minutes_after_trade * 60):
            self._log_reject("cooldown_active")
            return False

        current_price = self._get_current_price()
        if current_price <= 0:
            self._log_reject("invalid_price")
            return False

        self._roll_previous_candle_if_needed(current_price)
        self._update_atr_and_ema_for_live(current_price)

        if self.current_atr < self.paper_params.min_atr_points:
            self._log_reject("insufficient_volatility", {"current_atr": round(self.current_atr, 2)})
            try:
                from .diagnostic_logger import diag
                diag.log_signal_decision(self.symbol, "REJECTED", {
                    "reason": "insufficient_volatility",
                    "current_atr": round(self.current_atr, 2),
                    "min_required": self.paper_params.min_atr_points,
                    "regime": market_regime,
                })
            except Exception:
                pass
            return False

        vol_ok = self._volume_confirmation_passes()
        entry_conf = round(
            0.45 + ((self.prev_low - current_price) / max(self.current_atr or 1, 1)) * 0.25, 2
        ) if self.prev_low else 0.5
        if not self._passes_effective_trade_cap(
            rg, index_key, trades_today, market_regime, vol_ok, entry_conf
        ):
            return False

        if not rg.can_place_order(self.symbol if self.risk_manager else None, is_exit=False):
            self._log_reject("risk_gatekeeper_denied_entry")
            return False

        if not self._passes_sheet_gate("SELL", market_regime):
            return False

        if self.paper_params.use_atr_breakout and self.current_atr > 0:
            breakout_buffer = self.current_atr * self.paper_params.breakout_atr_mult
        else:
            breakout_buffer = self.paper_params.breakout_buffer_points

        risk_mult = self.get_risk_multiplier()
        htf_bias = market_regime["htf_bias"]

        if market_regime["volatility"] == "high":
            breakout_buffer *= 1.30
        elif market_regime["volatility"] == "low":
            breakout_buffer *= 0.85

        if market_regime["trend"] == "ranging":
            breakout_buffer *= 1.15

        # Higher Timeframe Bias Filter for shorts
        if htf_bias == "bullish":
            breakout_buffer *= 1.35

        breakout_buffer = self._apply_posture_breakout_adjustments(
            breakout_buffer, index_key, market_regime
        )

        if not (self.prev_low > 0 and current_price < self.prev_low - breakout_buffer and vol_ok):
            self._log_reject("short_breakout_not_met", {
                "current": round(current_price, 2),
                "prev_low": round(self.prev_low, 2),
                "buffer": round(breakout_buffer, 2),
                "vol_ok": vol_ok,
                "atr": round(self.current_atr, 2),
                "regime": market_regime,
                "risk_mult": round(risk_mult, 2)
            })
            return False

        log_signal_accepted("SHORT", current_price, {
            "buffer": round(breakout_buffer, 2),
            "vol_ok": vol_ok,
            "atr": round(self.current_atr, 2),
            "regime": market_regime,
            "risk_mult": round(risk_mult, 2)
        })

        # Granular accepted log
        try:
            from .diagnostic_logger import diag
            diag.log_signal_decision(self.symbol, "ACCEPTED_SHORT", {
                "ltp": round(current_price, 2),
                "buffer": round(breakout_buffer, 2),
                "atr": round(self.current_atr, 2),
                "fast_atr": round(getattr(self, 'fast_atr', 0), 2),
                "regime": market_regime,
                "risk_mult": round(risk_mult, 2)
            })
        except Exception:
            pass

        trade_ledger.record("signal.accepted", {
            "symbol": self.symbol,
            "side": "SHORT",
            "price": current_price,
            "atr": round(self.current_atr, 2),
            "buffer": round(breakout_buffer, 2),
            "vol_ok": vol_ok,
            "regime": market_regime,
            "suggested_risk_mult": round(risk_mult, 2)
        })
        return True

    def should_exit(self) -> bool:
        """
        Professional multi-condition exit logic.
        Designed to protect capital and capture profit across different market conditions.
        Uses the per-symbol risk manager (self._rg) when available so BANKNIFTY/SENSEX positions are tracked independently.
        """
        rg = self._rg
        try:
            if rg.is_flat(self.symbol if hasattr(rg, 'positions') or hasattr(rg, '_normalize_symbol') else None):
                return False
        except Exception:
            if rg.is_flat():
                return False

        current_price = self._get_current_price()
        if current_price <= 0:
            return False

        # === ALWAYS update internal models on price observation for held positions (best principle) ===
        self._roll_previous_candle_if_needed(current_price)
        self._update_atr_and_ema_for_live(current_price)

        if self.entry_price == 0:
            return False

        try:
            if hasattr(rg, "get_position_quantity"):
                qty = rg.get_position_quantity(self.symbol)
                is_long = qty > 0
            elif hasattr(rg, "is_long"):
                is_long = rg.is_long()
            else:
                is_long = False
        except Exception:
            is_long = False
        atr = max(self.current_atr, 8.0)  # safety floor
        market_regime = self.get_market_regime()
        index_key = self._index_key or "NIFTY"
        if self.paper_params.use_atr_exits and atr > 0:
            cfg = build_exit_config_from_atr(
                atr,
                self.paper_params.profit_target_atr_mult,
                self.paper_params.stop_loss_atr_mult,
            )
        else:
            cfg = build_exit_config_from_fixed(
                self.paper_params.profit_target,
                self.paper_params.stop_loss,
                atr,
            )
        try:
            from .regime_orchestrator import exit_overrides_for_posture, posture_for_symbol

            posture_ctx = {"learning_mult": 1.0}
            if self.risk_manager is not None:
                rm = self.risk_manager
                posture_ctx.update({
                    "daily_pnl": getattr(rm, "daily_pnl", 0),
                    "capital": getattr(rm, "capital", 1_000_000),
                    "consecutive_losses": getattr(rm, "consecutive_losses", 0),
                })
            posture = posture_for_symbol(index_key, market_regime, posture_ctx)
            overrides = exit_overrides_for_posture(
                posture.get("posture", "normal"), market_regime
            )
            for key, val in overrides.items():
                if hasattr(cfg, key):
                    setattr(cfg, key, val)
        except Exception:
            pass

        exit_state = ExitState(
            best_price=getattr(self, "_best_price_in_trade", self.entry_price) or self.entry_price,
            entry_time=getattr(self, "_entry_time", None),
        )
        try:
            from app.market_calendar import now_ist
            bar_time = now_ist()
        except Exception:
            bar_time = None
        exit_now, exit_state, reason = should_exit_position(
            current_price,
            self.entry_price,
            is_long,
            atr,
            cfg,
            exit_state,
            bar_time=bar_time,
            regime=market_regime,
        )
        self._best_price_in_trade = exit_state.best_price
        if exit_now:
            logger.info(f"Exit triggered ({reason}) @ {current_price:.2f}")
            return True

        # === Dynamic trailing of active T/SL for smoother position_health_conf (fixes pinned 0.95 on SENSEX) ===
        if self.active_target and self.active_stop_loss and self.entry_price:
            trail_atr = max(atr, 8.0)
            if is_long and current_price > self.entry_price:
                # Trail target up when in profit
                new_target = max(self.active_target, round(current_price + trail_atr * 1.5))
                if new_target > self.active_target:
                    self.active_target = new_target
            elif not is_long and current_price < self.entry_price:
                new_target = min(self.active_target, round(current_price - trail_atr * 1.5))
                if new_target < self.active_target:
                    self.active_target = new_target

            # Trail stop in the profitable direction
            sl_updated = False
            if is_long:
                new_sl = max(self.active_stop_loss, round(current_price - trail_atr * 1.2))
                if new_sl > self.active_stop_loss:
                    self.active_stop_loss = new_sl
                    sl_updated = True
            else:
                new_sl = min(self.active_stop_loss, round(current_price + trail_atr * 1.2))
                if new_sl < self.active_stop_loss:
                    self.active_stop_loss = new_sl
                    sl_updated = True

            if sl_updated and state_machine.get_state() == SystemState.LIVE_MODE:
                try:
                    from .exchange_protection import exchange_protection

                    if self.kite and index_key:
                        exchange_protection.modify_protection(
                            self.kite, index_key, float(self.active_stop_loss)
                        )
                except Exception as exc:
                    logger.debug("Exchange SL-M trail sync skipped: %s", exc)

        return False

    def _compute_position_health_conf(self, current_price: float) -> float:
        """Improved dynamic confidence using both distance to SL and progress toward target.
        Much smoother than pure linear-from-SL, prevents pinning near 0.95."""
        if not self.entry_price or not self.active_target or not self.active_stop_loss:
            return 0.75

        total_range = abs(self.active_target - self.active_stop_loss)
        if total_range < 1:
            return 0.7

        # Distance from SL (0 = at SL, 1 = at or beyond target)
        dist_from_sl = abs(current_price - self.active_stop_loss)
        health_from_sl = min(1.0, max(0.0, dist_from_sl / total_range))

        # Progress from entry toward target (helps when price is between entry and target)
        entry_to_target = abs(self.active_target - self.entry_price)
        if entry_to_target > 1:
            progress = (abs(current_price - self.entry_price) / entry_to_target)
            progress = min(1.0, max(0.0, progress))
        else:
            progress = 0.5

        # Blended health: 60% distance from SL + 40% progress toward target
        blended = 0.6 * health_from_sl + 0.4 * progress

        # Map to nice 0.35–0.96 range so it feels alive but safe
        return round(0.35 + blended * 0.61, 2)

    def _is_edge_case(self) -> bool:
        """Conservative entry filters using proper calendar helpers + paper params."""
        try:
            now = now_ist()

            # Session filter from paper params
            if now.time() < self.paper_params.session_start or now.time() > self.paper_params.session_end:
                self._log_reject("outside_paper_session", {
                    "current_time": str(now.time()),
                    "allowed": f"{self.paper_params.session_start} - {self.paper_params.session_end}"
                })
                return True

            try:
                if not is_market_open(now):
                    self._log_reject("market_closed")
                    return True
            except Exception:
                # If calendar check fails, don't block trading on paper
                pass

            # Expiry day handling
            if self.paper_params.avoid_expiry_day:
                underlying = self._index_key or "NIFTY"
                if is_expiry_day(now.date(), underlying=underlying):
                    if now.hour >= self.paper_params.expiry_day_cutoff_hour:
                        self._log_reject("expiry_day_safety", {"hour": now.hour})
                        return True

        except Exception as e:
            self._log_reject("calendar_check_failed", {"error": str(e)})
        return False

    def _should_use_dev_simulation(self) -> bool:
        """Paper dev mode with forced-open calendar but real session closed → simulate prices."""
        if state_machine.get_state() == SystemState.LIVE_MODE:
            return False
        import os
        dev_force = os.getenv("DEV_FORCE_MARKET_OPEN", "false").strip().lower() in {"1", "true", "yes", "on"}
        force_dry = os.getenv("FORCE_DRY_RUN", "true").strip().lower() not in {"0", "false", "no"}
        if not (dev_force and force_dry):
            return False
        try:
            return not is_real_market_open()
        except Exception:
            return False

    def _simulate_price(self, reason: str = "fallback") -> float:
        """Paper-mode price simulation anchored to last known real price."""
        if "SENSEX" in (self.symbol or "").upper():
            try:
                from .diagnostic_logger import diag
                diag.get_logger().warning(
                    f"[SENSEX_LAG] SENSEX using simulation ({reason}). "
                    f"Last successful real: {self._last_successful_real_price}"
                )
            except Exception:
                pass

        base = self._last_successful_real_price or self._last_known_price or 0
        if base < 100:
            sym = (self.symbol or "").upper()
            if "BANKNIFTY" in sym:
                base = 48000.0
            elif "SENSEX" in sym:
                base = 74000.0
            else:
                base = 22000.0

        vol = max(getattr(self, 'fast_atr', 0) or getattr(self, 'current_atr', 0) or 25.0, 15.0)
        if "SENSEX" in (self.symbol or "").upper():
            vol = min(vol, 35.0)
            jitter = random.uniform(-vol * 0.35, vol * 0.35)
        else:
            jitter = random.uniform(-vol * 0.55, vol * 0.55)

        import os
        try:
            sim_mult = float(os.getenv("DEV_SIM_VOL_MULTIPLIER", "1.0"))
            if sim_mult != 1.0 and sim_mult > 0:
                jitter *= sim_mult
                if not getattr(self, "_dev_vol_mult_logged", False):
                    try:
                        from .diagnostic_logger import diag
                        diag.get_logger().info(
                            f"[DEV] DEV_SIM_VOL_MULTIPLIER={sim_mult:.2f} active for {self.symbol} "
                            f"(jitter scaled by {sim_mult:.2f}x)"
                        )
                    except Exception:
                        pass
                    self._dev_vol_mult_logged = True
        except Exception:
            pass

        sim_price = base + jitter
        self._last_known_price = sim_price
        self._last_price_source = "SIMULATED"

        try:
            from .diagnostic_logger import diag
            diag.log_price_fetch(self.symbol, sim_price, "SIMULATED", 0.0)
        except Exception:
            pass
        return sim_price

    def _invalidate_price_cache(self) -> None:
        self._price_cache_ts = 0.0
        self._price_cache_val = None

    def _get_current_price(self, *, use_cache: bool = True) -> float:
        """
        Real data first during actual market hours.
        Dev closed-market mode uses simulation so ATR/breakout logic stays exercisable.
        Paper mode falls back to simulation after real attempts fail.
        """
        now = time.time()
        if use_cache:
            cached_at = getattr(self, "_price_cache_ts", 0.0)
            cached_val = getattr(self, "_price_cache_val", None)
            if cached_val is not None and now - cached_at < _PRICE_CACHE_TTL_SEC:
                return float(cached_val)

        if self._should_use_dev_simulation():
            if not getattr(self, "_dev_sim_logged", False):
                try:
                    from .diagnostic_logger import diag
                    diag.get_logger().info(
                        f"[DEV] Real market closed — using simulated prices for {self.symbol} "
                        f"(calendar bypassed for strategy gates only)"
                    )
                except Exception:
                    pass
                self._dev_sim_logged = True
            price = self._simulate_price(reason="dev_closed_market")
            self._price_cache_val = price
            self._price_cache_ts = time.time()
            return price

        ws_feed = getattr(self, "ws_feed", None)
        if ws_feed and self.instrument_token:
            try:
                price, age = ws_feed.get_last_price_with_age(int(self.instrument_token))
                if price and price > 0 and age <= 15.0:
                    self._last_known_price = price
                    self._last_successful_real_price = price
                    self._last_price_source = "WS"
                    try:
                        from .diagnostic_logger import diag
                        diag.log_price_fetch(self.symbol, price, "WS", age * 1000.0, token=self.instrument_token)
                    except Exception:
                        pass
                    self._price_cache_val = price
                    self._price_cache_ts = time.time()
                    return price
            except Exception as ws_exc:
                logger.debug(f"[WS] Price fallback for {self.symbol}: {ws_exc}")

        attempts = 1
        try:
            if is_real_market_open():
                attempts = 3
        except Exception:
            attempts = 1

        for attempt in range(attempts):
            try:
                if not self.kite:
                    raise DataFeedError("No Kite client available")

                t0 = time.time()

                from .instruments_manager import ltp_key
                exchange = getattr(self, "exchange", None) or (
                    "BFO" if "SENSEX" in (self.symbol or "").upper() else "NFO"
                )
                key = ltp_key(self.symbol, exchange)
                from .kite_rate_limit import quote_limiter

                quote_limiter.wait()
                ltp_data = self.kite.ltp([key])

                duration_ms = (time.time() - t0) * 1000

                if key in ltp_data and "last_price" in ltp_data[key]:
                    price = float(ltp_data[key]["last_price"])
                    if price > 0:
                        prev = getattr(self, '_last_fetched_real_price', None)
                        if prev is not None and abs(price - prev) < 0.01:
                            self._unchanged_real_count += 1
                        else:
                            self._unchanged_real_count = 0
                            self._last_fetched_real_price = price

                        # Frozen LTP during paper dev (e.g. after-hours quote still returned)
                        if (
                            self._unchanged_real_count >= 4
                            and state_machine.get_state() != SystemState.LIVE_MODE
                            and os.getenv("FORCE_DRY_RUN", "true").strip().lower() not in {"0", "false", "no"}
                        ):
                            price = self._simulate_price(reason="stale_ltp")
                            self._price_cache_val = price
                            self._price_cache_ts = time.time()
                            return price

                        self._last_known_price = price
                        self._last_successful_real_price = price
                        self._last_price_source = "REAL"

                        try:
                            from .diagnostic_logger import diag
                            diag.log_price_fetch(self.symbol, price, "REAL", duration_ms, token=self.instrument_token)
                        except Exception:
                            pass
                        self._price_cache_val = price
                        self._price_cache_ts = time.time()
                        return price

                if attempt < attempts - 1:
                    continue

                raise Exception(f"No valid positive last_price for {key}")

            except Exception as e:
                if attempt < attempts - 1:
                    continue

                self._ltp_warning_count += 1
                if state_machine.get_state() == SystemState.LIVE_MODE:
                    logger.error(f"[LIVE] Real LTP failed for {self.symbol} after {attempts} attempts: {e}")
                    raise DataFeedError(f"Live LTP failed for {self.symbol}")

                logger.warning(f"[PAPER] Real data failed for {self.symbol} after retries. Using simulation anchored to last known.")
                price = self._simulate_price(reason="api_failure")
                self._price_cache_val = price
                self._price_cache_ts = time.time()
                return price

        fallback = self._last_known_price or 0.0
        if fallback:
            self._price_cache_val = fallback
            self._price_cache_ts = time.time()
        return fallback

    def _get_current_volume(self):
        """Real-time bar volume is unavailable from LTP-only feeds; None signals skip confirmation."""
        return None

    def _volume_confirmation_passes(self) -> bool:
        if not self.vol_confirmation or self.prev_volume == 0:
            return True
        current_volume = self._get_current_volume()
        if current_volume is None:
            return True
        return current_volume > self.prev_volume * self.paper_params.volume_mult

    def _sync_entry_trade_state(
        self,
        side: str,
        qty: int,
        entry_p: float,
        atr_at_entry: float,
        protective_stop: float,
    ) -> None:
        """Set strategy trade context after a confirmed entry fill (paper or live)."""
        self.last_trade_time = time.time()
        self._entry_time = time.time()
        self._best_price_in_trade = entry_p or self._last_known_price or 0.0
        self.entry_price = entry_p

        profit_m = getattr(self.paper_params, "profit_target_atr_mult", 2.0)
        if entry_p > 0:
            if side == "BUY":
                self.active_target = round(entry_p + (atr_at_entry * profit_m))
                self.active_stop_loss = protective_stop
            else:
                self.active_target = round(entry_p - (atr_at_entry * profit_m))
                self.active_stop_loss = protective_stop

        try:
            from .diagnostic_logger import diag
            diag.log_signal_decision(self.symbol, "POSITION_STATE_SET", {
                "entry_price": self.entry_price,
                "active_target": self.active_target,
                "active_stop_loss": self.active_stop_loss,
                "qty": qty,
            })
        except Exception:
            pass

        state_key = self._index_key or self.symbol
        rg = self._rg
        pos_qty = 0
        pos_avg = 0.0
        try:
            if hasattr(rg, "get_position"):
                p = rg.get_position(self.symbol)
                pos_qty = int(getattr(p, "quantity", 0) or 0)
                pos_avg = float(getattr(p, "avg_price", 0) or 0)
        except Exception:
            pass
        save_symbol_state(state_key, {
            "entry_price": self.entry_price,
            "entry_time": self._entry_time,
            "best_price": getattr(self, "_best_price_in_trade", self.entry_price),
            "active_target": getattr(self, "active_target", 0.0),
            "active_stop_loss": getattr(self, "active_stop_loss", 0.0),
            "quantity": pos_qty or qty,
            "avg_price": pos_avg or entry_p,
            "side": side,
            "symbol": self.symbol,
            "tradingsymbol": self.symbol,
        })

    def _clear_trade_state(self) -> None:
        self.entry_price = 0.0
        self.active_target = 0.0
        self.active_stop_loss = 0.0
        self._entry_time = None
        self._best_price_in_trade = 0.0
        clear_symbol_state(self._index_key or self.symbol)

    def on_live_fill_confirmed(
        self,
        fill_price: float,
        filled_qty: int,
        transaction_type: str,
        is_exit: bool,
        order_id: str = "",
    ) -> None:
        """Called by order_lifecycle when broker reports COMPLETE fill in live mode."""
        if is_exit:
            self._clear_trade_state()
            logger.info("Live exit fill confirmed for %s (order_id=%s)", self.symbol, order_id)
            return

        meta = getattr(self, "_pending_entry_meta", None) or {}
        side = transaction_type.upper() or meta.get("side")
        atr_at_entry = meta.get("atr_at_entry") or max(self.current_atr or 30.0, 20.0)
        protective_stop = meta.get("protective_stop")
        if protective_stop is None and fill_price > 0:
            stop_m = getattr(self.paper_params, "stop_loss_atr_mult", 1.1)
            protective_stop = (
                round(fill_price - (atr_at_entry * stop_m))
                if side == "BUY"
                else round(fill_price + (atr_at_entry * stop_m))
            )

        self._sync_entry_trade_state(
            side=side,
            qty=filled_qty,
            entry_p=fill_price,
            atr_at_entry=atr_at_entry,
            protective_stop=protective_stop,
        )
        self._pending_entry_meta = None
        logger.info(
            "Live entry fill confirmed for %s @ %.2f qty=%s (order_id=%s)",
            self.symbol,
            fill_price,
            filled_qty,
            order_id,
        )

    def on_live_order_terminal_no_fill(self, order_id: str = "", status: str = "", pending: dict = None) -> None:
        """Clear pending entry meta when broker rejects/cancels without fill."""
        if pending and pending.get("is_exit"):
            return
        self._pending_entry_meta = None
        logger.info("Live order %s %s — cleared pending entry meta for %s", order_id, status, self.symbol)

    def _place_order(self, side: str):
        """Place an entry order through the Risk Gatekeeper (or multi-symbol manager) with regime-aware sizing."""
        self._invalidate_price_cache()
        if not self.kite or not self.symbol:
            self._log_reject("missing_kite_or_symbol")
            return

        rg = self.risk_manager or risk_gatekeeper
        fill_price = float(self._last_known_price or 0.0)

        if hasattr(rg, "check_fo_rules"):
            allowed, fo_reason, fo_mult = rg.check_fo_rules(self.symbol)
            if not allowed:
                log_risk_block(fo_reason, {"side": side, "symbol": self.symbol, "source": "fo_rules"})
                return
        else:
            fo_mult = 1.0

        try:
            stop_distance = max(8.0, self.stop_loss)
            base_qty = rg.calculate_order_quantity(
                self.symbol,
                entry_price=fill_price or 24500.0,
                stop_price=(fill_price or 24500.0) - stop_distance if side == "BUY"
                else (fill_price or 24500.0) + stop_distance
            )

            # Apply regime-based risk adjustment + retail failure-pattern de-risk
            risk_mult = self.get_risk_multiplier() * fo_mult
            adjusted_qty = int(base_qty * risk_mult)

            # Dynamic lot alignment (critical for BANKNIFTY 30 / SENSEX 20)
            lot_size = getattr(rg, '_get_lot_size', lambda s: rg.config.lot_size)(self.symbol) if hasattr(rg, '_get_lot_size') else rg.config.lot_size
            adjusted_qty = max(lot_size, (adjusted_qty // lot_size) * lot_size)

            qty = adjusted_qty
        except Exception:
            # Safe fallback per symbol
            lot_size = 65 if "NIFTY" in (self.symbol or "") else (30 if "BANK" in (self.symbol or "") else 20)
            qty = lot_size

        atr_at_entry = max(self.current_atr or 30.0, 20.0)
        stop_m = getattr(self.paper_params, "stop_loss_atr_mult", 1.1)
        entry_ref = fill_price or 24500.0
        if side == "BUY":
            protective_stop = round(entry_ref - (atr_at_entry * stop_m))
        else:
            protective_stop = round(entry_ref + (atr_at_entry * stop_m))

        result = rg.place_guarded_order(
            kite=self.kite,
            symbol=self.symbol,
            quantity=qty,
            transaction_type=side,
            price=fill_price,
            is_exit=False,
            protective_stop=protective_stop,
        )

        if result.get("success"):
            is_confirmed = bool(result.get("dry_run") or result.get("position_updated"))
            if is_confirmed:
                self._sync_entry_trade_state(
                    side=side,
                    qty=qty,
                    entry_p=float(result.get("fill_price") or self._last_known_price or 0.0),
                    atr_at_entry=atr_at_entry,
                    protective_stop=protective_stop,
                )
            else:
                self._pending_entry_meta = {
                    "side": side,
                    "qty": qty,
                    "protective_stop": protective_stop,
                    "atr_at_entry": atr_at_entry,
                }
                logger.info(
                    "%s live entry submitted — awaiting broker fill (order_id=%s)",
                    side,
                    result.get("order_id"),
                )
                trade_ledger.record("order.pending_fill", {
                    "symbol": self.symbol,
                    "side": side,
                    "quantity": qty,
                    "order_id": result.get("order_id"),
                    "protective_stop": protective_stop,
                })

            logger.info(f"{side} order submitted via Gatekeeper | qty={qty} | {result.get('order_id')}")

            trade_ledger.record("order.placed", {
                "symbol": self.symbol,
                "side": side,
                "quantity": qty,
                "price": self._last_known_price,
                "order_id": result.get("order_id"),
                "dry_run": result.get("dry_run", True),
                "awaiting_fill": not is_confirmed,
            })
        else:
            log_risk_block(result.get("message", "unknown"), {"side": side, "qty": qty})

    def _place_order_exit(self):
        """Exit the current position through the Risk Gatekeeper (per-symbol aware)."""
        self._invalidate_price_cache()
        rg = self._rg
        try:
            if rg.is_flat(self.symbol if hasattr(rg, 'positions') else None):
                return
            position_qty = rg.get_position_quantity(self.symbol) if hasattr(rg, 'get_position_quantity') else rg.get_position_quantity()
        except Exception:
            if rg.is_flat():
                return
            position_qty = rg.get_position_quantity()
        if position_qty == 0:
            return

        side = "SELL" if position_qty > 0 else "BUY"
        qty = abs(position_qty)

        exit_price = float(self._last_known_price or 0.0)
        rg = self._rg
        result = rg.place_guarded_order(
            kite=self.kite,
            symbol=self.symbol,
            quantity=qty,
            transaction_type=side,
            price=exit_price,
            is_exit=True,
        )

        if result.get("success"):
            self.last_trade_time = time.time()
            logger.info(f"Exit order submitted via Gatekeeper | {result.get('order_id')}")

            trade_ledger.record("order.exit", {
                "symbol": self.symbol,
                "side": side,
                "quantity": qty,
                "order_id": result.get("order_id"),
                "awaiting_fill": not (result.get("dry_run") or result.get("position_updated")),
            })

            if result.get("dry_run") or result.get("position_updated"):
                self._clear_trade_state()
        else:
            log_risk_block(result.get("message", "unknown"), {"side": side, "qty": qty})


if __name__ == "__main__":
    logger = logging.getLogger(__name__)
    logger.info("Starting Strategy in Standalone Mode...")
    state_machine.set_state(SystemState.PAPER_MODE)
    kite = KiteConnect(api_key=KITE_API_KEY)
    kite.set_access_token(KITE_ACCESS_TOKEN)
    strategy = PreviousCandleBreakoutStrategy(kite)
    strategy.run()