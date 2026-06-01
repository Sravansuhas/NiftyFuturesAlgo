"""
Basic Options (CE/PE) Skeleton for Future Work
=============================================

This file exists as a clear starting point for adding options trading
(NIFTY, BANKNIFTY, SENSEX CE and PE) later.

Current Scope (as of May 2026):
- We are focusing on **Futures only** for the three indices for safe paper trading.
- Options will require:
    * Proper strike selection logic
    * Option chain fetching
    * Different risk rules (Greeks awareness, premium risk)
    * Use of /margins/basket for pre-trade margin checks
    * Separate strategy classes or heavy extension of the current one

DO NOT ACTIVATE THIS CODE for live/paper trading until it has gone through full design + review.

Usage pattern (future):
    from app.options_skeleton import OptionsPaperStrategy
    # Then integrate with MultiSymbolRiskManager (extended for options)

This file is intentionally minimal and heavily commented.
"""

from typing import Optional, Dict, Any
from kiteconnect import KiteConnect

class BasicOptionsStrategy:
    """
    Placeholder for a future options strategy.

    For now it only demonstrates the structure that would be needed.
    """

    def __init__(self, kite: KiteConnect, underlying: str, option_type: str = "CE"):
        self.kite = kite
        self.underlying = underlying.upper()   # NIFTY, BANKNIFTY, SENSEX
        self.option_type = option_type.upper() # CE or PE

        self.selected_strike = None
        self.selected_expiry = None
        self.instrument_token = None

    def fetch_option_chain(self, expiry_date: Optional[str] = None) -> Dict[str, Any]:
        """
        Placeholder.
        In real implementation this would:
        - Call kite.instruments("NFO")
        - Filter by name + option_type + expiry
        - Return sorted strikes with LTP, OI, etc.
        """
        print(f"[OPTIONS SKELETON] Would fetch {self.underlying} {self.option_type} chain here")
        return {"strikes": [], "message": "Not implemented yet"}

    def select_strike(self, chain_data: Dict, preference: str = "ATM") -> Optional[Dict]:
        """
        Placeholder for strike selection logic (ATM, OTM, etc.).
        """
        print("[OPTIONS SKELETON] Strike selection logic would go here")
        return None

    def calculate_premium_risk(self, entry_premium: float, quantity: int) -> float:
        """
        Very important for options: max loss is usually premium paid (for buyers).
        """
        return entry_premium * quantity

    def get_signal(self) -> Dict[str, Any]:
        """
        Future: return proposed option trade with suggested target/sl in premium terms.
        """
        return {
            "action": "HOLD",
            "message": "Options strategy not yet implemented. See options_skeleton.py"
        }

# Example of how it might be used later
if __name__ == "__main__":
    print("This is only a skeleton. Do not run for real trading yet.")