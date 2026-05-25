"""
generate_token.py
Generate a Zerodha Kite Connect access token and store it in .env.
Run this manually before market open when Zerodha requires a fresh login.
"""

from pathlib import Path

from dotenv import set_key
from kiteconnect import KiteConnect

import config


ENV_PATH = Path(".env")


def generate_access_token():
    print("=" * 60)
    print("Zerodha Kite Connect Token Generator")
    print("=" * 60)

    kite = KiteConnect(api_key=config.KITE_API_KEY)
    login_url = kite.login_url()

    print("\n[STEP 1] Open this URL in your browser and login with Zerodha:")
    print(login_url)
    print("\n[STEP 2] Copy the request_token value from the redirect URL.")

    request_token = input("\nPaste the request_token here: ").strip()
    if not request_token:
        print("No request_token provided. Exiting.")
        return

    try:
        data = kite.generate_session(request_token, api_secret=config.KITE_API_SECRET)
        access_token = data["access_token"]
        refresh_token = data.get("refresh_token", "")

        ENV_PATH.touch(exist_ok=True)
        set_key(str(ENV_PATH), "KITE_API_KEY", config.KITE_API_KEY)
        set_key(str(ENV_PATH), "KITE_API_SECRET", config.KITE_API_SECRET)
        set_key(str(ENV_PATH), "KITE_ACCESS_TOKEN", access_token)
        if refresh_token:
            set_key(str(ENV_PATH), "KITE_REFRESH_TOKEN", refresh_token)

        print("\nSUCCESS! Tokens saved to .env.")
        print("Keep .env out of git and rotate any credentials that were previously committed.")

    except Exception as exc:
        print(f"\nError generating session: {exc}")
        print("Common reasons: wrong API secret, expired request_token, or network issue.")


if __name__ == "__main__":
    generate_access_token()
