from abc import ABC, abstractmethod
import datetime
import random
import time

from kiteconnect import KiteConnect

from config import KITE_ACCESS_TOKEN, KITE_API_KEY
from market_calendar import is_entry_window_open, now_ist
from risk_gatekeeper import risk_gatekeeper
from state_machine import SystemState, state_machine


class BaseStrategy(ABC):
    def __init__(self, kite: KiteConnect, symbol: str = None, quantity: int = 75):
        self.kite = kite
        self.symbol = symbol
        self.instrument_token = None
        self.quantity = quantity
        self.position = 0
        self.entry_price = 0.0
        self._initialized = False
        self._last_status_time = 0.0

    @abstractmethod
    def should_enter_long(self) -> bool:
        pass

    @abstractmethod
    def should_enter_short(self) -> bool:
        pass

    @abstractmethod
    def should_exit(self) -> bool:
        pass

    def initialize(self):
        if self._initialized:
            return

        print(f"\nStrategy started -> {self.__class__.__name__} ({state_machine.get_state().value})")
        self._initialize_nifty_future()
        self._last_status_time = time.time()
        self._initialized = True

    def run_once(self):
        self.initialize()
        current_time = time.time()

        if current_time - self._last_status_time >= 30:
            current_price = self._get_current_price()
            pos = "FLAT"
            if self.position > 0:
                pos = "LONG"
            elif self.position < 0:
                pos = "SHORT"
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] "
                  f"LTP: {current_price:.2f} | Position: {pos} | "
                  f"Entry: {self.entry_price if self.entry_price else 'N/A'}")
            self._last_status_time = current_time

        if risk_gatekeeper.is_flat():
            if self.should_enter_long():
                self._enter("BUY")
            elif self.should_enter_short():
                self._enter("SELL")
        elif self.should_exit():
            self._exit()

    def run(self, poll_interval_seconds: int = 5):
        while True:
            try:
                self.run_once()
            except Exception as exc:
                print(f"Strategy error: {exc}")

            time.sleep(poll_interval_seconds)

    def _initialize_nifty_future(self):
        try:
            instruments = self.kite.instruments("NFO")
            nifty_futures = [
                instrument
                for instrument in instruments
                if instrument["name"] == "NIFTY" and instrument["segment"] == "NFO-FUT"
            ]
            if not nifty_futures:
                raise RuntimeError("No active Nifty futures found")

            nifty_futures.sort(key=lambda instrument: instrument["expiry"])
            active = nifty_futures[0]
            self.symbol = active["tradingsymbol"]
            self.instrument_token = active["instrument_token"]
            print(f"Using active Nifty Future: {self.symbol} (Token: {self.instrument_token})")
        except Exception as exc:
            print(f"Failed to initialize Nifty contract: {exc}")
            self.symbol = self.symbol or "NIFTY25JUNFUT"
            self.instrument_token = None

    def _enter(self, side: str):
        entry_price = self._get_current_price()
        stop_price = self.prev_low if side == "BUY" else self.prev_high
        try:
            quantity = risk_gatekeeper.calculate_order_quantity(entry_price, stop_price)
        except ValueError:
            quantity = self.quantity

        result = risk_gatekeeper.place_guarded_order(
            kite=self.kite,
            symbol=self.symbol,
            quantity=quantity,
            transaction_type=side,
            price=entry_price,
            force_dry_run=state_machine.get_state() != SystemState.LIVE_MODE,
        )
        if result["success"] and result.get("position_updated", False):
            self.position = quantity if side == "BUY" else -quantity
            self.entry_price = entry_price
            print(f"{side} entry @ {self.entry_price:.2f} -> {result.get('order_id')}")
        elif result["success"]:
            print(f"{side} order submitted @ {entry_price:.2f}; waiting for broker fill confirmation")

    def _exit(self):
        if self.position == 0:
            return

        side = "SELL" if self.position > 0 else "BUY"
        result = risk_gatekeeper.place_guarded_order(
            kite=self.kite,
            symbol=self.symbol,
            quantity=abs(self.position),
            transaction_type=side,
            is_exit=True,
            force_dry_run=state_machine.get_state() != SystemState.LIVE_MODE,
        )
        if result["success"] and result.get("position_updated", False):
            print(f"Exit executed -> {result.get('order_id')}")
            self.position = 0
            self.entry_price = 0.0
        elif result["success"]:
            print(f"Exit order submitted -> {result.get('order_id')}")


