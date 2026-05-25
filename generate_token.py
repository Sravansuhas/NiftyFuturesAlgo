"""
generate_token.py
Clean script to generate/refresh Zerodha Kite Connect access token.
Run this every morning before market open.
"""

from kiteconnect import KiteConnect
import config

def generate_access_token():
    print("=" * 60)
    print("Zerodha Kite Connect Token Generator")
    print("=" * 60)

    kite = KiteConnect(api_key=config.KITE_API_KEY)

    # Step 1: Get login URL
    login_url = kite.login_url()
    print("\n[STEP 1] Open this URL in your browser and login with Zerodha:")
    print(login_url)
    print("\nAfter login, you will be redirected to a URL like:")
    print("http://localhost:5000/?request_token=XXXXXXXXXXXXXXXX&action=login&status=success")
    print("\n[STEP 2] Copy the 'request_token' value from that URL and paste it below.")

    request_token = input("\nPaste the request_token here: ").strip()

    if not request_token:
        print("❌ No request_token provided. Exiting.")
        return

    try:
        # Step 3: Generate session and get access_token
        data = kite.generate_session(request_token, api_secret=config.KITE_API_SECRET)
        access_token = data["access_token"]

        print("\n" + "=" * 60)
        print("✅ SUCCESS! New Access Token Generated:")
        print("=" * 60)
        print(f"\n{access_token}\n")
        print("Copy the above token and update KITE_ACCESS_TOKEN in config.py")
        print("=" * 60)

        # Optional: Show how to update config.py
        print("\nTo auto-update config.py in future, we can enhance this script.")
        print("For now, manually paste the token into config.py under KITE_ACCESS_TOKEN.")

    except Exception as e:
        print(f"\n❌ Error generating session: {e}")
        print("Common reasons: Wrong API secret, expired request_token, or network issue.")

if __name__ == "__main__":
    generate_access_token()