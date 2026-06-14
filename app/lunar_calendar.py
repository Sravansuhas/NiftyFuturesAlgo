"""
Lunar phase and Indian panchang metadata for NSE F&O research.

Provides two parallel label systems (per Indian market research conventions):
  1. Panchang-style tithi/paksha at Mumbai sunrise (Shukla/Krishna paksha,
     Amavasya, Purnima, Ekadashi) — matches cultural trader hypotheses.
  2. Astronomical synodic phases (new/full moon instants) with ±3/±5 day
     trading windows per Karamchandani et al. (2014) NIFTY study.

IST session dates are keyed to NSE regular hours (09:15–15:30/15:40).
Panchang tithi is anchored to Mumbai sunrise (~06:30 IST), not market open.

RESEARCH METADATA ONLY — not a validated alpha signal. See README folklore section.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import ephem

from .market_calendar import (
    IST,
    MARKET_HOLIDAYS,
    MUHURAT_TRADING_2026,
    holiday_name,
    is_expiry_day,
    is_trading_holiday,
    next_trading_day,
    now_ist,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LUNAR_FILE = PROJECT_ROOT / "data" / "lunar_calendar.json"
LUNAR_INDEX_DIR = PROJECT_ROOT / "data" / "lunar_calendar"

# Mumbai / NSE reference (drik-panchang convention)
MUMBAI_LAT = "19:04:33"
MUMBAI_LON = "72:52:39"

TITHI_NAMES = (
    "pratipada", "dwitiya", "tritiya", "chaturthi", "panchami",
    "shashthi", "saptami", "ashtami", "navami", "dashami",
    "ekadashi", "dwadashi", "trayodashi", "chaturdashi",
    "purnima",  # Shukla 15
    "pratipada_krishna", "dwitiya_krishna", "tritiya_krishna", "chaturthi_krishna",
    "panchami_krishna", "shashthi_krishna", "saptami_krishna", "ashtami_krishna",
    "navami_krishna", "dashami_krishna", "ekadashi_krishna", "dwadashi_krishna",
    "trayodashi_krishna", "chaturdashi_krishna", "amavasya",  # Krishna 15
)

LUNAR_LINKED_HOLIDAYS = frozenset({
    "Holi", "Diwali-Balipratipada", "Ganesh Chaturthi", "Dussehra",
    "Shri Ram Navami", "Bakri Id", "Muharram",
})


def _observer() -> ephem.Observer:
    obs = ephem.Observer()
    obs.lat = MUMBAI_LAT
    obs.lon = MUMBAI_LON
    obs.elevation = 0
    obs.pressure = 0
    obs.horizon = "-0:34"  # standard refraction for sunrise
    return obs


def _ephem_to_ist(ephem_date: ephem.Date) -> datetime:
    utc_dt = ephem.Date(ephem_date).datetime().replace(tzinfo=timezone.utc)
    return utc_dt.astimezone(IST)


def mumbai_sunrise(for_date: date) -> datetime:
    """Sunrise at Mumbai on civil date `for_date` (IST)."""
    obs = _observer()
    midnight = datetime(for_date.year, for_date.month, for_date.day, 0, 0, tzinfo=IST)
    obs.date = ephem.Date(midnight)
    rising = obs.next_rising(ephem.Sun())
    return _ephem_to_ist(rising)


def _elongation_degrees(obs: ephem.Observer) -> float:
    sun = ephem.Sun(obs)
    moon = ephem.Moon(obs)
    moon_lon = math.degrees(ephem.Ecliptic(moon).lon)
    sun_lon = math.degrees(ephem.Ecliptic(sun).lon)
    return (moon_lon - sun_lon) % 360.0


def _tithi_from_elongation(elongation: float) -> Tuple[int, str, str, bool, bool, bool]:
    """
    Map moon-sun elongation to tithi 1–30.

    Shukla paksha: tithi 1–15 (waxing, ends at Purnima).
    Krishna paksha: tithi 16–30 (waning, ends at Amavasya).
    """
    tithi_num = int(elongation / 12.0) + 1
    if tithi_num > 30:
        tithi_num = 30
    paksha = "shukla" if tithi_num <= 15 else "krishna"
    tithi_name = TITHI_NAMES[tithi_num - 1]
    is_purnima = tithi_num == 15
    is_amavasya = tithi_num == 30
    is_ekadashi = tithi_num in (11, 26)
    return tithi_num, tithi_name, paksha, is_purnima, is_amavasya, is_ekadashi


def panchang_at_sunrise(for_date: date) -> Dict[str, Any]:
    """Panchang-style labels at Mumbai sunrise (simplified siddhantic model)."""
    sunrise = mumbai_sunrise(for_date)
    obs = _observer()
    obs.date = ephem.Date(sunrise)
    elongation = _elongation_degrees(obs)
    tithi_num, tithi_name, paksha, is_purnima, is_amavasya, is_ekadashi = (
        _tithi_from_elongation(elongation)
    )
    return {
        "calendar_system": "panchang_sunrise_mumbai",
        "sunrise_ist": sunrise.isoformat(),
        "elongation_deg": round(elongation, 2),
        "tithi_num": tithi_num,
        "tithi_name": tithi_name,
        "paksha": paksha,
        "is_purnima": is_purnima,
        "is_amavasya": is_amavasya,
        "is_ekadashi": is_ekadashi,
        "is_amavasya_friday": is_amavasya and for_date.weekday() == 4,
        "caveat": "Simplified tithi from sunrise elongation; may differ ±1 day from drikpanchang.com at boundaries.",
    }


def _phase_name(illumination: float, elongation: float) -> str:
    if illumination < 0.03:
        return "new_moon"
    if illumination > 0.97:
        return "full_moon"
    if elongation < 90:
        return "waxing_crescent"
    if elongation < 180:
        return "waxing_gibbous"
    if elongation < 270:
        return "waning_gibbous"
    return "waning_crescent"


def astronomical_phase(for_date: date) -> Dict[str, Any]:
    """Astronomical phase at NSE market open (09:15 IST)."""
    market_open = datetime(for_date.year, for_date.month, for_date.day, 9, 15, tzinfo=IST)
    obs = _observer()
    obs.date = ephem.Date(market_open)
    elongation = _elongation_degrees(obs)
    moon = ephem.Moon(obs)
    illumination = float(moon.phase) / 100.0
    return {
        "calendar_system": "astronomical",
        "reference_time_ist": market_open.isoformat(),
        "elongation_deg": round(elongation, 2),
        "illumination_pct": round(illumination * 100, 1),
        "phase_name": _phase_name(illumination, elongation),
        "dichev_bucket": "near_new" if elongation < 180 else "near_full",
    }


def _synodic_events_between(start: date, end: date) -> List[Dict[str, Any]]:
    """Alternating astronomical new/full moon instants in range."""
    obs = _observer()
    obs.date = ephem.Date(datetime(start.year, start.month, start.day, 12, 0, tzinfo=IST))
    events: List[Dict[str, Any]] = []

    nm = ephem.previous_new_moon(obs.date)
    while _ephem_to_ist(nm).date() < start:
        nm = ephem.next_new_moon(nm)

    while _ephem_to_ist(nm).date() <= end:
        instant = _ephem_to_ist(nm)
        events.append({
            "phase": "synodic_new_moon",
            "instant_ist": instant.isoformat(),
            "civil_date": instant.date(),
        })
        fm = ephem.next_full_moon(nm)
        finstant = _ephem_to_ist(fm)
        if finstant.date() <= end:
            events.append({
                "phase": "synodic_full_moon",
                "instant_ist": finstant.isoformat(),
                "civil_date": finstant.date(),
            })
        nm = ephem.next_new_moon(fm)

    return events


def _trading_dates_between(start: date, end: date) -> List[date]:
    days: List[date] = []
    probe = start
    while probe <= end:
        if probe.weekday() < 5 and probe not in MARKET_HOLIDAYS:
            days.append(probe)
        probe += timedelta(days=1)
    return days


def _as_date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _nearest_trading_day(event_date: date | str, trading_dates: List[date]) -> Optional[date]:
    if not trading_dates:
        return None
    anchor = _as_date(event_date)
    best = min(trading_dates, key=lambda d: abs((d - anchor).days))
    return best


def _window_flags(
    for_date: date,
    anchors: Dict[str, List[date]],
    trading_index: Dict[date, int],
    window: int,
) -> Dict[str, Any]:
    flags: Dict[str, Any] = {}
    idx = trading_index.get(for_date)
    if idx is None:
        return {
            f"in_new_moon_window_{window}d": False,
            f"in_full_moon_window_{window}d": False,
            f"new_moon_trading_offset_{window}d": None,
            f"full_moon_trading_offset_{window}d": None,
        }

    for phase_key, prefix in (("synodic_new_moon", "new_moon"), ("synodic_full_moon", "full_moon")):
        in_window = False
        offset = None
        for anchor in anchors.get(phase_key, []):
            anchor_idx = trading_index.get(anchor)
            if anchor_idx is None:
                continue
            delta = idx - anchor_idx
            if -window <= delta <= window:
                in_window = True
                offset = delta
                break
        flags[f"in_{prefix}_window_{window}d"] = in_window
        flags[f"{prefix}_trading_offset_{window}d"] = offset
    return flags


def _days_to_next_phase(for_date: date) -> Dict[str, Optional[float]]:
    """Trading-day distance to next synodic new/full moon (approx via civil days)."""
    obs = _observer()
    noon = datetime(for_date.year, for_date.month, for_date.day, 12, 0, tzinfo=IST)
    obs.date = ephem.Date(noon)
    next_nm = ephem.next_new_moon(obs.date)
    next_fm = ephem.next_full_moon(obs.date)
    prev_nm = ephem.previous_new_moon(obs.date)
    prev_fm = ephem.previous_full_moon(obs.date)

    def _days_to(target: ephem.Date) -> float:
        delta = ephem.Date(target) - ephem.Date(noon)
        return round(delta * 1.0, 1)

    return {
        "days_to_next_new_moon": _days_to(next_nm),
        "days_to_next_full_moon": _days_to(next_fm),
        "days_since_prev_new_moon": round(-_days_to(prev_nm), 1),
        "days_since_prev_full_moon": round(-_days_to(prev_fm), 1),
    }


def _festival_tags(for_date: date, panchang: Dict[str, Any]) -> List[str]:
    tags: List[str] = []
    hname = holiday_name(for_date)
    if hname and hname in LUNAR_LINKED_HOLIDAYS:
        tags.append(hname.lower().replace(" ", "_"))
    if for_date in MUHURAT_TRADING_2026:
        tags.append("muhurat_trading")
    if panchang.get("is_amavasya"):
        tags.append("amavasya")
    if panchang.get("is_purnima"):
        tags.append("purnima")
    if panchang.get("is_ekadashi"):
        tags.append("ekadashi")
    return tags


def _folklore_tag(panchang: Dict[str, Any], astro: Dict[str, Any]) -> str:
    if panchang.get("is_amavasya"):
        return "amavasya"
    if panchang.get("is_purnima"):
        return "purnima"
    if panchang.get("is_ekadashi"):
        return "ekadashi"
    if panchang.get("paksha") == "shukla":
        return "shukla_paksha"
    if panchang.get("paksha") == "krishna":
        return "krishna_paksha"
    return astro.get("phase_name", "unknown")


def _is_nse_trading_session(for_date: date) -> bool:
    return for_date.weekday() < 5 and for_date not in MARKET_HOLIDAYS


def build_lunar_day(for_date: date, *, trading_index: Optional[Dict[date, int]] = None,
                    anchors: Optional[Dict[str, List[date]]] = None) -> Dict[str, Any]:
    """Full lunar metadata for one NSE session date."""
    panchang = panchang_at_sunrise(for_date)
    astro = astronomical_phase(for_date)
    phase_dist = _days_to_next_phase(for_date)
    festival = _festival_tags(for_date, panchang)

    windows: Dict[str, Any] = {}
    if trading_index is not None and anchors is not None:
        for w in (3, 5):
            windows.update(_window_flags(for_date, anchors, trading_index, w))

    is_muhurat = for_date in MUHURAT_TRADING_2026
    is_holiday = is_trading_holiday(for_date)
    is_trading = _is_nse_trading_session(for_date)

    return {
        "trade_date": for_date.isoformat(),
        "location": "mumbai_nse",
        "panchang": panchang,
        "astronomical": astro,
        "phase_distances": phase_dist,
        "event_windows": windows,
        "trading_context": {
            "is_trading_session": is_trading,
            "is_trading_holiday": is_holiday,
            "is_expiry_day": is_expiry_day(for_date),
            "is_muhurat_trading": is_muhurat,
            "next_trading_day": next_trading_day(for_date).isoformat() if not is_trading else None,
            "weekday": for_date.strftime("%A"),
        },
        "festival_tags": festival,
        "is_analysis_excluded": is_muhurat or holiday_name(for_date) in (
            "Diwali-Balipratipada", "Holi",
        ),
        "session_hints": {
            "folklore_tag": _folklore_tag(panchang, astro),
            "research_only": True,
            "reasons": ["documented_folklore_not_primary_signal"],
        },
    }


def _build_anchors(
    start: date,
    end: date,
    trading_dates: List[date],
) -> Dict[str, List[date]]:
    anchors: Dict[str, List[date]] = {
        "synodic_new_moon": [],
        "synodic_full_moon": [],
        "amavasya": [],
        "purnima": [],
    }
    for ev in _synodic_events_between(start, end):
        anchor = _nearest_trading_day(ev["civil_date"], trading_dates)
        if anchor:
            anchors[ev["phase"]].append(anchor)

    probe = start
    while probe <= end:
        p = panchang_at_sunrise(probe)
        if p["is_amavasya"]:
            td = probe if _is_nse_trading_session(probe) else _nearest_trading_day(probe, trading_dates)
            if td:
                anchors["amavasya"].append(td)
        if p["is_purnima"]:
            td = probe if _is_nse_trading_session(probe) else _nearest_trading_day(probe, trading_dates)
            if td:
                anchors["purnima"].append(td)
        probe += timedelta(days=1)

    for key in anchors:
        anchors[key] = sorted(set(anchors[key]))
    return anchors


def build_lunar_context(for_date: Optional[date] = None, *, refresh: bool = False) -> Dict[str, Any]:
    """Build or return cached lunar context for a single date (default: today IST)."""
    day = for_date or now_ist().date()
    day_str = day.isoformat()

    if not refresh and LUNAR_FILE.exists():
        try:
            cached = json.loads(LUNAR_FILE.read_text(encoding="utf-8"))
            if cached.get("date_ist") == day_str:
                return cached
        except Exception:
            pass

    start = day - timedelta(days=10)
    end = day + timedelta(days=10)
    trading_dates = _trading_dates_between(start, end)
    trading_index = {d: i for i, d in enumerate(trading_dates)}
    anchors = _build_anchors(start, end, trading_dates)

    payload: Dict[str, Any] = {
        "date_ist": day_str,
        "computed_at": now_ist().isoformat(),
        "source": "ephem",
        "available": True,
        "location": "mumbai_nse",
        **build_lunar_day(day, trading_index=trading_index, anchors=anchors),
    }
    return payload


def build_lunar_range(start: date, end: date) -> Dict[str, Any]:
    """Build lunar metadata index for a date range (backtest enrichment)."""
    trading_dates = _trading_dates_between(start, end)
    trading_index = {d: i for i, d in enumerate(trading_dates)}
    anchors = _build_anchors(start, end, trading_dates)

    days = [
        build_lunar_day(d, trading_index=trading_index, anchors=anchors)
        for d in _date_range(start, end)
    ]

    return {
        "generated_at": now_ist().isoformat(),
        "from": start.isoformat(),
        "to": end.isoformat(),
        "location": "mumbai_nse",
        "source": "ephem",
        "trading_day_count": len(trading_dates),
        "days": days,
        "events": {
            "synodic_new_moon_anchors": [d.isoformat() for d in anchors["synodic_new_moon"]],
            "synodic_full_moon_anchors": [d.isoformat() for d in anchors["synodic_full_moon"]],
            "amavasya_anchors": [d.isoformat() for d in anchors["amavasya"]],
            "purnima_anchors": [d.isoformat() for d in anchors["purnima"]],
        },
        "methodology": {
            "panchang": "Tithi from moon-sun elongation at Mumbai sunrise (simplified).",
            "astronomical_windows": "±3 trading days (Karamchandani 2014 NIFTY); ±5 optional (Dichev/CXO).",
            "amavasya_friday": "IIMB 2023 abstinence hypothesis interaction factor.",
            "stance": "research_metadata_only",
        },
    }


def list_lunar_events(start: date, end: date) -> List[Dict[str, Any]]:
    """Flat list of notable lunar events in range."""
    events: List[Dict[str, Any]] = []
    for ev in _synodic_events_between(start, end):
        events.append({**ev, "calendar_system": "astronomical"})
    probe = start
    while probe <= end:
        p = panchang_at_sunrise(probe)
        if p["is_amavasya"]:
            events.append({
                "phase": "amavasya",
                "civil_date": probe.isoformat(),
                "calendar_system": "panchang_sunrise_mumbai",
                "is_friday": probe.weekday() == 4,
            })
        if p["is_purnima"]:
            events.append({
                "phase": "purnima",
                "civil_date": probe.isoformat(),
                "calendar_system": "panchang_sunrise_mumbai",
            })
        probe += timedelta(days=1)
    for ev in events:
        if isinstance(ev.get("civil_date"), date):
            ev["civil_date"] = ev["civil_date"].isoformat()
    return sorted(events, key=lambda e: e.get("civil_date", e.get("instant_ist", "")))


def _date_range(start: date, end: date) -> List[date]:
    days: List[date] = []
    probe = start
    while probe <= end:
        days.append(probe)
        probe += timedelta(days=1)
    return days


def save_lunar_context(payload: Dict[str, Any], path: Path = LUNAR_FILE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def save_lunar_range(payload: Dict[str, Any], path: Optional[Path] = None) -> Path:
    LUNAR_INDEX_DIR.mkdir(parents=True, exist_ok=True)
    if path is None:
        year = payload.get("from", "range")[:4]
        path = LUNAR_INDEX_DIR / f"{year}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def load_lunar_context(path: Path = LUNAR_FILE) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def format_lunar_summary(payload: Dict[str, Any]) -> str:
    """Human-readable one-day summary for CLI."""
    p = payload.get("panchang", {})
    a = payload.get("astronomical", {})
    hints = payload.get("session_hints", {})
    tc = payload.get("trading_context", {})
    lines = [
        f"  Date (IST): {payload.get('date_ist') or payload.get('trade_date')}",
        f"  Panchang: {p.get('paksha', '?')} paksha, tithi {p.get('tithi_num')} ({p.get('tithi_name')})",
        f"  Amavasya: {p.get('is_amavasya')} | Purnima: {p.get('is_purnima')} | Ekadashi: {p.get('is_ekadashi')}",
        f"  Astronomical: {a.get('phase_name')} ({a.get('illumination_pct')}% lit)",
        f"  Folklore tag: {hints.get('folklore_tag')} (research only)",
        f"  Trading session: {tc.get('is_trading_session')} | Expiry: {tc.get('is_expiry_day')}",
    ]
    ew = payload.get("event_windows", {})
    if ew:
        lines.append(
            f"  Windows: new±3d={ew.get('in_new_moon_window_3d')} "
            f"full±3d={ew.get('in_full_moon_window_3d')}"
        )
    return "\n".join(lines)