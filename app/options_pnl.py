"""
1-lot options P&L helpers for manual external signals (CE/PE buyer).

Uses Zerodha-style costs and April 2026 STT on options (0.15% on sell premium).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from .instruments_manager import _FALLBACK_LOT_SIZES

# April 2026 STT: 0.15% on option premium (sell side) — see INDIAN_FO_KNOWLEDGE_BASE.md
OPTIONS_STT_SELL_RATE = 0.0015
BROKERAGE_PER_ORDER = 20.0
OTHER_CHARGES_BUFFER = 22.0  # exchange + SEBI + GST + stamp (conservative per round turn)


@dataclass(frozen=True)
class OptionsLotPnl:
    lot_size: int
    premium: Optional[float]
    lot_price_inr: Optional[float]
    entry_premium: Optional[float]
    target_premium: Optional[float]
    stop_premium: Optional[float]
    gain_gross: Optional[float]
    loss_gross: Optional[float]
    costs_round_turn: Optional[float]
    gain_net: Optional[float]
    loss_net: Optional[float]
    mtm_gross: Optional[float]
    mtm_net: Optional[float]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "lot_size": self.lot_size,
            "premium": self.premium,
            "lot_price_inr": self.lot_price_inr,
            "entry_premium": self.entry_premium,
            "target_premium": self.target_premium,
            "stop_premium": self.stop_premium,
            "gain_gross_1lot": self.gain_gross,
            "loss_gross_1lot": self.loss_gross,
            "costs_round_turn": self.costs_round_turn,
            "gain_net_1lot": self.gain_net,
            "loss_net_1lot": self.loss_net,
            "mtm_gross_1lot": self.mtm_gross,
            "mtm_net_1lot": self.mtm_net,
        }


def get_index_lot_size(index: str, option_inst: Optional[Dict[str, Any]] = None) -> int:
    if option_inst and option_inst.get("lot_size"):
        return int(option_inst["lot_size"])
    return int(_FALLBACK_LOT_SIZES.get(index.upper(), 1))


def options_round_turn_cost(entry_premium: float, exit_premium: float, lot_size: int) -> float:
    """Estimated all-in round turn cost for 1 lot (buy + sell)."""
    if entry_premium <= 0 or exit_premium <= 0 or lot_size <= 0:
        return BROKERAGE_PER_ORDER * 2 + OTHER_CHARGES_BUFFER
    brokerage = BROKERAGE_PER_ORDER * 2
    stt_sell = exit_premium * lot_size * OPTIONS_STT_SELL_RATE
    return round(brokerage + stt_sell + OTHER_CHARGES_BUFFER, 2)


def exit_only_cost(exit_premium: float, lot_size: int) -> float:
    """Cost to close an open long (sell leg only + half buffer)."""
    if exit_premium <= 0 or lot_size <= 0:
        return BROKERAGE_PER_ORDER + OTHER_CHARGES_BUFFER * 0.5
    stt_sell = exit_premium * lot_size * OPTIONS_STT_SELL_RATE
    return round(BROKERAGE_PER_ORDER + stt_sell + OTHER_CHARGES_BUFFER * 0.5, 2)


def compute_lot_pnl(
    *,
    entry: Optional[float],
    target: Optional[float],
    stop_loss_pts: Optional[float],
    ltp: Optional[float],
    lot_size: int,
    journal_status: str = "watching",
    entry_fill: Optional[float] = None,
) -> OptionsLotPnl:
    """
    1-lot buyer economics.

    - Potential gain/loss uses entry C/P (or fill if already entered).
    - MTM only when status is entered / target_met / stop_hit.
    """
    premium = float(ltp) if ltp and ltp > 0 else None
    lot_price = round(premium * lot_size, 2) if premium is not None else None

    ref_entry = entry_fill if entry_fill is not None else entry
    if ref_entry is None or lot_size <= 0:
        return OptionsLotPnl(
            lot_size=lot_size,
            premium=premium,
            lot_price_inr=lot_price,
            entry_premium=ref_entry,
            target_premium=target,
            stop_premium=None,
            gain_gross=None,
            loss_gross=None,
            costs_round_turn=None,
            gain_net=None,
            loss_net=None,
            mtm_gross=None,
            mtm_net=None,
        )

    ref_entry = float(ref_entry)
    stop_premium = ref_entry - float(stop_loss_pts or 0) if stop_loss_pts else None

    gain_gross = None
    loss_gross = None
    gain_net = None
    loss_net = None
    costs = None

    if target is not None:
        gain_gross = round((float(target) - ref_entry) * lot_size, 2)
        costs = options_round_turn_cost(ref_entry, float(target), lot_size)
        gain_net = round(gain_gross - costs, 2)

    if stop_loss_pts:
        loss_gross = round(float(stop_loss_pts) * lot_size, 2)
        exit_at_stop = stop_premium if stop_premium and stop_premium > 0 else ref_entry * 0.5
        stop_costs = options_round_turn_cost(ref_entry, exit_at_stop, lot_size)
        loss_net = round(-(loss_gross + stop_costs), 2)

    mtm_gross = None
    mtm_net = None
    in_trade = journal_status in {"entered", "target_met", "stop_hit"}
    if in_trade and premium is not None:
        mtm_gross = round((premium - ref_entry) * lot_size, 2)
        mtm_net = round(mtm_gross - exit_only_cost(premium, lot_size), 2)

    return OptionsLotPnl(
        lot_size=lot_size,
        premium=premium,
        lot_price_inr=lot_price,
        entry_premium=ref_entry,
        target_premium=float(target) if target is not None else None,
        stop_premium=round(stop_premium, 2) if stop_premium is not None else None,
        gain_gross=gain_gross,
        loss_gross=loss_gross,
        costs_round_turn=costs,
        gain_net=gain_net,
        loss_net=loss_net,
        mtm_gross=mtm_gross,
        mtm_net=mtm_net,
    )


def enrich_side_pnl(
    side: Dict[str, Any],
    *,
    ltp: Optional[float],
    lot_size: int,
) -> Dict[str, Any]:
    pnl = compute_lot_pnl(
        entry=side.get("entry"),
        target=side.get("target"),
        stop_loss_pts=side.get("stop_loss"),
        ltp=ltp if ltp is not None else side.get("last_ltp"),
        lot_size=lot_size,
        journal_status=side.get("journal_status") or "watching",
        entry_fill=side.get("entry_fill"),
    )
    out = dict(side)
    out.update(pnl.as_dict())
    return out


def summarize_sheet_pnl(sheet: Dict[str, Any]) -> Dict[str, Any]:
    """Aggregate MTM and scenario P&L across all legs on a sheet."""
    total_mtm_gross = 0.0
    total_mtm_net = 0.0
    total_gain_net = 0.0
    total_loss_net = 0.0
    legs = 0
    in_trade = 0

    for idx, block in (sheet.get("indices") or {}).items():
        for leg in ("call", "put"):
            side = block.get(leg) or {}
            if not side.get("strike") and side.get("entry") is None:
                continue
            legs += 1
            mtm_g = side.get("mtm_gross_1lot")
            mtm_n = side.get("mtm_net_1lot")
            if side.get("journal_status") in {"entered", "target_met", "stop_hit"}:
                in_trade += 1
                if mtm_g is not None:
                    total_mtm_gross += float(mtm_g)
                if mtm_n is not None:
                    total_mtm_net += float(mtm_n)
            gain_n = side.get("gain_net_1lot")
            loss_n = side.get("loss_net_1lot")
            if gain_n is not None:
                total_gain_net += float(gain_n)
            if loss_n is not None:
                total_loss_net += float(loss_n)

    return {
        "legs": legs,
        "in_trade": in_trade,
        "mtm_gross": round(total_mtm_gross, 2),
        "mtm_net": round(total_mtm_net, 2),
        "max_gain_net_if_all_hit": round(total_gain_net, 2),
        "max_loss_net_if_all_stop": round(total_loss_net, 2),
    }