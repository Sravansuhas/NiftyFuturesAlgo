"""
Convenience script to run the Aegis Bloomberg-style terminal.

Usage:
    python -m web.run_dashboard
"""

import asyncio
import uvicorn
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Add project root to path
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from web.dashboard import app

if __name__ == "__main__":
    print("Starting Aegis Terminal Dashboard...")
    print("Open http://localhost:8050 in your browser")
    uvicorn.run(app, host="0.0.0.0", port=8050, log_level="info")