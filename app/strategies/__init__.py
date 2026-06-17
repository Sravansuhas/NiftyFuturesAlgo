"""
Options strategy proposals — research layer only.

All modules return signal dicts tagged research_only=True for future routing
through OptionsRiskChecker and RiskGatekeeper. No order placement.
"""

from .iron_condor import default_strikes_from_spot, propose_iron_condor
from .straddle_proposal import (
    propose_long_straddle,
    propose_short_straddle,
    propose_straddle,
)

__all__ = [
    "propose_iron_condor",
    "default_strikes_from_spot",
    "propose_straddle",
    "propose_long_straddle",
    "propose_short_straddle",
]