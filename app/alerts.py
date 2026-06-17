"""
Lightweight, pluggable alerting system for Aegis.

Designed to be extremely efficient (no heavy dependencies by default).

Supported channels (easy to extend):
- Console (always on)
- File logging
- Telegram (optional, via requests)

Usage:
    from app.alerts import alert_manager

    alert_manager.send("HIGH", "Daily loss limit approaching", data={"loss": 18000})
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

ALERT_LOG = Path("data/alerts.log")
ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)


class AlertManager:
    def __init__(self):
        self.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")

    def send(self, level: str, message: str, data: Optional[Dict[str, Any]] = None):
        """Send alert to all configured channels."""
        timestamp = datetime.now().isoformat()
        payload = {
            "timestamp": timestamp,
            "level": level.upper(),
            "message": message,
            "data": data or {}
        }

        # Always log to file (very lightweight)
        self._log_to_file(payload)

        # Console
        print(f"[ALERT {level}] {message} | {data}")

        # Telegram (only if configured)
        if self.telegram_token and self.telegram_chat_id:
            self._send_telegram(payload)

    def _log_to_file(self, payload: Dict):
        with open(ALERT_LOG, "a") as f:
            f.write(json.dumps(payload, default=str) + "\n")

    def _send_telegram(self, payload: Dict):
        try:
            import requests
            text = f"*{payload['level']}* - {payload['message']}\n\n`{json.dumps(payload['data'], indent=2)}`"
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            requests.post(url, data={
                "chat_id": self.telegram_chat_id,
                "text": text,
                "parse_mode": "Markdown"
            }, timeout=5)
        except Exception as e:
            print(f"[ALERT] Telegram failed: {e}")


alert_manager = AlertManager()
