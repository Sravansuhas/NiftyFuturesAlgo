from pathlib import Path

from dotenv import set_key
from kiteconnect import KiteConnect

from config import KITE_API_KEY, KITE_API_SECRET


ENV_PATH = Path(".env")

kite = KiteConnect(api_key=KITE_API_KEY)

print("Login URL:", kite.login_url())
request_token = input("Paste the request_token from redirect URL: ").strip()

data = kite.generate_session(request_token, api_secret=KITE_API_SECRET)

ENV_PATH.touch(exist_ok=True)
set_key(str(ENV_PATH), "KITE_API_KEY", KITE_API_KEY)
set_key(str(ENV_PATH), "KITE_API_SECRET", KITE_API_SECRET)
set_key(str(ENV_PATH), "KITE_ACCESS_TOKEN", data["access_token"])
if data.get("refresh_token"):
    set_key(str(ENV_PATH), "KITE_REFRESH_TOKEN", data["refresh_token"])

print("New tokens saved to .env.")
