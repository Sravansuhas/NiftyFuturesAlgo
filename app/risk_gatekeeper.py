import time
import datetime
from kiteconnect import KiteConnect
from state_machine import state_machine, SystemState


class RiskGatekeeper:
    def __init__(self, capital: float = 1_000_000.0):
        self.capital = capital
        self.daily_loss = 0.0
        self.max_daily_loss_pct = 0.02
        self.max_drawdown_pct = 0.08
        self.risk_per_trade_pct = 0.01

        self.position = {
            "symbol": None,
            "quantity": 0,
            "avg_price": 0.0,
            "last_updated": None
        }

        self.last_reconciliation_time = time.time()
        print("✅ RiskGatekeeper initialized (stateful position tracking active)")

    def sync_with_broker(self, broker_net_positions: list):
        nifty_positions = [p for p in broker_net_positions 
                           if p.get("tradingsymbol", "").startswith("NIFTY")]

        if not nifty_positions:
            if self.position["quantity"] != 0:
                print("🚨 MISMATCH: We thought we had a position but broker shows FLAT")
                self._trigger_mismatch_alarm()
            self._reset_position()
            return

        pos = nifty_positions[0]
        broker_qty = pos.get("quantity", 0)
        broker_symbol = pos.get("tradingsymbol")
        broker_avg = pos.get("average_price", 0.0)

        if self.position["quantity"] != broker_qty or self.position["symbol"] != broker_symbol:
            print(f"🚨 POSITION MISMATCH DETECTED")
            print(f"   Internal: {self.position['symbol']} x {self.position['quantity']}")
            print(f"   Broker  : {broker_symbol} x {broker_qty}")
            self._trigger_mismatch_alarm()

        self.position["symbol"] = broker_symbol
        self.position["quantity"] = broker_qty
        self.position["avg_price"] = broker_avg
        self.position["last_updated"] = time.time()
        self.last_reconciliation_time = time.time()

    def _reset_position(self):
        self.position["symbol"] = None
        self.position["quantity"] = 0
        self.position["avg_price"] = 0.0
        self.position["last_updated"] = time.time()

    def _trigger_mismatch_alarm(self):
        state_machine.set_state(SystemState.RECONCILIATION_FAILED)

    def has_open_position(self) -> bool:
        return self.position["quantity"] != 0

    def get_position_quantity(self) -> int:
        return self.position["quantity"]

    def is_long(self) -> bool:
        return self.position["quantity"] > 0

    def is_short(self) -> bool:
        return self.position["quantity"] < 0

    def is_flat(self) -> bool:
        return self.position["quantity"] == 0

    def can_place_order(self, is_exit: bool = False) -> bool:
        if not state_machine.is_trading_allowed():
            print("🚫 can_place_order: Trading not allowed in current state")
            return False

        if self.daily_loss >= self.max_daily_loss_pct * self.capital:
            print("🚫 can_place_order: Daily loss limit reached")
            return False

        # Only block new entries when already in a position.
        # Allow exits even if we have an open position.
        if self.has_open_position() and not is_exit:
            print("🚫 can_place_order: Already have an open position")
            return False

        return True
    
    def place_guarded_order(self, kite, symbol: str, quantity: int,
                            transaction_type: str, price: float = 0.0,
                            order_type: str = "MARKET", product: str = "MIS",
                            validity: str = "DAY", dry_run: bool = None,
                            is_exit: bool = False) -> dict:
        """
        Smart guarded order placement with automatic dry_run detection.

        - If dry_run is None → Automatically decides based on market hours + holidays
        - Uses market_calendar.is_market_open()
        """

        from market_calendar import is_market_open

        # Auto decide dry_run if not explicitly provided
        if dry_run is None:
            dry_run = not is_market_open()

        mode = "DRY RUN" if dry_run else "REAL"
        print(f"\n[{mode}] Attempting guarded order → {transaction_type} {quantity} {symbol}")

        # Safety Gate (respects is_exit flag)
        if not self.can_place_order(is_exit=is_exit):
            return {
                "success": False,
                "order_id": None,
                "message": "Order blocked by risk gates"
            }

        # Dry Run Mode
        if dry_run:
            if is_exit:
                # For exits, just reduce the position (no need to call on_order_placed)
                if self.position["quantity"] != 0:
                    # Simple position reduction for exit
                    if transaction_type.upper() == "SELL" and self.position["quantity"] > 0:
                        self.position["quantity"] -= quantity
                    elif transaction_type.upper() == "BUY" and self.position["quantity"] < 0:
                        self.position["quantity"] += quantity

                    if self.position["quantity"] == 0:
                        self.position["symbol"] = None
                        self.position["avg_price"] = 0.0

                print(f"[{mode}] Exit simulated successfully. Position updated.")
            else:
                # Normal entry
                self.on_order_placed(symbol, quantity, transaction_type, price)
                print(f"[{mode}] Simulated successfully. Optimistic position updated.")

            return {
                "success": True,
                "order_id": "DRY-RUN",
                "message": "Dry run completed successfully"
            }

        # Real Order Mode
        try:
            order_id = kite.place_order(
                variety="regular",
                exchange="NFO",
                tradingsymbol=symbol,
                transaction_type=transaction_type.upper(),
                quantity=quantity,
                product=product,
                order_type=order_type.upper(),
                price=price if order_type.upper() == "LIMIT" else 0,
                validity=validity
            )

            if order_id:
                self.on_order_placed(symbol, quantity, transaction_type, price)
                print(f"[{mode}] Order placed successfully → ID: {order_id}")
                return {
                    "success": True,
                    "order_id": order_id,
                    "message": "Real order placed successfully"
                }
            else:
                return {
                    "success": False,
                    "order_id": None,
                    "message": "No order ID returned from broker"
                }

        except Exception as e:
            print(f"[{mode}] Order placement failed: {str(e)}")
            return {
                "success": False,
                "order_id": None,
                "message": str(e)
            }

    def on_order_placed(self, symbol: str, quantity: int, side: str, avg_price: float):
        """Simulate a new order placement in test/demo mode."""
        if not self.can_place_order():
            return False

        if side.upper() not in {"BUY", "SELL"}:
            print(f"🚫 Invalid order side: {side}")
            return False

        if quantity <= 0:
            print("🚫 Invalid order quantity: must be positive")
            return False

        self.position["symbol"] = symbol
        self.position["quantity"] = quantity if side.upper() == "BUY" else -quantity
        self.position["avg_price"] = avg_price
        self.position["last_updated"] = time.time()

        print(f"✅ Simulated order placed: {side.upper()} {quantity} {symbol} @ ₹{avg_price:,.2f}")
        return True

    def check_all_gates(self) -> bool:
        if not state_machine.is_trading_allowed():
            print("🚫 Gate 1 FAILED: Trading not allowed in current state")
            return False

        if self.daily_loss >= self.max_daily_loss_pct * self.capital:
            print("🚫 Gate 2 FAILED: Daily loss limit reached")
            state_machine.set_state(SystemState.TRADING_DISABLED)
            return False

        print("✅ ALL RISK GATES PASSED")
        return True

    def update_daily_loss(self, realized_pnl: float):
        self.daily_loss += realized_pnl
        
    def print_position_status(self):
        """Prints current position for observability and debugging"""
        if self.is_flat():
            print("📊 Position: FLAT")
        else:
            direction = "LONG" if self.is_long() else "SHORT"
            qty = abs(self.position["quantity"])
            symbol = self.position["symbol"] or "N/A"
            avg = self.position["avg_price"]
            print(f"📊 Position: {direction} {qty} @ ₹{avg:,.2f} ({symbol})")

    def test_guarded_order(self, symbol: str, quantity: int, 
                           transaction_type: str, avg_price: float = 0.0) -> dict:
        """
        SAFE TEST VERSION - Does NOT place any real order.
        Use this to verify the full guarded flow before going live.

        Simulates:
        - can_place_order() check
        - Optimistic position update
        - What would happen on success / rejection

        Returns the same structure as place_guarded_order()
        """
        print(f"\n🧪 TEST: Attempting guarded order → {transaction_type} {quantity} {symbol}")

        # Step 1: Check all safety gates
        if not self.can_place_order():
            print("🚫 TEST BLOCKED by risk gates or existing position")
            return {
                "success": False,
                "order_id": None,
                "message": "Blocked by can_place_order()",
                "simulated": True
            }

        # Step 2: Simulate successful order placement
        print(f"✅ TEST: Order would be placed successfully (simulated)")

        # Step 3: Apply optimistic position update (same as real flow)
        self.on_order_placed(
            symbol=symbol,
            quantity=quantity,
            side=transaction_type,
            avg_price=avg_price
        )

        print(f"📈 TEST: Optimistic position updated → {self.position['quantity']} {symbol}")

        return {
            "success": True,
            "order_id": "TEST-ORDER-12345",   # Fake order ID for simulation
            "message": "Simulated order placed successfully",
            "simulated": True
        }

risk_gatekeeper = RiskGatekeeper(capital=1_000_000.0)
