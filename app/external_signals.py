"""
Manual daily options signals (external sheet — e.g. family/discretionary alerts).

Separate from the futures breakout engine. Stored as JSON by trade date.
Includes journal tracking: entry / target / stop vs live option premium.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from time import time
from typing import Any, Dict, List, Optional, Tuple

from .instruments_manager import ltp_key
from .market_calendar import now_ist
from .options_pnl import enrich_side_pnl, get_index_lot_size, summarize_sheet_pnl

_PREMIUMS_CACHE: Dict[str, Any] = {"payload": None, "ts": 0.0, "sheet_date": None}
_PREMIUMS_CACHE_SEC = 20.0

INDICES = ("SENSEX", "NIFTY", "BANKNIFTY")

# Six premium legs in sheet entry order (SENSEX → NIFTY → BANKNIFTY, CE then PE).
OPTION_LEG_SPECS: Tuple[Tuple[str, str, str], ...] = (
    ("SENSEX", "call", "CE"),
    ("SENSEX", "put", "PE"),
    ("NIFTY", "call", "CE"),
    ("NIFTY", "put", "PE"),
    ("BANKNIFTY", "call", "CE"),
    ("BANKNIFTY", "put", "PE"),
)


def leg_key(index: str, leg: str) -> str:
    """Stable leg id, e.g. NIFTY + call → NIFTY_CE."""
    opt = "CE" if leg == "call" else "PE"
    return f"{index.upper()}_{opt}"


def iter_legs(sheet: Dict[str, Any]):
    """Yield (leg_id, index, leg, option_type, side_dict) for all six chart legs."""
    for index, leg, option_type in OPTION_LEG_SPECS:
        block = (sheet.get("indices") or {}).get(index) or {}
        side = block.get(leg) or {}
        yield leg_key(index, leg), index, leg, option_type, side

DISPLAY_NAMES = {
    "NIFTY": "NIFTY 50",
    "BANKNIFTY": "BANK NIFTY",
    "SENSEX": "SENSEX",
}

SIDE_FIELDS = (
    "entry", "target", "stop_loss", "strike", "status", "remarks",
    "journal_status", "entry_fill", "last_ltp", "session_high", "session_low",
    "checked_at", "target_met_at", "stop_hit_at", "entered_at", "outcome_note",
)

TERMINAL_JOURNAL = frozenset({"target_met", "stop_hit", "skipped", "expired"})

DEFAULT_SIDE = {
    "entry": None,
    "target": None,
    "stop_loss": None,
    "strike": None,
    "status": "Ready",
    "remarks": "",
    "journal_status": "watching",
    "entry_fill": None,
    "last_ltp": None,
    "session_high": None,
    "session_low": None,
    "checked_at": None,
    "target_met_at": None,
    "stop_hit_at": None,
    "entered_at": None,
    "outcome_note": "",
}

STORE_PATH = Path("data/external_options_signals.json")


def _empty_index_block() -> Dict[str, Any]:
    return {
        "call": deepcopy(DEFAULT_SIDE),
        "put": deepcopy(DEFAULT_SIDE),
    }


def _empty_sheet(trade_date: Optional[date] = None) -> Dict[str, Any]:
    d = trade_date or now_ist().date()
    return {
        "date": d.isoformat(),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "notes": "",
        "indices": {idx: _empty_index_block() for idx in INDICES},
    }


def _normalize_side(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out = deepcopy(DEFAULT_SIDE)
    if not raw:
        return out
    for key in SIDE_FIELDS:
        if key not in raw:
            continue
        val = raw[key]
        if key in ("entry", "target", "stop_loss", "strike", "entry_fill", "last_ltp", "session_high", "session_low"):
            if val is None or val == "":
                out[key] = None
            else:
                try:
                    out[key] = float(val)
                except (TypeError, ValueError):
                    out[key] = None
        else:
            out[key] = str(val) if val is not None else ""
    if not out.get("journal_status"):
        out["journal_status"] = "watching"
    return out


def _normalize_sheet(raw: Dict[str, Any]) -> Dict[str, Any]:
    trade_date = raw.get("date") or now_ist().date().isoformat()
    indices_in = raw.get("indices") or {}
    indices_out = {}
    for idx in INDICES:
        block = indices_in.get(idx) or {}
        indices_out[idx] = {
            "call": _normalize_side(block.get("call")),
            "put": _normalize_side(block.get("put")),
        }
    return {
        "date": str(trade_date)[:10],
        "updated_at": raw.get("updated_at") or datetime.now().isoformat(timespec="seconds"),
        "notes": str(raw.get("notes") or ""),
        "indices": indices_out,
    }


def evaluate_side(side: Dict[str, Any], ltp: Optional[float], now_iso: Optional[str] = None) -> Dict[str, Any]:
    """
    Update journal state for one CE/PE leg using live option premium.

    Rules (option buyer):
    - Enter when LTP <= entry (limit-style) or within 2% above entry
    - After entry: target_met if session high >= target
    - After entry: stop_hit if session low <= entry_fill - L points
    """
    out = deepcopy(side)
    now_iso = now_iso or datetime.now().isoformat(timespec="seconds")

    if out.get("journal_status") in TERMINAL_JOURNAL:
        out["checked_at"] = now_iso
        return out

    entry = out.get("entry")
    target = out.get("target")
    stop_pts = out.get("stop_loss")
    strike = out.get("strike")

    if not strike or entry is None:
        out["journal_status"] = "incomplete"
        out["checked_at"] = now_iso
        return out

    if ltp is None or ltp <= 0:
        out["checked_at"] = now_iso
        if not out.get("outcome_note"):
            out["outcome_note"] = "No live premium — refresh token or check strike/expiry"
        return out

    out["last_ltp"] = round(float(ltp), 2)
    out["session_high"] = round(max(out.get("session_high") or ltp, ltp), 2)
    out["session_low"] = round(min(out.get("session_low") or ltp, ltp), 2)
    out["checked_at"] = now_iso

    status = out.get("journal_status") or "watching"
    entry_fill = out.get("entry_fill")

    if status == "watching" and ltp <= float(entry) * 1.02:
        out["journal_status"] = "entered"
        out["entry_fill"] = round(entry_fill if entry_fill else ltp, 2)
        out["entered_at"] = out.get("entered_at") or now_iso
        entry_fill = out["entry_fill"]
        status = "entered"

    if status == "entered" and entry_fill is not None:
        stop_level = float(entry_fill) - float(stop_pts or 0)
        if target is not None and out["session_high"] >= float(target):
            out["journal_status"] = "target_met"
            out["target_met_at"] = out.get("target_met_at") or now_iso
            out["outcome_note"] = f"Target met — high {out['session_high']} >= T{target}"
        elif stop_pts and out["session_low"] <= stop_level:
            out["journal_status"] = "stop_hit"
            out["stop_hit_at"] = out.get("stop_hit_at") or now_iso
            out["outcome_note"] = f"Stop hit — low {out['session_low']} <= {stop_level:.1f} (L{stop_pts})"
        else:
            dist_t = float(target) - ltp if target else None
            dist_sl = ltp - stop_level if stop_pts else None
            out["outcome_note"] = (
                f"In trade @ {entry_fill} — LTP {ltp}"
                + (f" | {dist_t:.0f} to target" if dist_t is not None else "")
                + (f" | {dist_sl:.0f} above stop" if dist_sl is not None else "")
            )
    else:
        if target is not None and ltp >= float(target):
            out["outcome_note"] = f"LTP {ltp} >= target {target} (not entered at C/P{entry} yet)"
        elif stop_pts and ltp <= float(entry) - float(stop_pts):
            out["outcome_note"] = f"LTP {ltp} below entry−L band (entry {entry}, L{stop_pts})"
        else:
            out["outcome_note"] = f"Watching — LTP {ltp} vs entry {entry}"

    return out


def _side_has_journal_data(side: Dict[str, Any]) -> bool:
    """Include leg in journal if any trade level was entered (not just strike+entry)."""
    for key in ("entry", "target", "stop_loss", "strike"):
        val = side.get(key)
        if val is not None and val != "":
            return True
    return False


def build_journal_rows(sheet: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    d = sheet.get("date")
    for idx in INDICES:
        block = sheet.get("indices", {}).get(idx, {})
        for leg, label in (("call", "CE"), ("put", "PE")):
            side = block.get(leg) or {}
            if not _side_has_journal_data(side):
                continue
            rows.append({
                "date": d,
                "index": idx,
                "display_name": DISPLAY_NAMES.get(idx, idx),
                "leg": leg,
                "option_type": label,
                "strike": side.get("strike"),
                "entry": side.get("entry"),
                "target": side.get("target"),
                "stop_loss": side.get("stop_loss"),
                "journal_status": side.get("journal_status"),
                "last_ltp": side.get("last_ltp"),
                "session_high": side.get("session_high"),
                "session_low": side.get("session_low"),
                "entry_fill": side.get("entry_fill"),
                "outcome_note": side.get("outcome_note"),
                "target_met_at": side.get("target_met_at"),
                "stop_hit_at": side.get("stop_hit_at"),
                "entered_at": side.get("entered_at"),
                "checked_at": side.get("checked_at"),
                "lot_size": side.get("lot_size"),
                "lot_price_inr": side.get("lot_price_inr"),
                "gain_net_1lot": side.get("gain_net_1lot"),
                "loss_net_1lot": side.get("loss_net_1lot"),
                "mtm_net_1lot": side.get("mtm_net_1lot"),
            })
    return rows


_MTM_CACHE: Dict[str, Any] = {"ts": 0.0, "payload": None}
_MTM_CACHE_TTL_SEC = 30


def apply_pnl_to_sheet(sheet: Dict[str, Any], premiums: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Attach 1-lot P&L fields to each leg (in-memory; not persisted on save)."""
    from .instruments_manager import instruments_manager
    from .options_chain import options_chain_manager

    out = deepcopy(sheet)
    live = premiums or fetch_live_premiums(out)
    options_chain_manager.bind(instruments_manager.kite)

    for idx in INDICES:
        block = out.get("indices", {}).get(idx, {})
        live_row = (live.get("indices") or {}).get(idx) or {}

        for leg, opt_type in (("call", "CE"), ("put", "PE")):
            side = block.get(leg) or {}
            if not side.get("strike") and side.get("entry") is None:
                continue

            leg_strike = side.get("strike")
            expiry = live_row.get("expiry")
            if not expiry and leg_strike and instruments_manager.kite:
                exp = options_chain_manager.resolve_expiry(idx)
                expiry = exp.isoformat() if exp else None

            lot_size = get_index_lot_size(idx)
            if leg_strike and expiry and instruments_manager.kite:
                inst = instruments_manager.get_option_instruments(
                    idx, opt_type, expiry, float(leg_strike),
                )
                if inst:
                    lot_size = get_index_lot_size(idx, inst)

            ltp_key = f"{leg}_ltp"
            ltp = live_row.get(ltp_key) if live_row.get(ltp_key) is not None else side.get("last_ltp")
            block[leg] = enrich_side_pnl(side, ltp=ltp, lot_size=lot_size)
        out["indices"][idx] = block

    out["pnl_summary"] = summarize_sheet_pnl(out)
    return out


