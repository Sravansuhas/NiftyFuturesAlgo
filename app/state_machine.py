from enum import Enum
import threading

class SystemState(Enum):
    BOOTING = "BOOTING"
    IDLE = "IDLE"
    PRE_MARKET = "PRE_MARKET"
    MARKET_OPEN = "MARKET_OPEN"
    TRADING_ENABLED = "TRADING_ENABLED"
    TRADING_DISABLED = "TRADING_DISABLED"
    SHADOW_MODE = "SHADOW_MODE"
    PAPER_MODE = "PAPER_MODE"
    LIVE_MODE = "LIVE_MODE"
    CIRCUIT_BREAKER_TRIGGERED = "CIRCUIT_BREAKER_TRIGGERED"
    RECONCILIATION_FAILED = "RECONCILIATION_FAILED"
    BROKER_DISCONNECTED = "BROKER_DISCONNECTED"
    EMERGENCY_HALT = "EMERGENCY_HALT"
    POST_MARKET = "POST_MARKET"

class TradingStateMachine:
    def __init__(self):
        self._state = SystemState.BOOTING
        self._lock = threading.Lock()
    
    def get_state(self):
        with self._lock:
            return self._state
    
    def set_state(self, new_state: SystemState):
        with self._lock:
            old_state = self._state
            self._state = new_state
            print(f"[STATE MACHINE] Transition: {old_state.value} -> {new_state.value}")
    
    def is_trading_allowed(self):
        allowed = {SystemState.TRADING_ENABLED, SystemState.PAPER_MODE, SystemState.LIVE_MODE}
        return self._state in allowed

    def is_paper_or_live(self):
        return self._state in {SystemState.PAPER_MODE, SystemState.LIVE_MODE}

    def emergency_halt(self, reason: str = ""):
        self.set_state(SystemState.EMERGENCY_HALT)
        print(f"[STATE] EMERGENCY HALT: {reason}")

state_machine = TradingStateMachine()
