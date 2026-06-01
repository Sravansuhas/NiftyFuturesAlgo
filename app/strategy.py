from abc import ABC, abstractmethod
import time
import random
import datetime
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
)
from .trade_ledger import trade_ledger
from .state_persistence import save_strategy_state, load_strategy_state, clear_strategy_state
from .market_calendar import is_market_open, now_ist, is_expiry_day

import logging
logger = logging.getLogger(__name__)


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
        self._initialize_nifty_future()
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
        # Always refresh from source (polling or sim) so UI + terminal show live prices for ALL symbols
        try:
            current_price = self._get_current_price()
        except Exception:
            current_price = getattr(self, '_last_known_price', 0) or 0
        self._last_known_price = current_price if current_price else getattr(self, '_last_known_price', 0)

        # Staleness tracking (especially useful for diagnosing SENSEX lag)
        self._last_price_update_ts = time.time()

        # For live demo feel, strongly prefer the responsive fast_atr over the slow seeded 5-min ATR
        slow_atr = getattr(self, 'current_atr', 0) or 0
        fast = getattr(self, 'fast_atr', 0) or 0
        atr = fast if fast > slow_atr * 0.3 else max(slow_atr, fast, 1.0)  # avoid tiny fast_atr dominating in quiet periods

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

        # Compute dynamic position health first (for held positions) so "confidence" key is never stale 92%
        health_conf = None
        if getattr(self, 'entry_price', 0) > 0:
            health_conf = self._compute_position_health_conf(current_price)

        # Accurate data source tracking
        data_source = "SIMULATED"
        if hasattr(self, '_last_successful_real_price') and self._last_successful_real_price is not None:
            data_source = "REAL"

        # Warming up state for better UX during first 90 seconds
        is_warming_up = (data_source == "SIMULATED") and (time.time() - getattr(self, '_start_time', 0) < 90)

        snap = {
            "symbol": self.symbol,
            "ltp": round(current_price, 2) if current_price else 0,
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
        """
        try:
            from .instruments_manager import instruments_manager
            instruments_manager.kite = self.kite
            active = instruments_manager.get_active_future(index_name)

            if active:
                self.symbol = active["tradingsymbol"]
                self.instrument_token = active["instrument_token"]
                logging.getLogger(__name__).info(f"Using active {index_name} Future via InstrumentsManager: {self.symbol}")
                return
            else:
                raise ValueError(f"No active future returned by InstrumentsManager for {index_name}")

        except Exception as e:
            logger.warning(f"InstrumentsManager failed for {index_name}: {e}. Using fallback.")
            fallbacks = {
                "NIFTY": "NIFTY26JUNFUT",
                "BANKNIFTY": "BANKNIFTY26JUNFUT",
                "SENSEX": "SENSEX26JUNFUT"
            }
            self.symbol = fallbacks.get(index_name.upper(), "NIFTY26JUNFUT")
            self.instrument_token = None

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
        self._last_price_update_ts = 0.0  # for staleness detection (SENSEX lag diagnosis)
        self._start_time = time.time()            # for warming_up state
        self._current_candle = {"start_ts": 0, "high": 0.0, "low": 0.0, "open": 0.0}
        self.vol_confirmation = vol_confirmation
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

        # Seed as soon as we have symbol
        self._initialize_nifty_future()
        self._seed_previous_candle()

        # Attempt to restore critical state from previous run (for restart reliability)
        persisted = load_strategy_state()
        rg_has_position = False
        try:
            rg_has_position = self._rg.get_position_quantity(self.symbol) != 0 if hasattr(self._rg, 'get_position_quantity') else False
        except:
            pass

        if persisted and rg_has_position:
            self.entry_price = persisted.get("entry_price", 0.0)
            self._entry_time = persisted.get("entry_time", time.time())
            self._best_price_in_trade = persisted.get("best_price", self.entry_price)
            logger.debug("Restored position context from previous session.")

        # Fallback: If multi-symbol risk already has a position for us at startup, use its avg_price as entry
        if self.entry_price == 0 and rg_has_position:
            try:
                pos = self._rg.get_position(self.symbol) if hasattr(self._rg, 'get_position') else None
                if pos and pos.avg_price:
                    self.entry_price = pos.avg_price
                    # Also try to restore active levels if they exist on the risk side (future proof)
                    if hasattr(pos, 'active_target') and pos.active_target:
                        self.active_target = pos.active_target
                    if hasattr(pos, 'active_stop_loss') and pos.active_stop_loss:
                        self.active_stop_loss = pos.active_stop_loss
                    logger.debug(f"Restored entry_price from multi-risk for {self.symbol}")
            except Exception as e:
                logger.debug(f"Could not pull entry from multi-risk: {e}")

    def _seed_previous_candle(self):
        """Seed prev_high/low/volume from the most recent completed 5min candle via historical data.
        This makes the breakout logic actually work on first tick instead of dummy 0 values.
        """
        if not self.kite or not self.symbol:
            self.prev_high = 0.0
            self.prev_low = 0.0
            return
        try:
            # Fetch last ~2 hours to guarantee at least one completed candle
            now = datetime.datetime.now(datetime.timezone.utc)  # Kite uses UTC in responses usually
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
                # Last candle is often still forming; use the one before it as "previous"
                prev = candles[-2]
                self.prev_high = float(prev.get("high", prev.get("close", 0)))
                self.prev_low = float(prev.get("low", prev.get("close", 0)))
                self.prev_volume = int(prev.get("volume", 100000))
                self._last_seed_time = time.time()
                logging.getLogger(__name__).debug(f"Seeded previous candle: H={self.prev_high:.2f} L={self.prev_low:.2f}")

                # Bootstrap ATR from historical candles (fixes the "ATR = 0 for first 70 mins" problem)
                trs = []
                for i in range(1, min(len(candles), 20)):
                    high = float(candles[-i].get("high", 0))
                    low = float(candles[-i].get("low", 0))
                    prev_close = float(candles[-i-1].get("close", candles[-i].get("close", 0))) if i+1 < len(candles) else float(candles[-i].get("close", 0))
                    tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
                    trs.append(tr)
                if trs:
                    self._tr_values = trs[-14:]  # keep up to 14
                    self.current_atr = sum(self._tr_values) / len(self._tr_values)
                    logging.getLogger(__name__).debug(f"Bootstrapped Live ATR from history: {self.current_atr:.2f}")

            else:
                logger.debug("Insufficient historical candles for seeding previous candle (using conservative defaults)")
                self.prev_high = self._last_known_price + 15 if self._last_known_price else 0
                self.prev_low = self._last_known_price - 15 if self._last_known_price else 0
        except Exception as e:
            logger.warning(f"Could not seed previous candle from history ({e}). Breakout may be delayed until first roll.")
            self.prev_high = 0.0
            self.prev_low = 0.0

    def _roll_previous_candle_if_needed(self, current_price: float):
        """Time-based 5-min candle roller + ATR calculation."""
        now = time.time()
        bucket = int(now // 300) * 300

        if self._current_candle["start_ts"] == 0:
            self._current_candle = {"start_ts": bucket, "open": current_price, "high": current_price, "low": current_price}
            return

        if bucket > self._current_candle["start_ts"]:
            # Calculate True Range
            prev_close = self._current_candle["open"]
            tr = max(
                self._current_candle["high"] - self._current_candle["low"],
                abs(self._current_candle["high"] - prev_close),
                abs(self._current_candle["low"] - prev_close)
            )

            # Short-term ATR (14)
            self._tr_values.append(tr)
            if len(self._tr_values) > 14:
                self._tr_values.pop(0)
            self.current_atr = sum(self._tr_values) / len(self._tr_values) if self._tr_values else tr

            # Long-term ATR (50) for regime detection
            self._long_tr_values.append(tr)
            if len(self._long_tr_values) > 50:
                self._long_tr_values.pop(0)
            self.long_term_atr = sum(self._long_tr_values) / len(self._long_tr_values) if self._long_tr_values else self.current_atr

            # Roll previous candle
            if self._current_candle["high"] > 0:
                self.prev_high = self._current_candle["high"]
                self.prev_low = self._current_candle["low"]
                self.prev_volume = max(self.prev_volume, 80000)

            self._current_candle = {"start_ts": bucket, "open": current_price, "high": current_price, "low": current_price}
            self._last_candle_update = now

            if now - self._last_seed_time > 900:
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

    def get_market_regime(self) -> dict:
        """Combined regime view for decision making."""
        return {
            "volatility": self.get_volatility_regime(),
            "trend": self.get_trend_regime(),
            "htf_bias": self.get_higher_timeframe_bias()
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
        Suggests a risk adjustment based on current regime.
        Used by RiskGatekeeper or strategy for adaptive sizing.
        """
        vol = self.get_volatility_regime()
        trend = self.get_trend_regime()

        multiplier = 1.0

        if vol == "high":
            multiplier *= 0.65          # Reduce risk significantly in high vol
        elif vol == "low":
            multiplier *= 0.85          # Slightly more conservative in dead markets

        if trend == "ranging":
            multiplier *= 0.75          # Be very selective in ranging markets

        return max(0.4, min(1.2, multiplier))  # Safety bounds

    def should_enter_long(self) -> bool:
        """God-level decision function with ATR awareness + cooldown."""
        self._update_daily_flags()

        if self._is_edge_case():
            log_signal_rejected("edge_case_filter")
            return False

        rg = self.risk_manager or risk_gatekeeper
        trades_today = getattr(rg, 'trades_today', getattr(self._rg, 'trades_today', 0))

        if trades_today >= self.paper_params.max_trades_per_day:
            log_signal_rejected("max_trades_per_day_reached", {"trades_today": trades_today})
            return False

        # Cooldown after recent trade
        if time.time() - self.last_trade_time < (self.paper_params.cooldown_minutes_after_trade * 60):
            log_signal_rejected("cooldown_active")
            return False

        if not rg.can_place_order(self.symbol if self.risk_manager else None, is_exit=False):
            log_signal_rejected("risk_gatekeeper_denied_entry")
            return False

        current_price = self._get_current_price()
        if current_price <= 0:
            log_signal_rejected("invalid_price")
            return False

        self._roll_previous_candle_if_needed(current_price)
        self._update_atr_and_ema_for_live(current_price)   # ensure EMA/price history for regime

        # Minimum volatility filter
        if self.current_atr < self.paper_params.min_atr_points:
            if int(time.time()) % 30 == 0:
                log_signal_rejected("insufficient_volatility", {"current_atr": round(self.current_atr, 2)})
            return False

        current_volume = self._get_current_volume()
        vol_ok = (not self.vol_confirmation) or (self.prev_volume == 0) or (current_volume > self.prev_volume * self.paper_params.volume_mult)

        # Dynamic breakout buffer
        if self.paper_params.use_atr_breakout and self.current_atr > 0:
            breakout_buffer = self.current_atr * self.paper_params.breakout_atr_mult
        else:
            breakout_buffer = self.paper_params.breakout_buffer_points

        # Regime-aware logic
        market_regime = self.get_market_regime()
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

        if not (self.prev_high > 0 and current_price > self.prev_high + breakout_buffer and vol_ok):
            log_signal_rejected("long_breakout_not_met", {
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
            log_signal_rejected("edge_case_filter")
            return False

        rg = self.risk_manager or risk_gatekeeper
        trades_today = getattr(rg, 'trades_today', getattr(self._rg, 'trades_today', 0))

        if trades_today >= self.paper_params.max_trades_per_day:
            log_signal_rejected("max_trades_per_day_reached", {"trades_today": trades_today})
            return False

        if time.time() - self.last_trade_time < (self.paper_params.cooldown_minutes_after_trade * 60):
            log_signal_rejected("cooldown_active")
            return False

        if not rg.can_place_order(self.symbol if self.risk_manager else None, is_exit=False):
            log_signal_rejected("risk_gatekeeper_denied_entry")
            return False

        current_price = self._get_current_price()
        if current_price <= 0:
            log_signal_rejected("invalid_price")
            return False

        self._roll_previous_candle_if_needed(current_price)
        self._update_atr_and_ema_for_live(current_price)

        if self.current_atr < self.paper_params.min_atr_points:
            if int(time.time()) % 30 == 0:
                log_signal_rejected("insufficient_volatility", {"current_atr": round(self.current_atr, 2)})
            return False

        current_volume = self._get_current_volume()
        vol_ok = (not self.vol_confirmation) or (self.prev_volume == 0) or (current_volume > self.prev_volume * self.paper_params.volume_mult)

        if self.paper_params.use_atr_breakout and self.current_atr > 0:
            breakout_buffer = self.current_atr * self.paper_params.breakout_atr_mult
        else:
            breakout_buffer = self.paper_params.breakout_buffer_points

        market_regime = self.get_market_regime()
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

        if not (self.prev_low > 0 and current_price < self.prev_low - breakout_buffer and vol_ok):
            log_signal_rejected("short_breakout_not_met", {
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
            is_long = rg.is_long(self.symbol) if hasattr(rg, 'is_long') and 'symbol' in rg.is_long.__code__.co_varnames else rg.is_long()
        except Exception:
            is_long = rg.is_long() if hasattr(rg, 'is_long') else False
        pnl = (current_price - self.entry_price) * (1 if is_long else -1)
        atr = max(self.current_atr, 8.0)  # safety floor

        # 1. Hard Profit Target
        if pnl >= self.profit_target:
            logger.info(f"PROFIT TARGET HIT (+{pnl:.2f})")
            return True

        # 2. Hard Stop Loss
        if pnl <= -self.stop_loss:
            logger.info(f"STOP LOSS HIT ({pnl:.2f})")
            return True

        # 3. Breakeven + Trailing Stop (ATR based) + Dynamic active levels for better health confidence
        if pnl > abs(self.stop_loss) * 1.2:
            if not hasattr(self, '_best_price_in_trade'):
                self._best_price_in_trade = self.entry_price

            if is_long:
                self._best_price_in_trade = max(self._best_price_in_trade, current_price)
                trail_stop = self._best_price_in_trade - (1.5 * atr)
                if current_price < trail_stop:
                    logger.info(f"Trailing Stop hit (Long). PnL: {pnl:.2f}")
                    return True
            else:
                self._best_price_in_trade = min(self._best_price_in_trade, current_price)
                trail_stop = self._best_price_in_trade + (1.5 * atr)
                if current_price > trail_stop:
                    logger.info(f"Trailing Stop hit (Short). PnL: {pnl:.2f}")
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
            if is_long:
                new_sl = max(self.active_stop_loss, round(current_price - trail_atr * 1.2))
                if new_sl > self.active_stop_loss:
                    self.active_stop_loss = new_sl
            else:
                new_sl = min(self.active_stop_loss, round(current_price + trail_atr * 1.2))
                if new_sl < self.active_stop_loss:
                    self.active_stop_loss = new_sl

        # 4. Time-based Exit (avoid sitting forever)
        time_in_trade = time.time() - getattr(self, '_entry_time', time.time())
        max_hold_seconds = 90 * 60

        if time_in_trade > max_hold_seconds and pnl < self.profit_target * 0.4:
            logger.info(f"Time-based exit after {int(time_in_trade/60)} min. PnL: {pnl:.2f}")
            return True

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
                log_signal_rejected("outside_paper_session", {
                    "current_time": str(now.time()),
                    "allowed": f"{self.paper_params.session_start} - {self.paper_params.session_end}"
                })
                return True

            try:
                if not is_market_open(now):
                    log_signal_rejected("market_closed")
                    return True
            except Exception:
                # If calendar check fails, don't block trading on paper
                pass

            # Expiry day handling
            if self.paper_params.avoid_expiry_day:
                if is_expiry_day(now.date()):
                    if now.hour >= self.paper_params.expiry_day_cutoff_hour:
                        log_signal_rejected("expiry_day_safety", {"hour": now.hour})
                        return True

        except Exception as e:
            log_signal_rejected("calendar_check_failed", {"error": str(e)})
        return False

    def _get_current_price(self) -> float:
        """
        Real data first, aggressive retry during market hours.
        Only falls back to simulation in PAPER mode after real attempts fail.
        """
        # During open market, try harder to get real data
        attempts = 1
        try:
            if is_market_open():
                attempts = 3
        except Exception:
            attempts = 1  # safe default if calendar helper fails

        for attempt in range(attempts):
            try:
                if not self.kite:
                    raise DataFeedError("No Kite client available")

                import time as _t
                t0 = _t.time()

                if self.instrument_token:
                    ltp_data = self.kite.ltp([self.instrument_token])
                    key = str(self.instrument_token)
                else:
                    exchange = "BFO" if "SENSEX" in (self.symbol or "").upper() else "NFO"
                    ltp_data = self.kite.ltp([f"{exchange}:{self.symbol}"])
                    key = f"{exchange}:{self.symbol}"

                duration_ms = (_t.time() - t0) * 1000

                if key in ltp_data and "last_price" in ltp_data[key]:
                    price = float(ltp_data[key]["last_price"])
                    if price > 0:
                        self._last_known_price = price
                        self._last_successful_real_price = price

                        # === DIAGNOSTIC (Kite best practice + easy diagnosis) ===
                        try:
                            from .diagnostic_logger import diag
                            diag.log_price_fetch(self.symbol, price, "REAL", duration_ms, token=self.instrument_token)
                        except Exception:
                            pass
                        return price

                if attempt < attempts - 1:
                    continue

                raise Exception(f"No valid positive last_price for {key}")

            except Exception as e:
                if attempt < attempts - 1:
                    continue

                # Final failure after retries
                self._ltp_warning_count += 1
                if state_machine.get_state() == SystemState.LIVE_MODE:
                    logger.error(f"[LIVE] Real LTP failed for {self.symbol} after {attempts} attempts: {e}")
                    raise DataFeedError(f"Live LTP failed for {self.symbol}")

                # PAPER MODE fallback - prefer last real price as base if we ever had one. Never hardcode obvious fakes.
                logger.warning(f"[PAPER] Real data failed for {self.symbol} after retries. Using simulation anchored to last known.")

                # Special handling + logging for SENSEX lag (common in current polling setup)
                if "SENSEX" in (self.symbol or "").upper():
                    try:
                        from .diagnostic_logger import diag
                        diag.get_logger().warning(f"[SENSEX_LAG] SENSEX price updates are lagging. Last successful real: {self._last_successful_real_price}")
                    except Exception:
                        pass

                base = self._last_successful_real_price or self._last_known_price or 0
                if base < 100:
                    # Conservative last-resort seed (will be obvious in UI via data_source + warming flag)
                    base = 22000.0 if "NIFTY" in (self.symbol or "").upper() and "BANK" not in (self.symbol or "").upper() else (48000.0 if "BANKNIFTY" in (self.symbol or "").upper() else 74000.0)

                import random
                # Refined simulation for SENSEX (and others): cap jitter and adapt slower when real data is sparse
                vol = max(getattr(self, 'fast_atr', 0) or getattr(self, 'current_atr', 0) or 25.0, 15.0)

                # SENSEX-specific smoothing (its real updates can be slower → avoid crazy jumps in sim)
                if "SENSEX" in (self.symbol or "").upper():
                    vol = min(vol, 35.0)  # hard cap on simulated volatility for SENSEX
                    jitter = random.uniform(-vol * 0.35, vol * 0.35)  # gentler movement
                else:
                    jitter = random.uniform(-vol * 0.55, vol * 0.55)

                sim_price = base + jitter
                self._last_known_price = sim_price

                # === DIAGNOSTIC ===
                try:
                    from .diagnostic_logger import diag
                    diag.log_price_fetch(self.symbol, sim_price, "SIMULATED", 0.0)
                except Exception:
                    pass
                return sim_price

        return self._last_known_price or 0.0

    def _get_current_volume(self) -> int:
        """Best-effort volume. Real-time volume not available from simple LTP; use seeded or conservative."""
        if self.prev_volume > 0:
            # Use recent real volume as baseline; intrabar estimate not reliable from LTP polling alone
            return max(int(self.prev_volume * 0.8), 70000)
        return 120000  # safe neutral default when no history

    def _place_order(self, side: str):
        """Place an entry order through the Risk Gatekeeper (or multi-symbol manager) with regime-aware sizing."""
        if not self.kite or not self.symbol:
            log_signal_rejected("missing_kite_or_symbol")
            return

        rg = self.risk_manager or risk_gatekeeper

        try:
            stop_distance = max(8.0, self.stop_loss)
            base_qty = rg.calculate_order_quantity(
                self.symbol,
                entry_price=self._last_known_price or 24500.0,
                stop_price=(self._last_known_price or 24500.0) - stop_distance if side == "BUY" 
                else (self._last_known_price or 24500.0) + stop_distance
            )

            # Apply regime-based risk adjustment
            risk_mult = self.get_risk_multiplier()
            adjusted_qty = int(base_qty * risk_mult)

            # Dynamic lot alignment (critical for BANKNIFTY 30 / SENSEX 20)
            lot_size = getattr(rg, '_get_lot_size', lambda s: rg.config.lot_size)(self.symbol) if hasattr(rg, '_get_lot_size') else rg.config.lot_size
            adjusted_qty = max(lot_size, (adjusted_qty // lot_size) * lot_size)

            qty = adjusted_qty
        except Exception:
            # Safe fallback per symbol
            lot_size = 65 if "NIFTY" in (self.symbol or "") else (30 if "BANK" in (self.symbol or "") else 20)
            qty = lot_size

        result = rg.place_guarded_order(
            kite=self.kite,
            symbol=self.symbol,
            quantity=qty,
            transaction_type=side,
            is_exit=False
        )

        if result.get("success"):
            self.last_trade_time = time.time()
            self._entry_time = time.time()
            # Use last known price (safe fallback). 'current_price' is not in scope here.
            self._best_price_in_trade = self._last_known_price or 0.0

            # === CRITICAL STATE SYNC (best principle: strategy owns its trade context) ===
            entry_p = self._last_known_price or 0.0
            self.entry_price = entry_p

            # Store realistic target/SL for this specific trade
            atr_at_entry = max(self.current_atr or 30.0, 20.0)
            profit_m = getattr(self.paper_params, 'profit_target_atr_mult', 2.0)
            stop_m = getattr(self.paper_params, 'stop_loss_atr_mult', 1.1)
            if entry_p > 0:
                self.active_target = round(entry_p + (atr_at_entry * profit_m))
                self.active_stop_loss = round(entry_p - (atr_at_entry * stop_m))

            # Granular diagnostic on successful position state update
            try:
                from .diagnostic_logger import diag
                diag.log_signal_decision(self.symbol, "POSITION_STATE_SET", {
                    "entry_price": self.entry_price,
                    "active_target": self.active_target,
                    "active_stop_loss": self.active_stop_loss,
                    "qty": qty
                })
            except Exception:
                pass

            logger.info(f"{side} order submitted via Gatekeeper | qty={qty} | {result.get('order_id')}")

            trade_ledger.record("order.placed", {
                "symbol": self.symbol,
                "side": side,
                "quantity": qty,
                "price": self._last_known_price,
                "order_id": result.get("order_id"),
                "dry_run": result.get("dry_run", True)
            })

            # Persist critical state for restart reliability
            save_strategy_state({
                "entry_price": self.entry_price,
                "entry_time": self._entry_time,
                "best_price": getattr(self, "_best_price_in_trade", self.entry_price),
                "symbol": self.symbol
            })
        else:
            log_risk_block(result.get("message", "unknown"), {"side": side, "qty": qty})

    def _place_order_exit(self):
        """Exit the current position through the Risk Gatekeeper (per-symbol aware)."""
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

        rg = self._rg
        result = rg.place_guarded_order(
            kite=self.kite,
            symbol=self.symbol,
            quantity=qty,
            transaction_type=side,
            is_exit=True
        )

        if result.get("success"):
            self.last_trade_time = time.time()
            logger.info(f"Exit order submitted via Gatekeeper | {result.get('order_id')}")

            trade_ledger.record("order.exit", {
                "symbol": self.symbol,
                "side": side,
                "quantity": qty,
                "order_id": result.get("order_id")
            })

            clear_strategy_state()  # Position closed, no need to persist context
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