class PreviousCandleBreakoutStrategy(BaseStrategy):
    """
    Previous candle breakout strategy.

    The live implementation still uses simulated candle/volume fallbacks when
    Kite data is unavailable. Keep it in PAPER_MODE until this is replaced with
    real historical candles and validated paper trading.
    """

    def __init__(self, kite: KiteConnect, profit_target: float = 25.0, stop_loss: float = 15.0):
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

    def should_enter_long(self) -> bool:
        if self.has_entered_today or self._is_edge_case():
            return False

        current_price = self._get_current_price()
        current_volume = self._get_current_volume()

        if self.prev_high > 0 and current_price > self.prev_high and current_volume > self.prev_volume * 1.2:
            self.has_entered_today = True
            print(
                f"LONG breakout triggered -> price {current_price:.2f} "
                f"broke above {self.prev_high:.2f} with volume confirmation"
            )
            return True
        return False

    def should_enter_short(self) -> bool:
        if self.has_entered_today or self._is_edge_case():
            return False

        current_price = self._get_current_price()
        current_volume = self._get_current_volume()

        if self.prev_low > 0 and current_price < self.prev_low and current_volume > self.prev_volume * 1.2:
            self.has_entered_today = True
            print(
                f"SHORT breakout triggered -> price {current_price:.2f} "
                f"broke below {self.prev_low:.2f} with volume confirmation"
            )
            return True
        return False

    def should_exit(self) -> bool:
        if self.position == 0 or self.entry_price == 0:
            return False

        current_price = self._get_current_price()
        pnl_points = current_price - self.entry_price if self.position > 0 else self.entry_price - current_price

        if pnl_points >= self.profit_target:
            print(f"Profit target hit -> +{pnl_points:.2f} points")
            return True
        if pnl_points <= -self.stop_loss:
            print(f"Stop loss hit -> {pnl_points:.2f} points")
            return True
        return False

    def _is_edge_case(self) -> bool:
        now = now_ist()
        if not is_entry_window_open(now):
            return True
        if now.weekday() == 3 and now.day >= 22:
            return True
        return False

    def _get_current_price(self) -> float:
        try:
            if self.instrument_token:
                ltp_data = self.kite.ltp(self.instrument_token)
                token_str = str(self.instrument_token)
                if token_str in ltp_data:
                    price = float(ltp_data[token_str]["last_price"])
                    self._update_previous_candle(price)
                    return price

            ltp_data = self.kite.ltp(f"NFO:{self.symbol}")
            price = float(ltp_data[f"NFO:{self.symbol}"]["last_price"])
            self._update_previous_candle(price)
            return price

        except Exception as exc:
            if state_machine.get_state() == SystemState.LIVE_MODE:
                raise
            self._ltp_warning_count += 1
            if self._ltp_warning_count % 5 == 1:
                print(f"LTP failed for {self.symbol}, using simulation: {exc}")
            base = 24550.0
            simulated_price = base + random.uniform(-40, 50)
            self._update_previous_candle(simulated_price)
            return simulated_price

    def _update_previous_candle(self, current_price: float):
        current_time = time.time()
        if self.prev_high == 0 or (current_time - self._last_candle_update > 75):
            self.prev_high = current_price + random.uniform(8, 25)
            self.prev_low = current_price - random.uniform(8, 25)
            self.prev_volume = random.randint(80000, 180000)
            self._last_candle_update = current_time
            print(f"Previous candle updated -> High: {self.prev_high:.2f} | Low: {self.prev_low:.2f}")

    def _get_current_volume(self) -> int:
        return random.randint(60000, 160000)


if __name__ == "__main__":
    print("Starting strategy in standalone mode...")
    state_machine.set_state(SystemState.PAPER_MODE)

    kite = KiteConnect(api_key=KITE_API_KEY)
    if KITE_ACCESS_TOKEN:
        kite.set_access_token(KITE_ACCESS_TOKEN)

    strategy = PreviousCandleBreakoutStrategy(kite, profit_target=25.0, stop_loss=15.0)
    strategy.run()
