from abc import ABC, abstractmethod
import time
from kiteconnect import KiteConnect
from config import KITE_API_KEY, KITE_ACCESS_TOKEN
from state_machine import state_machine, SystemState
from risk_gatekeeper import risk_gatekeeper

class BaseStrategy(ABC):
    def __init__(self, kite: KiteConnect, symbol: str = "NIFTY25MAYFUT", quantity: int = 75):
        self.kite = kite
        self.symbol = symbol
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
        print(f"\n🚀 Strategy Started → {self.__class__.__name__}")

        while True:
            try:
                if risk_gatekeeper.is_flat():
                    if self.should_enter_long():
                        self._enter("BUY")
                    elif self.should_enter_short():
                        self._enter("SELL")
                elif self.should_exit():
                    self._exit()
            except Exception as e:
                print(f"❌ Strategy Error: {e}")
            time.sleep(5)

    def _enter(self, side: str):
        result = risk_gatekeeper.place_guarded_order(
            kite=self.kite,
            symbol=self.symbol,
            quantity=self.quantity,
            transaction_type=side,
            dry_run=True          # ← Force dry run for today’s testing
        )
        if result["success"]:
            self.position = self.quantity if side == "BUY" else -self.quantity
            self.entry_price = 24550.0
            print(f"✅ {side} Entry @ {self.entry_price} → {result.get('order_id')}")

    def _exit(self):
        if self.position == 0:
            return

        side = "SELL" if self.position > 0 else "BUY"

        # Use place_guarded_order with is_exit=True so it doesn't get blocked by position check
        result = risk_gatekeeper.place_guarded_order(
            kite=self.kite,
            symbol=self.symbol,
            quantity=abs(self.position),
            transaction_type=side,
            is_exit=True          # ← New parameter
        )

        if result["success"]:
            print(f"✅ Exit Executed → {result.get('order_id')}")
            self.position = 0
            self.entry_price = 0.0
# ============================================================
# Test Strategy with Profit Target + Stop Loss
# ============================================================
class TestStrategy(BaseStrategy):
    """
    Simple but complete test strategy for dry-run validation.
    - Enters Long once
    - Exits when Profit Target or Stop Loss is hit (simulated)
    """
class TestStrategy(BaseStrategy):
    def __init__(self, kite: KiteConnect, 
                 profit_target: float = 25.0, 
                 stop_loss: float = 15.0,
                 simulated_move: float = 30.0,
                 force_dry_run: bool = True):        # ← Add this
        super().__init__(kite)
        self.has_entered = False
        self.profit_target = profit_target
        self.stop_loss = stop_loss
        self.simulated_move = simulated_move
        self.current_simulated_price = 0.0
        self.force_dry_run = force_dry_run            # ← Add this
        
    def should_enter_long(self) -> bool:
        if not self.has_entered and risk_gatekeeper.is_flat():
            self.has_entered = True
            return True
        return False

    def should_enter_short(self) -> bool:
        return False

    def should_exit(self) -> bool:
        if self.position == 0 or self.entry_price == 0:
            return False

        current_price = self._get_current_simulated_price()
        if current_price == 0:
            return False

        pnl = current_price - self.entry_price if self.position > 0 else self.entry_price - current_price

        if pnl >= self.profit_target:
            print(f"🎯 Profit Target Hit (+{pnl:.2f} points)")
            return True

        if pnl <= -self.stop_loss:
            print(f"🛑 Stop Loss Hit ({pnl:.2f} points)")
            return True

        return False

    def _get_current_simulated_price(self) -> float:
        """
        Dynamic simulation for dry-run testing.
        With 65% probability → favorable move (hits profit target)
        With 35% probability → unfavorable move (hits stop loss)
        """
        import random

        if self.entry_price == 0:
            return 0.0

        # Randomly decide outcome
        if random.random() < 0.65:  
            # Favorable move → hit profit target
            self.current_simulated_price = self.entry_price + self.profit_target + 5
        else:
            # Unfavorable move → hit stop loss
            self.current_simulated_price = self.entry_price - self.stop_loss - 3

        return self.current_simulated_price

# ============================================================
# Run Strategy (Standalone)
# ============================================================
if __name__ == "__main__":
    print("🚀 Starting TestStrategy in Standalone Mode...")

    state_machine.set_state(SystemState.PAPER_MODE)

    kite = KiteConnect(api_key=KITE_API_KEY)
    kite.set_access_token(KITE_ACCESS_TOKEN)

    strategy = TestStrategy(kite)
    strategy.run()