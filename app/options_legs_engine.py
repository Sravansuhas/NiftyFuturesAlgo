"""
Six-leg options premium engine for manual external signals sheet.

Tracks NIFTY / BANKNIFTY / SENSEX CE+PE via WebSocket (preferred) with REST
fallback. Feeds main loop terminal output, dashboard status/SSE, and
/api/options-legs/live.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from copy import deepcopy
from datetime import datetime
from typing import Any, Deque, Dict, List, Optional, Tuple

from .data_feed import STALE_PRICE_SECONDS, BaseDataFeed
from .external_signals import (
    DISPLAY_NAMES,
    OPTION_LEG_SPECS,
    _side_has_journal_data,
    apply_pnl_to_sheet,
    build_journal_rows,
    bust_options_mtm_cache,
    evaluate_side,
    external_signals_store,
    fetch_live_premiums,
    iter_legs,
    leg_key,
)

logger = logging.getLogger(__name__)

SPARKLINE_LEN = 20

_snapshots: Dict[str, dict] = {}
_current_date: Optional[str] = None
_snap_lock = threading.RLock()


def get_all_snapshots() -> Dict[str, dict]:
    with _snap_lock:
        return deepcopy(_snapshots)


def get_snapshot(leg_id: str) -> dict:
    with _snap_lock:
        snap = _snapshots.get(leg_id, {})
        return deepcopy(snap) if snap else {}


class OptionsLegsEngine:
    """Resolve option tokens, stream WS ticks, evaluate journal rules, expose snapshots."""

    def __init__(self) -> None:
        self._resolved: List[Dict[str, Any]] = []
        self._resolve_cache_key: Optional[str] = None
        self._token_by_leg: Dict[str, int] = {}
        self._sparklines: Dict[str, Deque[float]] = {}
        self._data_source: Dict[str, str] = {}
        self._data_age: Dict[str, float] = {}
        self._subscribed_tokens: List[int] = []
        self._external_save_pending = False

    def bust_instrument_cache(self) -> None:
        """Clear cached instrument resolution (call after sheet save via API)."""
        self._resolved = []
        self._resolve_cache_key = None
        self._token_by_leg = {}

    def _sheet_resolve_key(self, sheet: Dict[str, Any]) -> str:
        parts = [str(sheet.get("date") or "")]
        for _leg_id, index, leg, _opt, side in iter_legs(sheet):
            parts.append(f"{index}:{leg}:{side.get('strike')}")
        return "|".join(parts)

    def resolve_instruments(self, sheet: Dict[str, Any], *, force: bool = False) -> List[Dict[str, Any]]:
        """
        Resolve Kite tokens for each leg that has a strike on the sheet.

        Returns list of dicts:
        leg_id, index, leg, option_type, strike, token, tradingsymbol, exchange, expiry
        """
        cache_key = self._sheet_resolve_key(sheet)
        if not force and self._resolved and cache_key == self._resolve_cache_key:
            return list(self._resolved)

        from .instruments_manager import instruments_manager
        from .options_chain import options_chain_manager

        if not instruments_manager.kite:
            self._resolved = []
            self._resolve_cache_key = cache_key
            self._token_by_leg = {}
            return []

        options_chain_manager.bind(instruments_manager.kite)
        resolved: List[Dict[str, Any]] = []
        token_map: Dict[str, int] = {}

        for _leg_id, index, leg, option_type, side in iter_legs(sheet):
            strike = side.get("strike")
            if strike is None or strike == "":
                continue

            expiry_dt = options_chain_manager.resolve_expiry(index)
            if not expiry_dt:
                logger.debug("[OptionsLegs] No expiry for %s %s", index, option_type)
                continue

            inst = instruments_manager.get_option_instruments(
                index, option_type, expiry_dt, float(strike),
            )
            if not inst or not inst.get("instrument_token"):
                logger.debug(
                    "[OptionsLegs] No instrument %s %s strike=%s expiry=%s",
                    index, option_type, strike, expiry_dt,
                )
                continue

            leg_id = leg_key(index, leg)
            row = {
                "leg_id": leg_id,
                "index": index,
                "leg": leg,
                "option_type": option_type,
                "strike": float(strike),
                "token": int(inst["instrument_token"]),
                "tradingsymbol": inst.get("tradingsymbol"),
                "exchange": inst.get("exchange") or ("BFO" if index == "SENSEX" else "NFO"),
                "expiry": expiry_dt.isoformat(),
            }
            resolved.append(row)
            token_map[leg_id] = row["token"]
            self._sparklines.setdefault(leg_id, deque(maxlen=SPARKLINE_LEN))

        self._resolved = resolved
        self._resolve_cache_key = cache_key
        self._token_by_leg = token_map
        return list(resolved)

    def refresh_ws_subscriptions(
        self,
        ws_feed: Optional[BaseDataFeed],
        sheet: Dict[str, Any],
        *,
        futures_tokens: Optional[List[int]] = None,
    ) -> List[int]:
        """Merge futures + option tokens and subscribe on the shared WS feed."""
        if ws_feed is None:
            return []

        option_tokens = [r["token"] for r in self.resolve_instruments(sheet)]
        fut_tokens = [int(t) for t in (futures_tokens or []) if t]
        merged: List[int] = []
        seen = set()
        for tok in fut_tokens + option_tokens:
            if tok not in seen:
                seen.add(tok)
                merged.append(tok)

        if merged:
            ws_feed.subscribe(merged)
            self._subscribed_tokens = merged
            logger.info(
                "[OptionsLegs] WS subscribe: %d futures + %d options → %d tokens",
                len(fut_tokens), len(option_tokens), len(merged),
            )
        return merged

    def _ws_ltp(
        self,
        ws_feed: BaseDataFeed,
        token: int,
    ) -> Tuple[Optional[float], float, str]:
        price, age = ws_feed.get_last_price_with_age(token)
        if price is None or price <= 0:
            return None, age, "none"
        if age > STALE_PRICE_SECONDS:
            return None, age, "none"
        return float(price), age, "WS"

    def tick_from_ws(
        self,
        ws_feed: Optional[BaseDataFeed],
        sheet: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Update last_ltp / session_high / session_low per leg from WS only.
        Does not run evaluate_side or persist.
        """
        if ws_feed is None:
            return sheet

        working = deepcopy(sheet)
        for row in self.resolve_instruments(working):
            leg_id = row["leg_id"]
            index, leg = row["index"], row["leg"]
            token = row["token"]
            ltp, age, source = self._ws_ltp(ws_feed, token)
            self._data_age[leg_id] = age
            if ltp is None:
                self._data_source.setdefault(leg_id, "none")
                continue

            self._data_source[leg_id] = source
            block = working.setdefault("indices", {}).setdefault(index, {})
            side = block.setdefault(leg, {})
            side["last_ltp"] = round(ltp, 2)
            side["session_high"] = round(max(side.get("session_high") or ltp, ltp), 2)
            side["session_low"] = round(min(side.get("session_low") or ltp, ltp), 2)

            spark = self._sparklines.setdefault(leg_id, deque(maxlen=SPARKLINE_LEN))
            spark.append(round(ltp, 2))

        return working

    def _rest_ltp_for_leg(
        self,
        premiums: Dict[str, Any],
        index: str,
        leg: str,
    ) -> Optional[float]:
        live_row = (premiums.get("indices") or {}).get(index) or {}
        return live_row.get(f"{leg}_ltp")

    def _ws_covers_journal_legs(self, ws_feed: Optional[BaseDataFeed], sheet: Dict[str, Any]) -> bool:
        """True when every configured journal leg has a fresh WebSocket tick (skip REST premiums)."""
        if ws_feed is None:
            return False
        need_any = False
        for leg_id, _index, _leg, _opt, side in iter_legs(sheet):
            if not _side_has_journal_data(side):
                continue
            need_any = True
            token = self._token_by_leg.get(leg_id)
            if not token:
                return False
            ltp, _age, _src = self._ws_ltp(ws_feed, token)
            if ltp is None:
                return False
        return need_any

    def run_evaluation(
        self,
        sheet: Dict[str, Any],
        ws_feed: Optional[BaseDataFeed] = None,
        *,
        persist: bool = False,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Evaluate journal rules per leg: WS LTP first, REST fallback.

        Returns (updated_sheet, premiums_meta).
        """
        working = deepcopy(sheet)
        if self._ws_covers_journal_legs(ws_feed, working):
            premiums = {"available": True, "indices": {}, "source": "ws"}
        else:
            premiums = fetch_live_premiums(working)
        now_iso = datetime.now().isoformat(timespec="seconds")

        for leg_id, index, leg, _opt, side in iter_legs(working):
            if not _side_has_journal_data(side):
                continue

            ltp: Optional[float] = None
            source = "none"
            age = float("inf")

            token = self._token_by_leg.get(leg_id)
            if ws_feed is not None and token:
                ws_ltp, ws_age, ws_src = self._ws_ltp(ws_feed, token)
                if ws_ltp is not None:
                    ltp = ws_ltp
                    source = ws_src
                    age = ws_age

            if ltp is None and premiums.get("available"):
                rest_ltp = self._rest_ltp_for_leg(premiums, index, leg)
                if rest_ltp is not None and rest_ltp > 0:
                    ltp = float(rest_ltp)
                    source = "REST"
                    age = 0.0

            self._data_source[leg_id] = source
            self._data_age[leg_id] = age

            block = working.setdefault("indices", {}).setdefault(index, {})
            evaluated = evaluate_side(side, ltp, now_iso)
            block[leg] = evaluated

            if ltp is not None and ltp > 0:
                spark = self._sparklines.setdefault(leg_id, deque(maxlen=SPARKLINE_LEN))
                spark.append(round(ltp, 2))

        if persist:
            saved = external_signals_store.save(working)
            bust_options_mtm_cache()
            working = saved

        return working, premiums

    def build_snapshots(self, sheet: Dict[str, Any]) -> Dict[str, dict]:
        """Build leg_id → snapshot dict for dashboard / terminal."""
        enriched = apply_pnl_to_sheet(sheet)
        resolved_by_leg = {r["leg_id"]: r for r in self.resolve_instruments(sheet)}
        out: Dict[str, dict] = {}

        for leg_id, index, leg, option_type, side in iter_legs(enriched):
            inst = resolved_by_leg.get(leg_id, {})
            spark = list(self._sparklines.get(leg_id, deque()))
            age = self._data_age.get(leg_id, float("inf"))
            out[leg_id] = {
                "leg_id": leg_id,
                "index": index,
                "leg": leg,
                "option_type": option_type,
                "display_name": f"{DISPLAY_NAMES.get(index, index)} {option_type}",
                "last_ltp": side.get("last_ltp"),
                "strike": side.get("strike"),
                "entry": side.get("entry"),
                "target": side.get("target"),
                "stop_loss": side.get("stop_loss"),
                "status": side.get("status"),
                "remarks": side.get("remarks"),
                "journal_status": side.get("journal_status") or "watching",
                "entry_fill": side.get("entry_fill"),
                "session_high": side.get("session_high"),
                "session_low": side.get("session_low"),
                "checked_at": side.get("checked_at"),
                "entered_at": side.get("entered_at"),
                "target_met_at": side.get("target_met_at"),
                "stop_hit_at": side.get("stop_hit_at"),
                "tradingsymbol": inst.get("tradingsymbol"),
                "token": inst.get("token"),
                "expiry": inst.get("expiry"),
                "exchange": inst.get("exchange"),
                "data_source": self._data_source.get(leg_id, "none"),
                "data_age_seconds": round(age, 1) if age != float("inf") else None,
                "sparkline": spark[-SPARKLINE_LEN:],
                "outcome_note": side.get("outcome_note") or "",
                "lot_size": side.get("lot_size"),
                "lot_price_inr": side.get("lot_price_inr"),
                "gain_net_1lot": side.get("gain_net_1lot"),
                "loss_net_1lot": side.get("loss_net_1lot"),
                "mtm_gross_1lot": side.get("mtm_gross_1lot"),
                "mtm_net_1lot": side.get("mtm_net_1lot"),
            }
        return out

    def update_all_snapshots(self, sheet: Dict[str, Any]) -> Dict[str, dict]:
        global _snapshots, _current_date
        built = self.build_snapshots(sheet)
        with _snap_lock:
            _snapshots = built
            _current_date = sheet.get("date")
            return deepcopy(_snapshots)

    def _build_summary(self, legs: Dict[str, dict], trade_date: Optional[str]) -> Dict[str, Any]:
        status_counts: Dict[str, int] = {}
        mtm_gross = 0.0
        mtm_net = 0.0
        in_trade = 0
        configured = 0

        for snap in legs.values():
            if snap.get("strike") or snap.get("entry") is not None:
                configured += 1
            status = snap.get("journal_status") or "watching"
            status_counts[status] = status_counts.get(status, 0) + 1
            if status in {"entered", "target_met", "stop_hit"}:
                in_trade += 1
                if snap.get("mtm_gross_1lot") is not None:
                    mtm_gross += float(snap["mtm_gross_1lot"])
                if snap.get("mtm_net_1lot") is not None:
                    mtm_net += float(snap["mtm_net_1lot"])

        return {
            "date": trade_date,
            "legs": configured,
            "in_trade": in_trade,
            "mtm_gross": round(mtm_gross, 2),
            "mtm_net": round(mtm_net, 2),
            "status_counts": status_counts,
        }

    def refresh_from_sheet(
        self,
        sheet: Dict[str, Any],
        premiums: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Rebuild in-memory leg snapshots from a saved/enriched sheet."""
        if premiums is not None:
            enriched = apply_pnl_to_sheet(sheet, premiums)
        else:
            enriched = apply_pnl_to_sheet(sheet)
        self.update_all_snapshots(enriched)
        bust_options_mtm_cache()

    def get_all_snapshots(self) -> Dict[str, dict]:
        return get_all_snapshots()

    def _ensure_loaded(self) -> None:
        with _snap_lock:
            if _snapshots or _current_date:
                return
        try:
            sheet = external_signals_store.get()
            if any(
                (block.get("call") or {}).get("strike") or (block.get("call") or {}).get("entry")
                or (block.get("put") or {}).get("strike") or (block.get("put") or {}).get("entry")
                for block in (sheet.get("indices") or {}).values()
            ):
                self.refresh_from_sheet(sheet)
        except Exception:
            pass

    def get_status_payload(self, *, fast: bool = False) -> Dict[str, Any]:
        if not fast:
            self._ensure_loaded()
        with _snap_lock:
            legs = deepcopy(_snapshots)
            trade_date = _current_date
            subscribed = len(self._subscribed_tokens)
        summary = self._build_summary(legs, trade_date)
        return {
            "available": summary.get("legs", 0) > 0,
            "date": trade_date,
            "legs": legs,
            "summary": summary,
            "subscribed_tokens": subscribed,
        }

    def build_live_response(self, sheet: Dict[str, Any]) -> Dict[str, Any]:
        """Full live desk payload for /api/options-legs/live."""
        working = deepcopy(sheet)
        premiums = fetch_live_premiums(working)
        enriched = apply_pnl_to_sheet(working, premiums)
        self.update_all_snapshots(enriched)
        status = self.get_status_payload()

        return {
            "available": status.get("available", False),
            "date": enriched.get("date"),
            "timestamp": datetime.now().isoformat(),
            "sheet": enriched,
            "premiums": premiums,
            "legs": status.get("legs", {}),
            "summary": status.get("summary", {}),
            "journal_rows": build_journal_rows(enriched),
            "pnl_summary": enriched.get("pnl_summary"),
            "subscribed_tokens": len(self._subscribed_tokens),
        }

    def mark_external_save(self) -> None:
        """Signal main loop to reload sheet after API/dashboard save."""
        self._external_save_pending = True

    def consume_external_save(self) -> bool:
        if self._external_save_pending:
            self._external_save_pending = False
            return True
        return False

    def on_sheet_saved(self, saved: Dict[str, Any]) -> None:
        """Hook after external_signals_store.save() — bust token cache and refresh."""
        self.mark_external_save()
        self.bust_instrument_cache()
        self.refresh_from_sheet(saved)


options_legs_engine = OptionsLegsEngine()