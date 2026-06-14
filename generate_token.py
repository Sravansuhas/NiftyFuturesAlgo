"""
generate_token.py
Generate a Zerodha Kite Connect access token and store it in .env.

Usage:
    python generate_token.py              # auto-login (opens browser, captures token)
    python generate_token.py --auto       # same as default
    python generate_token.py --manual     # legacy copy-paste flow
    python generate_token.py --validate   # check if current token is valid
    python generate_token.py --setup      # verify .env + port + redirect URL
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from kiteconnect import KiteConnect

import config
from app.kite_auth import (
    DEFAULT_REDIRECT_URL,
    ensure_redirect_in_env,
    exchange_request_token,
    get_redirect_url,
    preflight_login_check,
    start_auto_login,
    validate_access_token,
)

PROJECT_ROOT = Path(__file__).resolve().parent


def _ensure_project_root() -> None:
    if Path.cwd().resolve() != PROJECT_ROOT:
        os.chdir(PROJECT_ROOT)


def _manual_flow() -> None:
    print("=" * 60)
    print("Zerodha Kite Connect Token Generator (Manual)")
    print("=" * 60)

    redirect = ensure_redirect_in_env()
    kite = KiteConnect(api_key=config.KITE_API_KEY)
    print(f"\nRedirect URL registered in Kite developer console must be:")
    print(f"  {redirect}")
    print("\n[STEP 1] Open this URL in your browser and login with Zerodha:")
    print(kite.login_url())
    print("\n[STEP 2] After login, copy request_token from the browser address bar.")
    print("         Example: http://127.0.0.1:8765/callback?request_token=XXXX&status=success")

    request_token = input("\nPaste the request_token here: ").strip()
    if not request_token:
        print("No request_token provided. Exiting.")
        return

    try:
        session = exchange_request_token(
            request_token,
            api_key=config.KITE_API_KEY,
            api_secret=config.KITE_API_SECRET,
        )
        print("\nSUCCESS! Tokens saved to .env.")
        print(f"Logged in as: {session.get('user_name', session.get('user_id', 'unknown'))}")
    except Exception as exc:
        print(f"\nError generating session: {exc}")
        print("Common reasons: wrong API secret, expired request_token (single-use), or network issue.")
        print("Run the script again and complete login once — do not refresh the callback page.")


def _setup_flow() -> None:
    print("=" * 60)
    print("Kite Login — Setup Check")
    print("=" * 60)
    try:
        checks = preflight_login_check(
            api_key=config.KITE_API_KEY,
            api_secret=config.KITE_API_SECRET,
        )
    except Exception as exc:
        print(f"\nSetup FAILED: {exc}")
        sys.exit(1)

    print("\nCredentials : OK (API key + secret present)")
    print(f"Redirect URL: {checks['redirect_url']}")
    print(f"Callback    : http://{checks['bind_host']}:{checks['port']}{checks['path']} (port free)")
    print("\nIn Kite developer portal → your app → Redirect URL (NOT Postback URL), set exactly:")
    print(f"  {checks['redirect_url']}")
    print("\nAlternatives if portal rejects /callback:")
    print("  http://127.0.0.1:8765")
    print("  http://127.0.0.1")
    try:
        from app.kite_connect_rules import faq_checklist, session_guidance
        guidance = session_guidance()
        print(f"\nToken window: {guidance['token_note']}")
        if not guidance["safe_to_generate_token"]:
            print("WARNING: Pre-07:35 IST — token may be flushed. Wait if login fails.")
    except Exception:
        pass
    print("\nThen run:  python generate_token.py")
    sys.exit(0)


def _auto_flow(open_browser: bool) -> None:
    print("=" * 60)
    print("Zerodha Kite Connect — Auto Login")
    print("=" * 60)

    try:
        from app.kite_connect_rules import session_guidance
        g = session_guidance()
        print(f"\n{g['token_note']}")
        print(f"{g['request_token_note']}")
        if not g["safe_to_generate_token"]:
            print("\n⚠ Pre-07:35 IST window — yesterday's token may still be flushing.")
    except Exception:
        pass

    try:
        checks = preflight_login_check(
            api_key=config.KITE_API_KEY,
            api_secret=config.KITE_API_SECRET,
        )
    except Exception as exc:
        print(f"\nPreflight failed: {exc}")
        print("\nTroubleshooting:")
        print("  1. Run: python generate_token.py --setup")
        print("  2. Free port 8765 if another process holds it")
        print("  3. Set Kite Redirect URL to match KITE_REDIRECT_URL in .env")
        sys.exit(1)

    print(f"\nKite developer portal → Redirect URL (must match exactly):")
    print(f"  {checks['redirect_url']}")
    print(f"\nLocal callback server: http://{checks['bind_host']}:{checks['port']}{checks['path']}")
    print(f"(Default: {DEFAULT_REDIRECT_URL})\n")

    try:
        session = start_auto_login(open_browser=open_browser)
        print("\nSUCCESS! Tokens saved to .env.")
        print(f"Logged in as: {session.get('user_name', session.get('user_id', 'unknown'))}")
        print("Token valid until ~6 AM IST tomorrow (Kite regulatory expiry).")
    except TimeoutError as exc:
        print(f"\n{exc}")
        print("\nIf the browser showed 'connection refused' on the callback URL:")
        print("  - Redirect URL in Kite console must match exactly (including /callback)")
        print("  - Do not close this terminal until login finishes")
        print("  - Or use: python generate_token.py --manual")
        sys.exit(1)
    except Exception as exc:
        print(f"\nAuto-login failed: {exc}")
        print("\nTry:")
        print("  python generate_token.py --manual")
        print("  python generate_token.py --setup")
        sys.exit(1)


def _validate_flow() -> None:
    ok, profile, err = validate_access_token(
        api_key=config.KITE_API_KEY,
        access_token=config.KITE_ACCESS_TOKEN,
    )
    if ok:
        print(f"Token valid — {profile.get('user_name')} ({profile.get('user_id')})")
        sys.exit(0)
    print(f"Token invalid — {err}")
    print("Generate a fresh token: python generate_token.py")
    sys.exit(1)


def main() -> None:
    _ensure_project_root()

    parser = argparse.ArgumentParser(description="Kite Connect token generator")
    parser.add_argument("--auto", action="store_true", help="Auto-login via local callback (default)")
    parser.add_argument("--manual", action="store_true", help="Legacy copy-paste request_token flow")
    parser.add_argument("--validate", action="store_true", help="Validate current .env access token")
    parser.add_argument("--setup", action="store_true", help="Verify redirect URL, port, and credentials")
    parser.add_argument("--no-browser", action="store_true", help="Don't auto-open browser (print URL only)")
    args = parser.parse_args()

    if args.validate:
        _validate_flow()
        return

    if args.setup:
        _setup_flow()
        return

    if args.manual:
        _manual_flow()
        return

    _auto_flow(open_browser=not args.no_browser)


if __name__ == "__main__":
    main()