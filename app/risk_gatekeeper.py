import time
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
        self.print_startup_status()

    # ============================================================
    # POSITION MANAGEMENT
    # ============================================================
    def has_open_position(self) -> bool:
        return self.position["quantity"] != 0

    def is_long(self) -> bool:
        return self.position["quantity"] > 0

    def is_short(self) -> bool:
        return self.position["quantity"] < 0

    def is_flat(self) -> bool:
        return self.position["quantity"] == 0

    def get_position_quantity(self) -> int:
        return self.position["quantity"]

    def _reset_position(self):
        self.position = {
            "symbol": None,
            "quantity": 0,
            "avg_price": 0.0,
            "last_updated": time.time()
        }

    # ============================================================
    # RISK & SAFETY GATES
    # ============================================================
    def can_place_order(self, is_exit: bool = False) -> bool:
        if not state_machine.is_trading_allowed():
            print("🚫 can_place_order: Trading not allowed in current state")
            return False

        if self.daily_loss >= self.max_daily_loss_pct * self.capital:
            print("🚫 can_place_order: Daily loss limit reached")
            return False

        # Block only new entries when already in a position
        if self.has_open_position() and not is_exit:
            print("🚫 can_place_order: Already have an open position")
            return False

        return True

    # ============================================================
    # GUARDED ORDER PLACEMENT (Core Method)
    # ============================================================
    def place_guarded_order(self, kite, symbol: str, quantity: int,
                            transaction_type: str, price: float = 0.0,
                            order_type: str = "MARKET", product: str = "MIS",
                            validity: str = "DAY", dry_run: bool = None,
                            is_exit: bool = False) -> dict:

        from market_calendar import is_market_open

        if dry_run is None:
            dry_run = not is_market_open()

        mode = "DRY RUN" if dry_run else "REAL"
        print(f"\n[{mode}] Attempting guarded order → {transaction_type} {quantity} {symbol}")

        if not self.can_place_order(is_exit=is_exit):
            return {
                "success": False,
                "order_id": None,
                "message": "Order blocked by risk gates"
            }

        # === DRY RUN MODE ===
        if dry_run:
            self.on_order_placed(symbol, quantity, transaction_type, price, is_exit=is_exit)
            action = "Exit simulated" if is_exit else "Simulated successfully"
            print(f"[{mode}] {action}. Optimistic position updated.")
            return {
                "success": True,
                "order_id": "DRY-RUN",
                "message": "Dry run completed successfully"
            }

        # === REAL ORDER MODE ===
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
                self.on_order_placed(symbol, quantity, transaction_type, price, is_exit=is_exit)
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

    # ============================================================
    # POSITION UPDATE (Single Source of Truth)
    # ============================================================
    def on_order_placed(self, symbol: str, quantity: int, side: str,
                        avg_price: float = 0.0, is_exit: bool = False):
        """
        Updates internal position after an order is placed/filled.
        Works correctly for both entries and exits.
        """
        if quantity <= 0:
            print("🚫 Invalid order quantity")
            return False

        side = side.upper()
        if side not in {"BUY", "SELL"}:
            print(f"🚫 Invalid order side: {side}")
            return False

        current_qty = self.position.get("quantity", 0)
        current_symbol = self.position.get("symbol")

        if is_exit:
            # Exit logic
            if current_symbol != symbol:
                print(f"⚠️ Exit symbol mismatch. Current: {current_symbol}, trying to exit: {symbol}")
                return False

            if side == "SELL" and current_qty > 0:
                self.position["quantity"] = current_qty - quantity
            elif side == "BUY" and current_qty < 0:
                self.position["quantity"] = current_qty + quantity
            else:
                print("⚠️ Invalid exit direction for current position")
                return False

            if self.position["quantity"] == 0:
                self.position["symbol"] = None
                self.position["avg_price"] = 0.0

            self.position["last_updated"] = time.time()
            return True

        else:
            # Entry / New order logic
            if current_symbol is None or current_symbol == symbol:
                self.position["symbol"] = symbol

                if side == "BUY":
                    new_qty = current_qty + quantity
                else:
                    new_qty = current_qty - quantity

                self.position["quantity"] = new_qty

                if avg_price > 0 and new_qty != 0:
                    if current_qty == 0:
                        self.position["avg_price"] = avg_price
                    else:
                        total_value = (self.position["avg_price"] * abs(current_qty)) + (avg_price * quantity)
                        self.position["avg_price"] = total_value / abs(new_qty)

                self.position["last_updated"] = time.time()
                return True
            else:
                print(f"⚠️ Cannot add {symbol} while holding {current_symbol}")
                return False

    # ============================================================
    # RECONCILIATION & POSITION SYNC
    # ============================================================
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

    def _trigger_mismatch_alarm(self):
        state_machine.set_state(SystemState.RECONCILIATION_FAILED)

    # ============================================================
    # OBSERVABILITY & HEALTH
    # ============================================================
    def print_position_status(self):
        if self.is_flat():
            print("📊 Position: FLAT")
        else:
            direction = "LONG" if self.is_long() else "SHORT"
            qty = abs(self.position["quantity"])
            symbol = self.position["symbol"] or "N/A"
            avg = self.position["avg_price"]
            print(f"📊 Position: {direction} {qty} @ ₹{avg:,.2f} ({symbol})")

    def print_startup_status(self):
        print("\n" + "="*50)
        print("🚀 RISK GATEKEEPER STARTUP STATUS")
        print("="*50)
        print(f"Capital               : ₹{self.capital:,.0f}")
        print(f"Max Daily Loss Limit  : {self.max_daily_loss_pct * 100}%")
        print(f"Current Position      : {self.position['quantity']} {self.position['symbol'] or ''}")
        print(f"Daily Loss So Far     : ₹{self.daily_loss:,.2f}")
        print("="*50 + "\n")

    # ============================================================
    # GATES
    # ============================================================
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
        
# ============================================================
# Singleton Instance (Used across the project)
# ============================================================
risk_gatekeeper = RiskGatekeeper(capital=1_000_000.0)