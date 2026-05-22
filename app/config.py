import os
from dotenv import load_dotenv
load_dotenv()

KITE_API_KEY = os.getenv("KITE_API_KEY")
KITE_API_SECRET = os.getenv("KITE_API_SECRET")
KITE_ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN")   

# Validation
if not KITE_API_KEY or not KITE_API_SECRET:
    raise ValueError("❌ Missing KITE_API_KEY or KITE_API_SECRET!")

print("✅ All Kite credentials loaded successfully")