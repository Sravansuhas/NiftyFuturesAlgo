"""
Project configuration.

Secrets must come from environment variables or a local .env file. Do not
commit real Kite credentials or daily access tokens to source control.
"""

import os
from dotenv import load_dotenv

load_dotenv()


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


KITE_API_KEY = _required_env("KITE_API_KEY")
KITE_API_SECRET = _required_env("KITE_API_SECRET")
KITE_ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN", "")
KITE_REFRESH_TOKEN = os.getenv("KITE_REFRESH_TOKEN", "")
