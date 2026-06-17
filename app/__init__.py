"""
Aegis — Deterministic, risk-first algorithmic trading platform for Indian index F&O.

Phase 1+2 complete infrastructure: state machine, risk gatekeeper, reconciliation,
market calendar, audit, token mgmt, previous-candle breakout strategy (live + backtest),
guarded execution, and functional backtesting framework.

All orders flow through RiskGatekeeper. Broker state is authoritative.
"""

__version__ = "0.3.0"
__all__ = [
    "state_machine",
    "risk_gatekeeper",
    "market_calendar",
    "broker_reconciliation",
    "strategy",
    "main",
]