def get_today_options_mtm(force: bool = False) -> Dict[str, Any]:
    """Cached options-sheet MTM for dashboard (today IST)."""
    global _MTM_CACHE
    now = time()
    if not force and _MTM_CACHE.get("payload") and (now - float(_MTM_CACHE.get("ts") or 0)) < _MTM_CACHE_TTL_SEC:
        return _MTM_CACHE["payload"]

    today = now_ist().date().isoformat()
    sheet = external_signals_store.get(today)
    has_legs = any(
        (block.get("call") or {}).get("strike") or (block.get("call") or {}).get("entry")
        or (block.get("put") or {}).get("strike") or (block.get("put") or {}).get("entry")
        for block in sheet.get("indices", {}).values()
    )
    if not has_legs:
        payload = {"date": today, "available": False, "mtm_net": 0.0, "mtm_gross": 0.0, "legs": 0, "in_trade": 0}
        _MTM_CACHE = {"ts": now, "payload": payload}
        return payload

    enriched = apply_pnl_to_sheet(sheet)
    summary = enriched.get("pnl_summary") or {}
    payload = {
        "date": today,
        "available": True,
        "mtm_net": summary.get("mtm_net", 0.0),
        "mtm_gross": summary.get("mtm_gross", 0.0),
        "legs": summary.get("legs", 0),
        "in_trade": summary.get("in_trade", 0),
        "max_gain_net_if_all_hit": summary.get("max_gain_net_if_all_hit", 0.0),
        "max_loss_net_if_all_stop": summary.get("max_loss_net_if_all_stop", 0.0),
    }
    _MTM_CACHE = {"ts": now, "payload": payload}
    return payload


