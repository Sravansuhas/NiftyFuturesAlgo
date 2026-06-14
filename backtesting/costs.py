"""
backtesting/costs.py

Realistic transaction cost & slippage model for Nifty futures on Zerodha (Kite).

This is the single most important missing piece for turning "encouraging" backtest numbers
into tradable expectancy. A 4.67 profit factor will compress dramatically once real costs
are applied.

References (as of 2026):
- Zerodha brokerage for F&O: Flat ₹20 per executed order or 0.03% (whichever lower).
  For a standard Nifty lot this almost always resolves to the flat ₹20 per order.
- STT on futures (sell side): 0.05% w.e.f. 1 Apr 2026 (was 0.0125% before)
- Exchange transaction charges + SEBI + GST + Stamp duty: small, typically bring total
  statutory + brokerage to ~₹50-90 all-in round turn per lot for most users.
- Slippage: Highly regime dependent. 1-2 ticks (1.5-3 points) is common in liquid hours
  for market orders. Can spike to 8-15+ points in opening 30min, expiry day, or news.

Trader reality:
- You will almost never get theoretical fills.
- Opening auction (9:15-9:30/9:45) and last 15 minutes are the worst for slippage + fakeouts.
- Expiry weeks have elevated realized vol + gamma risk even on the front contract.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# NSE NIFTY futures lot size (2026). BankNifty 30 / Sensex 20 — override per index in WFO.
NIFTY_LOT_SIZE_2026 = 65


@dataclass(frozen=True)
class CostConfig:
    # Per executed order (buy or sell leg)
    brokerage_per_order: float = 20.0

    # Additional statutory / exchange charges per round turn (conservative buffer)
    other_charges_per_lot_round_turn: float = 35.0   # STT + txn + GST + stamp + misc

    # Default realistic slippage for Nifty futures market orders (in index points)
    default_slippage_points: float = 3.5

    # Current Nifty lot size (65 w.e.f. late 2024 — keep configurable per index in WFO)
    lot_size: int = NIFTY_LOT_SIZE_2026

    # Safety multiplier for high-uncertainty periods (opening, expiry, events)
    high_uncertainty_multiplier: float = 2.0

    # Overall cost multiplier for sensitivity analysis (1.0 = base, 2.0 = double costs, etc.)
    cost_multiplier: float = 1.0


class TransactionCostModel:
    """
    Calculates realistic round-turn costs + slippage for Nifty futures backtests.

    Usage in backtester:
        model = TransactionCostModel()
        net_pnl = model.apply_costs(gross_pnl, quantity, entry_price, exit_price, ...)
    """

    def __init__(self, config: Optional[CostConfig] = None):
        self.config = config or CostConfig()

    def round_turn_cost_per_lot(self, slippage_points: Optional[float] = None, entry_price: float = None) -> float:
        """
        More accurate round-turn cost for Nifty futures.
        If entry_price is provided, we calculate STT more precisely on notional.
        Applies overall cost_multiplier for sensitivity analysis.
        """
        slip = slippage_points if slippage_points is not None else self.config.default_slippage_points
        slippage_cost = slip * self.config.lot_size * 2

        brokerage = self.config.brokerage_per_order * 2

        # Rough statutory + buffer (can be refined with exact price)
        other = self.config.other_charges_per_lot_round_turn

        # If we have price, do a better STT approximation (STT is only on sell side)
        if entry_price and entry_price > 0:
            notional_per_lot = entry_price * self.config.lot_size
            # STT 0.05% on sell side only for futures (Apr 2026+)
            stt_sell = notional_per_lot * 0.0005
            other = max(other, stt_sell * 1.8)  # buffer for txn + GST etc.

        base_cost = slippage_cost + brokerage + other
        return base_cost * self.config.cost_multiplier

    def estimate_cost_for_trade(
        self,
        quantity: int,
        entry_price: float,
        exit_price: float,
        slippage_points: Optional[float] = None,
        is_high_uncertainty: bool = False,
        bar_time: Optional[datetime] = None,
    ) -> float:
        """
        Returns the total cost (in ₹) that should be subtracted from the gross P&L of this trade.
        Positive number = cost to deduct.

        bar_time: Optional. Applies time-of-day slippage adjustment (very important for Indian markets).
        """
        lots = max(1, abs(quantity) // self.config.lot_size)

        slip = slippage_points if slippage_points is not None else self.config.default_slippage_points

        # Time-of-day adjustment (Nifty futures reality)
        if bar_time is not None:
            try:
                hour = bar_time.hour
                if hour < 10 or hour >= 15:      # First/last 15-30 min
                    slip *= 1.9
                elif hour == 10 or hour == 14:   # First/last hour
                    slip *= 1.45
            except Exception:
                pass

        if is_high_uncertainty:
            slip *= self.config.high_uncertainty_multiplier

        per_lot = self.round_turn_cost_per_lot(slip, entry_price=entry_price)
        return per_lot * lots

    def apply_to_pnl(
        self,
        gross_pnl: float,
        quantity: int,
        entry_price: float,
        exit_price: float,
        slippage_points: Optional[float] = None,
        is_high_uncertainty: bool = False,
        bar_time: Optional[datetime] = None,
    ) -> float:
        """Convenience: returns net P&L after realistic costs."""
        cost = self.estimate_cost_for_trade(
            quantity=quantity,
            entry_price=entry_price,
            exit_price=exit_price,
            slippage_points=slippage_points,
            is_high_uncertainty=is_high_uncertainty,
            bar_time=bar_time,
        )
        return gross_pnl - cost

    @classmethod
    def with_multiplier(cls, base_config: Optional[CostConfig] = None, multiplier: float = 1.0):
        """Convenience factory for cost sensitivity analysis."""
        if base_config is None:
            base_config = CostConfig()
        # Create new config with multiplier applied
        new_config = CostConfig(
            brokerage_per_order=base_config.brokerage_per_order,
            other_charges_per_lot_round_turn=base_config.other_charges_per_lot_round_turn,
            default_slippage_points=base_config.default_slippage_points,
            lot_size=base_config.lot_size,
            high_uncertainty_multiplier=base_config.high_uncertainty_multiplier,
            cost_multiplier=multiplier,
        )
        return cls(new_config)

    def __repr__(self):
        return (f"TransactionCostModel(default_slip={self.config.default_slippage_points}pts, "
                f"round_turn_per_lot≈₹{self.round_turn_cost_per_lot():.0f})")


# Global sensible default for Nifty futures on Zerodha
default_cost_model = TransactionCostModel(
    CostConfig(
        brokerage_per_order=20.0,
        other_charges_per_lot_round_turn=40.0,   # conservative buffer
        default_slippage_points=3.5,
        lot_size=65,
        high_uncertainty_multiplier=1.8,
    )
)
