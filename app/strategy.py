from abc import ABC, abstractmethod
import time
import random
import datetime
from kiteconnect import KiteConnect
from config import KITE_API_KEY, KITE_ACCESS_TOKEN
from .state_machine import state_machine, SystemState
from .risk_gatekeeper import risk_gatekeeper
from .audit_logger import audit_logger


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
        print(f"\n🚀 Strategy Started → {self.__class__.__name__} (PAPER_MODE)")
        self._initialize_nifty_future()
        last_status_time = time.time()

        while True:
            try:
                self.run_once()
                current_time = time.time()
                if current_time - last_status_time >= 30:
                    current_price = self._get_current_price()
                    pos = "FLAT" if self.position == 0 else ("LONG" if self.position > 0 else "SHORT")
                    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] LTP: {current_price:.2f} | Position: {pos} | Entry: {self.entry_price if self.entry_price else 'N/A'}")
                    last_status_time = current_time
            except Exception as e:
                print(f"❌ Strategy Error: {e}")
            time.sleep(5)

    def run_once(self):
        """Single iteration of the strategy decision loop. Safe for external drivers (main.py)."""
        if risk_gatekeeper.is_flat():
            if self.should_enter_long():
                self._enter("BUY")
            elif self.should_enter_short():
                self._enter("SELL")
        elif self.should_exit():
            self._exit()

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
            print(f"✅ Using active Nifty Future: {self.symbol} (Token: {self.instrument_token})")
        except Exception as e:
            print(f"⚠️ Failed to fetch active Nifty contract ({e}). Using safe fallback.")
            # Fallback must be a plausible near contract; user should login for real selection
            self.symbol = "NIFTY26JUNFUT"
            self.instrument_token = None

    def _enter(self, side: str):
        result = risk_gatekeeper.place_guarded_order(kite=self.kite, symbol=self.symbol, quantity=self.quantity, transaction_type=side, force_dry_run=True)
        if result["success"]:
            self.position = self.quantity if side == "BUY" else -self.quantity
            self.entry_price = result.get("price", 24550.0)
            print(f"✅ {side} Entry @ {self.entry_price} → {result.get('order_id')}")

    def _exit(self):
        if self.position == 0:
            return
        side = "SELL" if self.position > 0 else "BUY"
        result = risk_gatekeeper.place_guarded_order(kite=self.kite, symbol=self.symbol, quantity=abs(self.position), transaction_type=side, is_exit=True, force_dry_run=True)
        if result["success"]:
            print(f"✅ Exit Executed → {result.get('order_id')}")
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
                 vol_confirmation: bool = True):
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
        # Seed as soon as we have symbol
        self._initialize_nifty_future()
        self._seed_previous_candle()

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
                print(f"✅ Seeded previous candle: H={self.prev_high:.2f} L={self.prev_low:.2f} V={self.prev_volume}")
            else:
                print("⚠️ Insufficient historical candles for seeding previous candle (using conservative defaults)")
                self.prev_high = self._last_known_price + 15 if self._last_known_price else 0
                self.prev_low = self._last_known_price - 15 if self._last_known_price else 0
        except Exception as e:
            print(f"⚠️ Could not seed previous candle from history ({e}). Breakout may be delayed until first roll.")
            self.prev_high = 0.0
            self.prev_low = 0.0

    def _roll_previous_candle_if_needed(self, current_price: float):
        """Simple time-based candle roller using wall clock 5min buckets.
        In production replace with proper tick aggregation or websocket candles.
        """
        now = time.time()
        bucket = int(now // 300) * 300
        if self._current_candle["start_ts"] == 0:
            self._current_candle = {"start_ts": bucket, "open": current_price, "high": current_price, "low": current_price}
            return

        if bucket > self._current_candle["start_ts"]:
            # Roll
            if self._current_candle["high"] > 0:
                self.prev_high = self._current_candle["high"]
                self.prev_low = self._current_candle["low"]
                self.prev_volume = max(self.prev_volume, 80000)  # conservative if no real vol
            self._current_candle = {"start_ts": bucket, "open": current_price, "high": current_price, "low": current_price}
            self._last_candle_update = now
            # Re-seed from history occasionally for accurate volume
            if now - self._last_seed_time > 900:  # every 15min
                self._seed_previous_candle()

    def _update_daily_flags(self):
        """Reset per-day state (has_entered_today) on new trading day."""
        today = datetime.date.today()
        if self._last_day != today:
            self.has_entered_today = False
            self._last_day = today
            print(f"[STRATEGY] New trading day detected → has_entered_today reset")

    def should_enter_long(self) -> bool:
        self._update_daily_flags()
        if self.has_entered_today or self._is_edge_case():
            return False
        current_price = self._get_current_price()
        if current_price <= 0:
            return False
        self._roll_previous_candle_if_needed(current_price)
        current_volume = self._get_current_volume()
        vol_ok = (not self.vol_confirmation) or (self.prev_volume == 0) or (current_volume > self.prev_volume * 1.10)
        if self.prev_high > 0 and current_price > self.prev_high + 6 and vol_ok:
            self.has_entered_today = True
            print(f"📈 LONG BREAKOUT TRIGGERED → {current_price:.2f} > prevH {self.prev_high:.2f} (vol_ok={vol_ok})")
            return True
        return False

    def should_enter_short(self) -> bool:
        self._update_daily_flags()
        if self.has_entered_today or self._is_edge_case():
            return False
        current_price = self._get_current_price()
        if current_price <= 0:
            return False
        self._roll_previous_candle_if_needed(current_price)
        current_volume = self._get_current_volume()
        vol_ok = (not self.vol_confirmation) or (self.prev_volume == 0) or (current_volume > self.prev_volume * 1.10)
        if self.prev_low > 0 and current_price < self.prev_low - 6 and vol_ok:
            self.has_entered_today = True
            print(f"📉 SHORT BREAKOUT TRIGGERED → {current_price:.2f} < prevL {self.prev_low:.2f} (vol_ok={vol_ok})")
            return True
        return False

    def should_exit(self) -> bool:
        # Prefer risk_gatekeeper as source of truth for live position
        if risk_gatekeeper.is_flat():
            self.position = 0
            self.entry_price = 0.0
            return False
        # Sync local view
        self.position = risk_gatekeeper.get_position_quantity()
        if self.position == 0 or self.entry_price == 0:
            return False
        current_price = self._get_current_price()
        if current_price <= 0:
            return False
        pnl = (current_price - self.entry_price) * (1 if self.position > 0 else -1)
        if pnl >= self.profit_target:
            print(f"🎯 PROFIT TARGET HIT (+{pnl:.2f})")
            return True
        if pnl <= -self.stop_loss:
            print(f"🛑 STOP LOSS HIT ({pnl:.2f})")
            return True
        return False

    def _is_edge_case(self) -> bool:
        """Conservative entry filters: first 15min + near expiry days + holidays (via calendar)."""
        try:
            from .market_calendar import is_entry_window_open, is_market_open
            now = datetime.datetime.now()
            if not is_market_open(now) or not is_entry_window_open(now):
                return True
            # Caution on expiry day (Nifty FUT expiry usually last Thu of month)
            if now.weekday() == 3:  # Thursday
                if now.day >= 25 or (now.day >= 22 and now.month in (2, 4, 6, 9, 11)):  # rough last Thu
                    if now.hour >= 14:  # no new entries late on expiry
                        return True
        except Exception:
            pass
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
            if is_live:
                # Explicit failure in live - do not hallucinate price
                audit_logger.record("data.ltp_failed_live", {"symbol": self.symbol, "error": str(e)})
                print(f"🚨 LTP FAILED IN LIVE_MODE: {e} — trading paused for safety")
                raise DataFeedError(f"Live LTP unavailable for {self.symbol}: {e}") from e
            # Paper / dry-run only: limited simulation to keep demo running
            if self._ltp_warning_count % 10 == 1:
                print(f"⚠️ LTP failed (PAPER), using last-known + jitter: {e}")
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

    # --- Order execution now goes through risk (dynamic sizing, full guards) ---
    def _enter(self, side: str):
        if not self.kite or not self.symbol:
            print("Cannot enter: missing kite or symbol")
            return
        # Dynamic risk-based sizing (respects consecutive loss reduction, max lots etc)
        try:
            # Use a conservative stop distance based on strategy SL for sizing calc
            stop_distance = self.stop_loss + 5.0  # buffer
            qty = risk_gatekeeper.calculate_order_quantity(
                entry_price=self._last_known_price or 24500.0,
                stop_price=(self._last_known_price or 24500.0) - stop_distance if side == "BUY" else (self._last_known_price or 24500.0) + stop_distance
            )
        except Exception:
            qty = 75  # ultimate fallback 1 lot

        result = risk_gatekeeper.place_guarded_order(
            kite=self.kite,
            symbol=self.symbol,
            quantity=qty,
            transaction_type=side,
            is_exit=False,
            # force_dry_run removed — respect the global FORCE_DRY_RUN + risk gates
        )
        if result.get("success"):
            self.position = qty if side == "BUY" else -qty
            self.entry_price = result.get("price") or self._last_known_price or 0.0
            print(f"✅ {side} Entry @ ~{self.entry_price:.2f} qty={qty} → {result.get('order_id')}")
        else:
            print(f"❌ Entry blocked: {result.get('message')}")

    def _exit(self):
        if self.position == 0:
            return
        side = "SELL" if self.position > 0 else "BUY"
        qty = abs(self.position)
        result = risk_gatekeeper.place_guarded_order(
            kite=self.kite,
            symbol=self.symbol,
            quantity=qty,
            transaction_type=side,
            is_exit=True,
        )
        if result.get("success"):
            print(f"✅ Exit Executed → {result.get('order_id')}")
            self.position = 0
            self.entry_price = 0.0
        # Note: actual position zeroing happens via broker recon in main loop


if __name__ == "__main__":
    print("🚀 Starting Strategy in Standalone Mode...")
    state_machine.set_state(SystemState.PAPER_MODE)
    kite = KiteConnect(api_key=KITE_API_KEY)
    kite.set_access_token(KITE_ACCESS_TOKEN)
    strategy = PreviousCandleBreakoutStrategy(kite)
    strategy.run()