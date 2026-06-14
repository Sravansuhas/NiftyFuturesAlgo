import os
from typing import Optional

from kiteconnect import KiteConnect
from kiteconnect.exceptions import TokenException
from dotenv import load_dotenv, set_key
from pathlib import Path

load_dotenv()

_token_manager_singleton: Optional["TokenManager"] = None


def get_token_manager() -> Optional["TokenManager"]:
    return _token_manager_singleton


def live_trading_token_ok() -> bool:
    """
    True when live (non-dry-run) orders may call Kite APIs.
    Paper/dry-run bypasses token checks.
    """
    import os

    force_dry = os.getenv("FORCE_DRY_RUN", "true").strip().lower() not in {"0", "false", "no"}
    if force_dry:
        return True
    mgr = get_token_manager()
    return bool(mgr and mgr.is_token_valid())


class TokenManager:
    def __init__(self, kite: KiteConnect):
        global _token_manager_singleton
        _token_manager_singleton = self
        self.kite = kite
        self.api_key = os.getenv("KITE_API_KEY")
        self.api_secret = os.getenv("KITE_API_SECRET")
        self.access_token = os.getenv("KITE_ACCESS_TOKEN")
        self.refresh_token = os.getenv("KITE_REFRESH_TOKEN")

        self.token_valid = False
        self.needs_relogin = False
        self.token_reload_generation = 0

        if self.access_token:
            self.kite.set_access_token(self.access_token)

        # Register hook for automatic expiry detection
        self.kite.set_session_expiry_hook(self._handle_token_expiry)

        # Validate against Kite API (env token may be stale)
        if self.access_token:
            self.validate_token()

    def reload_from_env(self) -> None:
        """Reload tokens after auto-login updates .env."""
        load_dotenv(override=True)
        self.access_token = os.getenv("KITE_ACCESS_TOKEN")
        self.refresh_token = os.getenv("KITE_REFRESH_TOKEN")
        if self.access_token:
            self.kite.set_access_token(self.access_token)
            self.validate_token()
        self.token_reload_generation += 1

    def validate_token(self) -> bool:
        """Ping Kite profile API to confirm the access token is live."""
        if not self.access_token:
            self.token_valid = False
            self.needs_relogin = True
            return False
        try:
            self.kite.profile()
            self.token_valid = True
            self.needs_relogin = False
            return True
        except TokenException:
            self.token_valid = False
            self.needs_relogin = True
            try:
                from .kite_connect_rules import on_token_exception
                on_token_exception("validate_token")
            except Exception:
                pass
            return False
        except Exception:
            self.token_valid = False
            return False

    def _handle_token_expiry(self):
        """Called automatically when Kite raises TokenException (FAQ: relogin required)."""
        print("Access token expired or invalidated — run: python generate_token.py")
        self.token_valid = False
        try:
            from .kite_connect_rules import on_token_exception
            on_token_exception("session_expiry_hook")
        except Exception:
            pass

        if self.refresh_token:
            print("Attempting to refresh access token...")
            success = self._refresh_token()
            if success:
                self.token_valid = True
                self.needs_relogin = False
            else:
                self.needs_relogin = True
        else:
            print("No refresh token available. Run: python generate_token.py")
            self.needs_relogin = True

    def _refresh_token(self) -> bool:
        """Refresh via renew_access_token — only works on approved Kite platforms."""
        try:
            data = self.kite.renew_access_token(self.refresh_token, self.api_secret)
            new_access_token = data.get("access_token")
            new_refresh_token = data.get("refresh_token", self.refresh_token)

            if new_access_token:
                self.access_token = new_access_token
                self.refresh_token = new_refresh_token
                self.kite.set_access_token(new_access_token)

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