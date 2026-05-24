from kiteconnect import KiteConnect
from config import KITE_API_KEY, KITE_API_SECRET

kite = KiteConnect(api_key=KITE_API_KEY)

# Step 1: Get login URL
print("Login URL:", kite.login_url())

# After login, paste the request_token here
request_token = input("Paste the request_token from redirect URL: ").strip()

# Step 2: Generate session (this gives both access_token and refresh_token)
data = kite.generate_session(request_token, api_secret=KITE_API_SECRET)

print("\n✅ New tokens generated successfully!")
print("Access Token :", data["access_token"])
print("Refresh Token:", data["refresh_token"])