def bust_options_mtm_cache() -> None:
    global _MTM_CACHE, _PREMIUMS_CACHE
    _MTM_CACHE = {"ts": 0.0, "payload": None}
    _PREMIUMS_CACHE = {"payload": None, "ts": 0.0, "sheet_date": None}


class ExternalSignalsStore:
    def __init__(self, path: Path = STORE_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load_all(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"sheets": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "sheets" in data:
                return data
        except Exception:
            pass
        return {"sheets": {}}

    def _save_all(self, data: Dict[str, Any]) -> None:
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

    def list_dates(self) -> List[str]:
        sheets = self._load_all().get("sheets", {})
        return sorted(sheets.keys(), reverse=True)

    def get(self, trade_date: Optional[str] = None) -> Dict[str, Any]:
        key = (trade_date or now_ist().date().isoformat())[:10]
        sheets = self._load_all().get("sheets", {})
        if key in sheets:
            return _normalize_sheet(sheets[key])
        return _empty_sheet(date.fromisoformat(key))

    def save(self, sheet: Dict[str, Any]) -> Dict[str, Any]:
        normalized = _normalize_sheet(sheet)
        normalized["updated_at"] = datetime.now().isoformat(timespec="seconds")
        data = self._load_all()
        data.setdefault("sheets", {})[normalized["date"]] = normalized
        self._save_all(data)
        return normalized

    def delete(self, trade_date: str) -> bool:
        key = str(trade_date)[:10]
        data = self._load_all()
        sheets = data.get("sheets", {})
        if key not in sheets:
            return False
        del sheets[key]
        self._save_all(data)
        return True

    def journal_for_date(self, trade_date: str) -> List[Dict[str, Any]]:
        return build_journal_rows(self.get(trade_date))

    def journal(self, limit: int = 60, trade_date: Optional[str] = None) -> List[Dict[str, Any]]:
        if trade_date:
            return self.journal_for_date(str(trade_date)[:10])
        dates = self.list_dates()[:limit]
        rows: List[Dict[str, Any]] = []
        sheets = self._load_all().get("sheets", {})
        for d in dates:
            sheet = _normalize_sheet(sheets[d])
            rows.extend(build_journal_rows(sheet))
        return rows


external_signals_store = ExternalSignalsStore()


def fetch_live_premiums(sheet: Dict[str, Any]) -> Dict[str, Any]:
    """Kite LTP for entered strikes (CE/PE premiums) — single batched quote call."""
    from .instruments_manager import instruments_manager
    from .options_chain import options_chain_manager

    sheet_date = str(sheet.get("date") or "")[:10]
    now = time()
    cached = _PREMIUMS_CACHE.get("payload")
    if (
        cached
        and _PREMIUMS_CACHE.get("sheet_date") == sheet_date
        and now - float(_PREMIUMS_CACHE.get("ts") or 0) < _PREMIUMS_CACHE_SEC
    ):
        return cached

    kite = instruments_manager.kite
    if not kite:
        return {"available": False, "error": "kite_unavailable", "indices": {}}

    options_chain_manager.bind(kite)
    out: Dict[str, Any] = {"available": True, "indices": {}}
    quote_keys: List[str] = []
    leg_refs: List[Tuple[str, str, str, str]] = []

    for idx in INDICES:
        block = sheet.get("indices", {}).get(idx, {})
        call_strike = block.get("call", {}).get("strike")
        put_strike = block.get("put", {}).get("strike")
        if not call_strike and not put_strike:
            out["indices"][idx] = {"error": "no_strike"}
            continue

        expiry = options_chain_manager.resolve_expiry(idx)
        if not expiry:
            out["indices"][idx] = {
                "error": "no_expiry",
                "call_strike": call_strike,
                "put_strike": put_strike,
            }
            continue

        row: Dict[str, Any] = {
            "call_strike": call_strike,
            "put_strike": put_strike,
            "expiry": expiry.isoformat(),
            "call_ltp": None,
            "put_ltp": None,
        }
        for side, opt_type in (("call", "CE"), ("put", "PE")):
            leg_strike = block.get(side, {}).get("strike")
            if not leg_strike:
                row[f"{side}_symbol"] = None
                continue
            inst = instruments_manager.get_option_instruments(
                idx, opt_type, expiry, float(leg_strike),
            )
            if not inst:
                row[f"{side}_symbol"] = None
                row[f"{side}_error"] = f"no_instrument_{opt_type}_{leg_strike}"
                continue
            exchange = inst.get("exchange") or ("BFO" if idx == "SENSEX" else "NFO")
            sym = inst.get("tradingsymbol")
            row[f"{side}_symbol"] = sym
            row[f"{side}_strike"] = leg_strike
            if sym:
                key = ltp_key(sym, exchange)
                quote_keys.append(key)
                leg_refs.append((idx, side, key, sym))
        out["indices"][idx] = row

    prices = instruments_manager.fetch_ltp_batch(quote_keys)
    for idx, side, key, _sym in leg_refs:
        prem = prices.get(key)
        if prem is not None:
            out["indices"][idx][f"{side}_ltp"] = prem

    _PREMIUMS_CACHE.update({"payload": out, "ts": now, "sheet_date": sheet_date})
    return out


def evaluate_and_save(trade_date: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]:
    """Fetch live premiums, update journal state, persist sheet."""
    sheet = external_signals_store.get(trade_date)
    premiums = fetch_live_premiums(sheet)
    now_iso = datetime.now().isoformat(timespec="seconds")

    if premiums.get("available"):
        for idx in INDICES:
            live = premiums.get("indices", {}).get(idx) or {}
            block = sheet["indices"].get(idx, {})
            call_side = block.get("call", {})
            put_side = block.get("put", {})
            if live.get("call_ltp") is not None and _side_has_journal_data(call_side):
                block["call"] = evaluate_side(call_side, live.get("call_ltp"), now_iso)
            elif call_side and _side_has_journal_data(call_side):
                block["call"] = evaluate_side(call_side, None, now_iso)
            if live.get("put_ltp") is not None and _side_has_journal_data(put_side):
                block["put"] = evaluate_side(put_side, live.get("put_ltp"), now_iso)
            elif put_side and _side_has_journal_data(put_side):
                block["put"] = evaluate_side(put_side, None, now_iso)
            sheet["indices"][idx] = block

    saved = external_signals_store.save(sheet)
    bust_options_mtm_cache()
    enriched = apply_pnl_to_sheet(saved, premiums)
    rows = build_journal_rows(enriched)
    return enriched, premiums, rows