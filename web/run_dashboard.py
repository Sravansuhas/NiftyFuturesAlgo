"""
Convenience script to run the Bloomberg-style dashboard.

Usage:
    python -m web.run_dashboard
"""

import uvicorn
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from web.dashboard import app

if __name__ == "__main__":
    print("Starting NiftyFuturesAlgo Terminal Dashboard...")
    print("Open http://localhost:8050 in your browser")
    uvicorn.run(app, host="0.0.0.0", port=8050, log_level="info")