import os
import time
from dataclasses import dataclass
from typing import Dict, Optional

from kiteconnect import KiteConnect

from .audit_logger import audit_logger
from .state_machine import SystemState, state_machine


@dataclass(frozen=True)
class RiskConfig:
    capital: float = 1_000_000.0
    max_daily_loss_pct: float = 0.02
    max_drawdown_pct: float = 0.08
    risk_per_trade_pct: float = 0.005
    reduced_risk_multiplier: float = 0.5
    loss_streak_threshold: int = 2
    lot_size: int = 75
    max_lots: int = 4
    max_trades_per_day: int = 3
    max_order_quantity: int = 300
    force_dry_run: bool = True


class RiskGatekeeper:
    def __init__(self, capital: float = 1_000_000.0, config: Optional[RiskConfig] = None):
        env_force_dry_run = os.getenv("FORCE_DRY_RUN", "true").strip().lower() not in {"0", "false", "no"}
        self.config = config or RiskConfig(capital=capital, force_dry_run=env_force_dry_run)
        self.capital = self.config.capital
        self.daily_pnl = 0.0
        self.daily_loss = 0.0
        self.peak_equity = self.capital
        self.current_equity = self.capital
        self.consecutive_losses = 0
        self.trades_today = 0
        self.pending_orders: Dict[str, Dict] = {}

        self.position = {
            "symbol": None,
            "quantity": 0,
            "avg_price": 0.0,
            "last_updated": None,
        }

        self.last_reconciliation_time = time.time()
        print("RiskGatekeeper initialized (stateful position tracking active)")
        self.print_startup_status()

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

    def calculate_order_quantity(self, entry_price: float, stop_price: float) -> int:
        if self.capital <= 0:
            return self.config.lot_size
        stop_distance = abs(entry_price - stop_price)
        if stop_distance <= 1.0:  # minimum realistic tick distance for safety
            stop_distance = 10.0

        multiplier = self.config.reduced_risk_multiplier if self.consecutive_losses >= self.config.loss_streak_threshold else 1.0
        risk_amount = self.capital * self.config.risk_per_trade_pct * multiplier
        lot_risk = stop_distance * self.config.lot_size
        if lot_risk <= 0:
            return self.config.lot_size
        lots = max(1, int(risk_amount / lot_risk))
        lots = min(lots, self.config.max_lots)
        return lots * self.config.lot_size

    def _reset_position(self):
        self.position = {
            "symbol": None,
            "quantity": 0,
            "avg_price": 0.0,
            "last_updated": time.time(),
        }

    def _validate_order(self, symbol: str, quantity: int, transaction_type: str,
                        order_type: str, product: str, price: float, is_exit: bool) -> Optional[str]:
        if not symbol or not isinstance(symbol, str):
            return "Invalid symbol"
        if not isinstance(quantity, int) or quantity <= 0:
            return "Invalid quantity"
        if quantity > self.config.max_order_quantity:
            return "Order quantity exceeds configured maximum"
        if quantity % self.config.lot_size != 0:
            return f"Quantity must be a multiple of lot size {self.config.lot_size}"
        if transaction_type.upper() not in {"BUY", "SELL"}:
            return "Invalid transaction type"
        if order_type.upper() not in {"MARKET", "LIMIT", "SL", "SL-M"}:
            return "Invalid order type"
        if product.upper() not in {"MIS", "NRML"}:
            return "Invalid product for NFO futures"
        if order_type.upper() == "LIMIT" and price <= 0:
            return "LIMIT orders require a positive price"
        if not is_exit and self.trades_today >= self.config.max_trades_per_day:
            return "Max trades per day reached"
        return None

    def can_place_order(self, is_exit: bool = False) -> bool:
        if not state_machine.is_trading_allowed():
            print("can_place_order: Trading not allowed in current state")
            return False

        if self.daily_loss >= self.config.max_daily_loss_pct * self.capital:
            print("can_place_order: Daily loss limit reached")
            return False

        if self._current_drawdown_pct() >= self.config.max_drawdown_pct:
            print("can_place_order: Max drawdown reached")
            return False

        if self.has_open_position() and not is_exit:
            print("can_place_order: Already have an open position")
            return False

        if self.pending_orders and not is_exit:
            print("can_place_order: Pending order exists")
            return False

        return True

    def place_guarded_order(self, kite: KiteConnect, symbol: str, quantity: int,
                            transaction_type: str, price: float = 0.0,
                            order_type: str = "MARKET", product: str = "MIS",
                            validity: str = "DAY", dry_run: bool = None,
                            is_exit: bool = False, force_dry_run: bool = False,
                            tag: str = "NFALGO", market_protection: int = -1,
                            autoslice: bool = True) -> dict:
        from market_calendar import is_market_open

        validation_error = self._validate_order(symbol, quantity, transaction_type, order_type, product, price, is_exit)
        if validation_error:
            return self._blocked(validation_error, symbol, quantity, transaction_type)

        if force_dry_run or self.config.force_dry_run:
            dry_run = True
        elif dry_run is None:
            dry_run = not is_market_open()

        mode = "DRY RUN" if dry_run else "REAL"
        print(f"\n[{mode}] Attempting guarded order -> {transaction_type} {quantity} {symbol}")

        if not self.can_place_order(is_exit=is_exit):
            return self._blocked("Order blocked by risk gates", symbol, quantity, transaction_type)

        if dry_run:
            position_updated = self.on_order_placed(symbol, quantity, transaction_type, price, is_exit=is_exit)
            if not position_updated:
                return self._blocked("Dry run rejected by position accounting", symbol, quantity, transaction_type)

            if not is_exit:
                self.trades_today += 1
            audit_logger.record("order.dry_run", {
                "symbol": symbol,
                "quantity": quantity,
                "transaction_type": transaction_type.upper(),
                "is_exit": is_exit,
                "price": price,
            })
            return {
                "success": True,
                "order_id": "DRY-RUN",
                "message": "Dry run completed successfully",
                "position_updated": True,
                "dry_run": True,
            }

        try:
            order_id = kite.place_order(
                variety="regular",
                exchange="NFO",
                tradingsymbol=symbol,
                transaction_type=transaction_type.upper(),
                quantity=quantity,
                product=product.upper(),
                order_type=order_type.upper(),
                price=price if order_type.upper() == "LIMIT" else 0,
                validity=validity,
                market_protection=market_protection,
                tag=tag[:20],
            )

            if order_id:
                self.pending_orders[str(order_id)] = {
                    "symbol": symbol,
                    "quantity": quantity,
                    "transaction_type": transaction_type.upper(),
                    "is_exit": is_exit,
                    "placed_at": time.time(),
                }
                if not is_exit:
                    self.trades_today += 1
                audit_logger.record("order.submitted", {
                    "order_id": order_id,
                    "symbol": symbol,
                    "quantity": quantity,
                    "transaction_type": transaction_type.upper(),
                    "is_exit": is_exit,
                })
                print(f"[{mode}] Order submitted -> ID: {order_id}")
                return {
                    "success": True,
                    "order_id": order_id,
                    "message": "Order submitted; await broker fill confirmation",
                    "position_updated": False,
                    "dry_run": False,
                }

            return self._blocked("No order ID returned from broker", symbol, quantity, transaction_type)

        except Exception as exc:
            audit_logger.record("order.failed", {
                "symbol": symbol,
                "quantity": quantity,
                "transaction_type": transaction_type.upper(),
                "error": str(exc),
            })
            print(f"[{mode}] Order placement failed: {exc}")
            return {
                "success": False,
                "order_id": None,
                "message": str(exc),
            }

    def _blocked(self, message: str, symbol: str, quantity: int, transaction_type: str) -> dict:
        audit_logger.record("order.blocked", {
            "symbol": symbol,
            "quantity": quantity,
            "transaction_type": transaction_type,
            "message": message,
        })
        return {
            "success": False,
            "order_id": None,
            "message": message,
        }

    def on_order_placed(self, symbol: str, quantity: int, side: str,
                        avg_price: float = 0.0, is_exit: bool = False):
        if quantity <= 0:
            print("Invalid order quantity")
            return False

        side = side.upper()
        if side not in {"BUY", "SELL"}:
            print(f"Invalid order side: {side}")
            return False

        current_qty = self.position.get("quantity", 0)
        current_symbol = self.position.get("symbol")

        if is_exit:
            if current_symbol != symbol:
                print(f"Exit symbol mismatch. Current: {current_symbol}, trying to exit: {symbol}")
                return False

            if abs(quantity) > abs(current_qty):
                print(f"Exit quantity {quantity} exceeds open position {current_qty}")
                return False

            if side == "SELL" and current_qty > 0:
                self.position["quantity"] = current_qty - quantity
            elif side == "BUY" and current_qty < 0:
                self.position["quantity"] = current_qty + quantity
            else:
                print("Invalid exit direction for current position")
                return False

            if self.position["quantity"] == 0:
                self.position["symbol"] = None
                self.position["avg_price"] = 0.0

            self.position["last_updated"] = time.time()
            return True

        if current_symbol is not None and current_symbol != symbol:
            print(f"Cannot add {symbol} while holding {current_symbol}")
            return False

        self.position["symbol"] = symbol
        new_qty = current_qty + quantity if side == "BUY" else current_qty - quantity
        self.position["quantity"] = new_qty

        if avg_price > 0 and new_qty != 0:
            if current_qty == 0:
                self.position["avg_price"] = avg_price
            else:
                total_value = (self.position["avg_price"] * abs(current_qty)) + (avg_price * quantity)
                self.position["avg_price"] = total_value / abs(new_qty)

        self.position["last_updated"] = time.time()
        return True

    def sync_with_broker(self, broker_net_positions: list):
        nifty_futures_positions = [
            position for position in broker_net_positions
            if str(position.get("tradingsymbol", "")).startswith("NIFTY")
            and str(position.get("tradingsymbol", "")).endswith("FUT")
        ]

        if not nifty_futures_positions:
            if self.position["quantity"] != 0:
                print("MISMATCH: Internal state has a position but broker shows FLAT")
                self._trigger_mismatch_alarm()
            self._reset_position()
            self.pending_orders.clear()
            return

        # Edge case: multiple Nifty fut positions (rare) — net them and alarm if conflicting
        if len(nifty_futures_positions) > 1:
            print("⚠️ MULTIPLE NIFTY FUT POSITIONS DETECTED — netting for safety")
            net_qty = sum(p.get("quantity", 0) for p in nifty_futures_positions)
            # Use first symbol for simplicity; in reality you may want to flatten all
            pos = nifty_futures_positions[0]
            broker_qty = net_qty
            broker_symbol = pos.get("tradingsymbol")
            broker_avg = pos.get("average_price", 0.0)
            self._trigger_mismatch_alarm()
        else:
            pos = nifty_futures_positions[0]
            broker_qty = pos.get("quantity", 0)
            broker_symbol = pos.get("tradingsymbol")
            broker_avg = pos.get("average_price", 0.0)

        if self.position["quantity"] != broker_qty or self.position["symbol"] != broker_symbol:
            print("POSITION MISMATCH DETECTED")
            print(f"   Internal: {self.position['symbol']} x {self.position['quantity']}")
            print(f"   Broker  : {broker_symbol} x {broker_qty}")
            if self.pending_orders:
                print("   Pending orders exist; accepting broker as authoritative")
            else:
                self._trigger_mismatch_alarm()

        self.position["symbol"] = broker_symbol
        self.position["quantity"] = broker_qty
        self.position["avg_price"] = broker_avg
        self.position["last_updated"] = time.time()
        self.pending_orders.clear()
        self.last_reconciliation_time = time.time()
        audit_logger.record("position.synced", dict(self.position))

    def update_equity(self, equity: float):
        self.current_equity = equity
        self.peak_equity = max(self.peak_equity, equity)
        if self._current_drawdown_pct() >= self.config.max_drawdown_pct:
            state_machine.set_state(SystemState.CIRCUIT_BREAKER_TRIGGERED)

    def _current_drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return (self.peak_equity - self.current_equity) / self.peak_equity

    def _trigger_mismatch_alarm(self):
        state_machine.set_state(SystemState.RECONCILIATION_FAILED)

    def print_position_status(self):
        if self.is_flat():
            print("Position: FLAT")
        else:
            direction = "LONG" if self.is_long() else "SHORT"
            qty = abs(self.position["quantity"])
            symbol = self.position["symbol"] or "N/A"
            avg = self.position["avg_price"]
            print(f"Position: {direction} {qty} @ Rs {avg:,.2f} ({symbol})")

    def print_startup_status(self):
        print("\n" + "=" * 50)
        print("RISK GATEKEEPER STARTUP STATUS")
        print("=" * 50)
        print(f"Capital               : Rs {self.capital:,.0f}")
        print(f"Max Daily Loss Limit  : {self.config.max_daily_loss_pct * 100}%")
        print(f"Max Drawdown Limit    : {self.config.max_drawdown_pct * 100}%")
        print(f"Risk Per Trade        : {self.config.risk_per_trade_pct * 100}%")
        print(f"Force Dry Run         : {self.config.force_dry_run}")
        print(f"Current Position      : {self.position['quantity']} {self.position['symbol'] or ''}")
        print(f"Daily Loss So Far     : Rs {self.daily_loss:,.2f}")
        print("=" * 50 + "\n")

    def check_all_gates(self) -> bool:
        if not state_machine.is_trading_allowed():
            print("Gate 1 FAILED: Trading not allowed in current state")
            return False

        if self.daily_loss >= self.config.max_daily_loss_pct * self.capital:
            print("Gate 2 FAILED: Daily loss limit reached")
            state_machine.set_state(SystemState.TRADING_DISABLED)
            return False

        if self._current_drawdown_pct() >= self.config.max_drawdown_pct:
            print("Gate 3 FAILED: Max drawdown reached")
            state_machine.set_state(SystemState.CIRCUIT_BREAKER_TRIGGERED)
            return False

        print("ALL RISK GATES PASSED")
        return True

    def update_daily_loss(self, realized_pnl: float):
        self.daily_pnl += realized_pnl
        self.daily_loss = max(0.0, -self.daily_pnl)
        if realized_pnl < 0:
            self.consecutive_losses += 1
        elif realized_pnl > 0:
            self.consecutive_losses = 0
        self.update_equity(self.capital + self.daily_pnl)

    def reset_daily(self):
        """Reset per-trading-day counters. Called by main loop on new market day."""
        self.daily_pnl = 0.0
        self.daily_loss = 0.0
        self.trades_today = 0
        # consecutive_losses may be carried or reset — reset for fresh day risk budget
        self.consecutive_losses = 0
        self.peak_equity = self.capital
        self.current_equity = self.capital
        self.pending_orders.clear()
        print("[RISK] Daily counters reset (new trading day)")


risk_gatekeeper = RiskGatekeeper(capital=1_000_000.0)
