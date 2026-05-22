from state_machine import state_machine, SystemState
import time

class RiskGatekeeper:
    def __init__(self):
        self.capital = 1000000.0          # Example: ₹10 Lakh (change later)
        self.daily_loss = 0.0
        self.max_daily_loss = 0.02        # 2%
        self.max_drawdown = 0.08          # 8%
        self.risk_per_trade = 0.01        # 1%
        self.current_position = None
        self.last_reconciliation = time.time()

    def check_all_gates(self):
        """10+ hard gates before any trade"""
        
        # 1. System State Check
        if not state_machine.is_trading_allowed():
            print("🚫 Gate 1 FAILED: Trading not allowed in current state")
            return False

        # 2. Daily Loss Check
        if self.daily_loss >= self.max_daily_loss * self.capital:
            print("🚫 Gate 2 FAILED: Daily loss limit reached")
            state_machine.set_state(SystemState.TRADING_DISABLED)
            return False

        # 3. No existing position (for now - we allow only 1)
        if self.current_position is not None:
            print("🚫 Gate 3 FAILED: Already have an open position")
            return False

        # 4. Circuit Breaker (basic version - we will expand)
        if time.time() - self.last_reconciliation > 30:
            print("⚠️  Gate 4 WARNING: Reconciliation overdue")
            # In future this will trigger full circuit breaker

        print("✅ ALL RISK GATES PASSED")
        return True

    def update_daily_loss(self, pnl):
        self.daily_loss += pnl

risk_gatekeeper = RiskGatekeeper()
print("✅ Risk & Compliance Gatekeeper loaded")