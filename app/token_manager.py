import os
from kiteconnect import KiteConnect
from kiteconnect.exceptions import TokenException
from dotenv import load_dotenv, set_key
from pathlib import Path

load_dotenv()


class TokenManager:
    def __init__(self, kite: KiteConnect):
        self.kite = kite
        self.api_key = os.getenv("KITE_API_KEY")
        self.api_secret = os.getenv("KITE_API_SECRET")
        self.access_token = os.getenv("KITE_ACCESS_TOKEN")
        self.refresh_token = os.getenv("KITE_REFRESH_TOKEN")

        self.token_valid = False
        self.needs_relogin = False

        if self.access_token:
            self.kite.set_access_token(self.access_token)
            self.token_valid = True

        # Register hook for automatic expiry detection
        self.kite.set_session_expiry_hook(self._handle_token_expiry)

    def _handle_token_expiry(self):
        """Called automatically when Kite raises TokenException"""
        print("Access token has expired.")
        self.token_valid = False

        if self.refresh_token:
            print("Attempting to refresh access token...")
            success = self._refresh_token()
            if success:
                self.token_valid = True
                self.needs_relogin = False
            else:
                self.needs_relogin = True
        else:
            print("No refresh token available. Manual re-login required.")
            self.needs_relogin = True

    def _refresh_token(self) -> bool:
        """Internal method to refresh access token"""
        try:
            data = self.kite.renew_access_token(self.refresh_token, self.api_secret)
            new_access_token = data.get("access_token")
            new_refresh_token = data.get("refresh_token", self.refresh_token)

            if new_access_token:
                self.access_token = new_access_token
                self.refresh_token = new_refresh_token
                self.kite.set_access_token(new_access_token)

                # Persist new tokens
                env_path = Path(".env")
                if env_path.exists():
                    set_key(str(env_path), "KITE_ACCESS_TOKEN", new_access_token)
                    if new_refresh_token:
                        set_key(str(env_path), "KITE_REFRESH_TOKEN", new_refresh_token)

                print("Access token refreshed successfully.")
                return True
            return False

        except TokenException as e:
            print(f"Token refresh failed: {e}")
            return False
        except Exception as e:
            print(f"Unexpected error during refresh: {e}")
            return False

    def is_token_valid(self) -> bool:
        """Check if we have a valid token"""
        return self.token_valid and not self.needs_relogin
