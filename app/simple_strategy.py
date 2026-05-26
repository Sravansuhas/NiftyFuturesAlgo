import time
from .risk_gatekeeper import risk_gatekeeper


class SimpleStrategy:
    """
    Minimal strategy skeleton.
    This is a base structure you can expand later.
    """

    def __init__(self, kite):
        self.kite = kite
        self.symbol = "NIFTY25MAYFUT"   # Change as needed
        self.quantity = 75              # 1 lot

    def should_enter_long(self) -> bool:
        """
        Define your entry condition here.
        For now, this is just a placeholder.
        """
        # Example: You can later add technical indicators here
        return True   # Placeholder - always true for testing

    def run(self):
        """Main strategy loop"""
        print("\nSimple Strategy Started...")

        while True:
            # Check if we can place an order
            if risk_gatekeeper.can_place_order() and self.should_enter_long():
                print("Signal: Attempting Long Entry...")

                result = risk_gatekeeper.place_guarded_order(
                    kite=self.kite,
                    symbol=self.symbol,
                    quantity=self.quantity,
                    transaction_type="BUY"
                )

                if result["success"]:
                    print(f"Order placed -> {result.get('order_id')}")
                else:
                    print(f"Order blocked/failed -> {result.get('message')}")

            # Sleep to avoid busy loop (adjust as needed)
            time.sleep(30)


# Example usage (you can call this from main.py later)
if __name__ == "__main__":
    from kiteconnect import KiteConnect
    from config import KITE_API_KEY, KITE_ACCESS_TOKEN

    kite = KiteConnect(api_key=KITE_API_KEY)
    kite.set_access_token(KITE_ACCESS_TOKEN)

    strategy = SimpleStrategy(kite)
    strategy.run()
