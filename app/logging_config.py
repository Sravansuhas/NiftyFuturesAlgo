import logging
import sys
from datetime import datetime

def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Central structured logger for the entire trading system."""
    logger = logging.getLogger("NiftyFuturesAlgo")
    logger.setLevel(level)

    if not logger.handlers:
        # Console handler
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger

# Convenience function for trade-specific logging
def log_trade_event(logger: logging.Logger, event: str, trade_id: str = None, **kwargs):
    extra = f" | trade_id={trade_id}" if trade_id else ""
    details = " | ".join([f"{k}={v}" for k, v in kwargs.items()])
    logger.info(f"{event}{extra} | {details}")