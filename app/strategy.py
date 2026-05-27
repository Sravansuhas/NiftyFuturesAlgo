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

import logging
logger = logging.getLogger(__name__)


class DataFeedError(Exception):
    """Raised when live market data (LTP) is unavailable in LIVE_MODE.
    Strategy must never silently simulate prices when real capital is at risk.
    """
    pass


class BaseStrategy(ABC):
    def __init__(self, kite: KiteConnect, symbol: str = None, quantity: int = 75):
        self.kite = kite
        self.symbol = symbol
        self.instrument_token = None
        self.quantity = quantity
        self.position = 0
        self.entry_price = 0.0

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
        """
        # Always prefer the gatekeeper's view of reality
        if risk_gatekeeper.is_flat():
            if self.should_enter_long():
                self._place_order("BUY")
            elif self.should_enter_short():
                self._place_order("SELL")
        else:
            # We have a position according to the gatekeeper
            if self.should_exit():
                self._place_order_exit()

    def _initialize_nifty_future(self):
        """Select the front-month active Nifty futures contract dynamically."""
        try:
            instruments = self.kite.instruments("NFO")
            nifty_futures = [
                i for i in instruments
                if i.get("name") == "NIFTY" and i.get("segment") == "NFO-FUT"
            ]
            nifty_futures.sort(key=lambda x: x.get("expiry", "9999-99-99"))
            if not nifty_futures:
                raise ValueError("No Nifty futures contracts returned by broker")
            active = nifty_futures[0]
            self.symbol = active["tradingsymbol"]
            self.instrument_token = active["instrument_token"]
            # Active contract selection logged at DEBUG to keep terminal calm during live hours
            logging.getLogger(__name__).debug(f"Using active Nifty Future: {self.symbol} (Token: {self.instrument_token})")
        except Exception as e:
            logger.warning(f"Failed to fetch active Nifty contract ({e}). Using safe fallback.")
            # Fallback must be a plausible near contract; user should login for real selection
            self.symbol = "NIFTY26JUNFUT"
            self.instrument_token = None

    def _enter(self, side: str):
        result = risk_gatekeeper.place_guarded_order(kite=self.kite, symbol=self.symbol, quantity=self.quantity, transaction_type=side, force_dry_run=True)
        if result["success"]:
            self.position = self.quantity if side == "BUY" else -self.quantity
            self.entry_price = result.get("price", 24550.0)
            logger.info(f"{side} Entry @ {self.entry_price} → {result.get('order_id')}")

    def _exit(self):
        if self.position == 0:
            return
        side = "SELL" if self.position > 0 else "BUY"
        result = risk_gatekeeper.place_guarded_order(kite=self.kite, symbol=self.symbol, quantity=abs(self.position), transaction_type=side, is_exit=True, force_dry_run=True)
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
                 vol_confirmation: bool = True, paper_params: PaperTradingParams = None):
        super().__init__(kite)
        self.has_entered_today = False
        self.prev_high = 0.0
        self.prev_low = 0.0
        self.prev_volume = 0
        self.profit_target = profit_target
        self.stop_loss = stop_loss
        self.entry_price = 0.0
        self._ltp_warning_count = 0
        self._last_candle_update = 0
        self._last_seed_time = 0
        self._last_known_price = 0.0
        self._current_candle = {"start_ts": 0, "high": 0.0, "low": 0.0, "open": 0.0}
        self.vol_confirmation = vol_confirmation
        self._last_day = None
        self.trades_today = 0
        self.last_trade_time = 0.0          # for cooldown

        # ATR state for live adaptive logic
        self.current_atr = 0.0              # Short-term ATR (14)
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
        if persisted and risk_gatekeeper.get_position_quantity() != 0:
            self.entry_price = persisted.get("entry_price", 0.0)
            self._entry_time = persisted.get("entry_time", time.time())
            self._best_price_in_trade = persisted.get("best_price", self.entry_price)
            logger.debug("Restored position context from previous session.")

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
        """Lightweight update for EMA and price history used by regime detection."""
        self._price_history.append(current_price)
        if len(self._price_history) > 50:
            self._price_history.pop(0)

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

        if risk_gatekeeper.trades_today >= self.paper_params.max_trades_per_day:
            log_signal_rejected("max_trades_per_day_reached", {"trades_today": risk_gatekeeper.trades_today})
            return False

        # Cooldown after recent trade
        if time.time() - self.last_trade_time < (self.paper_params.cooldown_minutes_after_trade * 60):
            log_signal_rejected("cooldown_active")
            return False

        if not risk_gatekeeper.can_place_order(is_exit=False):
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
            return False

        log_signal_accepted("LONG", current_price, {
            "buffer": round(breakout_buffer, 2),
            "vol_ok": vol_ok,
            "atr": round(self.current_atr, 2),
            "regime": market_regime,
            "risk_mult": round(risk_mult, 2)
        })

        trade_ledger.record("signal.accepted", {
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

        if risk_gatekeeper.trades_today >= self.paper_params.max_trades_per_day:
            log_signal_rejected("max_trades_per_day_reached", {"trades_today": risk_gatekeeper.trades_today})
            return False

        if time.time() - self.last_trade_time < (self.paper_params.cooldown_minutes_after_trade * 60):
            log_signal_rejected("cooldown_active")
            return False

        if not risk_gatekeeper.can_place_order(is_exit=False):
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

        trade_ledger.record("signal.accepted", {
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
        """
        if risk_gatekeeper.is_flat():
            return False

        current_price = self._get_current_price()
        if current_price <= 0:
            return False

        if self.entry_price == 0:
            return False

        is_long = risk_gatekeeper.is_long()
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

        # 3. Breakeven + Trailing Stop (ATR based)
        # Once we have +1.2R, move stop to breakeven + small buffer
        if pnl > abs(self.stop_loss) * 1.2:
            # Simple trailing stop: trail by 1.5 ATR behind the best price seen
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

        # 4. Time-based Exit (avoid sitting forever)
        time_in_trade = time.time() - getattr(self, '_entry_time', time.time())
        max_hold_seconds = 90 * 60

        if time_in_trade > max_hold_seconds and pnl < self.profit_target * 0.4:
            logger.info(f"Time-based exit after {int(time_in_trade/60)} min. PnL: {pnl:.2f}")
            return True

        return False

    def _is_edge_case(self) -> bool:
        """Conservative entry filters using proper calendar helpers + paper params."""
        try:
            # Use absolute import to be robust when running as script or in Docker
            from app.market_calendar import is_market_open, now_ist, is_expiry_day
            now = now_ist()

            # Session filter from paper params
            if now.time() < self.paper_params.session_start or now.time() > self.paper_params.session_end:
                log_signal_rejected("outside_paper_session", {
                    "current_time": str(now.time()),
                    "allowed": f"{self.paper_params.session_start} - {self.paper_params.session_end}"
                })
                return True

            if not is_market_open(now):
                log_signal_rejected("market_closed")
                return True

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
        """Fetch real LTP. In LIVE_MODE never falls back to random simulation (raises on failure)."""
        try:
            if not self.kite:
                raise DataFeedError("No Kite client available")
            ltp_data = self.kite.ltp(self.instrument_token) if self.instrument_token else self.kite.ltp(f"NFO:{self.symbol}")
            key = str(self.instrument_token) if self.instrument_token else f"NFO:{self.symbol}"
            price = float(ltp_data[key]["last_price"])
            self._last_known_price = price
            return price
        except DataFeedError:
            raise
        except Exception as e:
            self._ltp_warning_count += 1
            is_live = state_machine.get_state() == SystemState.LIVE_MODE
            log_ltp_issue(str(e), self.symbol or "NIFTY")
            if is_live:
                audit_logger.record("data.ltp_failed_live", {"symbol": self.symbol, "error": str(e)})
                logger.error(f"LTP FAILED IN LIVE_MODE: {e} — trading paused for safety")
                raise DataFeedError(f"Live LTP unavailable for {self.symbol}: {e}") from e
            # Paper / dry-run only
            if self._ltp_warning_count % 10 == 1:
                logger.warning(f"LTP failed (PAPER), using last-known + jitter: {e}")
            jitter = random.uniform(-25, 30)
            sim_price = (self._last_known_price or 24500.0) + jitter
            self._last_known_price = sim_price
            return sim_price

    def _get_current_volume(self) -> int:
        """Best-effort volume. Real-time volume not available from simple LTP; use seeded or conservative."""
        if self.prev_volume > 0:
            # Use recent real volume as baseline; intrabar estimate not reliable from LTP polling alone
            return max(int(self.prev_volume * 0.8), 70000)
        return 120000  # safe neutral default when no history

    def _place_order(self, side: str):
        """Place an entry order through the Risk Gatekeeper with regime-aware sizing."""
        if not self.kite or not self.symbol:
            log_signal_rejected("missing_kite_or_symbol")
            return

        try:
            stop_distance = max(8.0, self.stop_loss)
            base_qty = risk_gatekeeper.calculate_order_quantity(
                entry_price=self._last_known_price or 24500.0,
                stop_price=(self._last_known_price or 24500.0) - stop_distance if side == "BUY" 
                else (self._last_known_price or 24500.0) + stop_distance
            )

            # Apply regime-based risk adjustment
            risk_mult = self.get_risk_multiplier()
            adjusted_qty = int(base_qty * risk_mult)
            # Ensure lot alignment
            lot_size = risk_gatekeeper.config.lot_size
            adjusted_qty = max(lot_size, (adjusted_qty // lot_size) * lot_size)

            qty = adjusted_qty
        except Exception:
            qty = 75

        result = risk_gatekeeper.place_guarded_order(
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

            logger.info(f"{side} order submitted via Gatekeeper | qty={qty} | {result.get('order_id')}")

            trade_ledger.record("order.placed", {
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
        """Exit the current position through the Risk Gatekeeper."""
        if risk_gatekeeper.is_flat():
            return

        position_qty = risk_gatekeeper.get_position_quantity()
        if position_qty == 0:
            return

        side = "SELL" if position_qty > 0 else "BUY"
        qty = abs(position_qty)

        result = risk_gatekeeper.place_guarded_order(
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