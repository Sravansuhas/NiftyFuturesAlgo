import datetime
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MARKET_EVENTS_PATH = PROJECT_ROOT / "data" / "market_events.json"
DEFAULT_PRE_EVENT_BLOCK_HOURS = 4.0
HIGH_IMPACT_LOOKAHEAD_DAYS = 90

_market_events_cache: Optional[Dict[str, Any]] = None

# Official NSE F&O trading holidays for calendar year 2026.
# Source: NSE circular NSE/FAOP/71777 dated December 12, 2025.
MARKET_HOLIDAYS_2026 = {
    datetime.date(2026, 1, 15),   # Maharashtra Municipal Corporation Elections (adhoc)
    datetime.date(2026, 1, 26),   # Republic Day
    datetime.date(2026, 3, 3),    # Holi
    datetime.date(2026, 3, 26),   # Shri Ram Navami
    datetime.date(2026, 3, 31),   # Shri Mahavir Jayanti
    datetime.date(2026, 4, 3),    # Good Friday
    datetime.date(2026, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
    datetime.date(2026, 5, 1),    # Maharashtra Day
    datetime.date(2026, 5, 28),   # Bakri Id
    datetime.date(2026, 6, 26),   # Muharram
    datetime.date(2026, 9, 14),   # Ganesh Chaturthi
    datetime.date(2026, 10, 2),   # Mahatma Gandhi Jayanti
    datetime.date(2026, 10, 20),  # Dussehra
    datetime.date(2026, 11, 10),  # Diwali-Balipratipada
    datetime.date(2026, 11, 24),  # Prakash Gurpurb Sri Guru Nanak Dev
    datetime.date(2026, 12, 25),  # Christmas
}

MUHURAT_TRADING_2026 = {
    # Timings are not final until NSE publishes the separate Muhurat circular.
    datetime.date(2026, 11, 8),
}

MARKET_HOLIDAYS = MARKET_HOLIDAYS_2026

# NSE F&O close extended to 15:40 IST from 2026-08-03 (Closing Auction Session / CAS alignment).
# Source: NSE circular communicated June 2026; effective first trading session on or after 3 Aug 2026.
NSE_FO_EXTENDED_SESSION_EFFECTIVE_DATE = datetime.date(2026, 8, 3)
_LEGACY_MARKET_CLOSE = datetime.time(15, 30)
_EXTENDED_MARKET_CLOSE = datetime.time(15, 40)
_SAFE_WINDOW_PRE_CLOSE_MINUTES = 15
_ENTRY_WINDOW_PRE_CLOSE_MINUTES = 30
# EOD MIS flatten: proactive square-off before broker auto-cutoff (~15:15 legacy regime).
# Post-extension brokers may shift MIS cutoff; we end flatten at safe-window close (15:25).
_LEGACY_EOD_FLATTEN_START = datetime.time(15, 10)
_LEGACY_EOD_FLATTEN_END = datetime.time(15, 15)
_EXTENDED_EOD_FLATTEN_START = datetime.time(15, 20)
_EXTENDED_EOD_FLATTEN_END = datetime.time(15, 25)


def uses_extended_nse_fo_session(for_date: Optional[datetime.date] = None) -> bool:
    """True on/after NSE_FO_EXTENDED_SESSION_EFFECTIVE_DATE."""
    day = for_date or now_ist().date()
    return day >= NSE_FO_EXTENDED_SESSION_EFFECTIVE_DATE


def get_nse_fo_market_close(for_date: Optional[datetime.date] = None) -> datetime.time:
    """Regular NSE F&O session close time for the given calendar date."""
    if uses_extended_nse_fo_session(for_date):
        return _EXTENDED_MARKET_CLOSE
    return _LEGACY_MARKET_CLOSE


def _time_before_close(
    for_date: datetime.date,
    *,
    minutes: int,
) -> datetime.time:
    close_dt = datetime.datetime.combine(for_date, get_nse_fo_market_close(for_date))
    return (close_dt - datetime.timedelta(minutes=minutes)).time()


def get_safe_trading_window_end(for_date: Optional[datetime.date] = None) -> datetime.time:
    """Conservative safe-window end: 15 min before official session close."""
    day = for_date or now_ist().date()
    return _time_before_close(day, minutes=_SAFE_WINDOW_PRE_CLOSE_MINUTES)


def get_entry_window_end(for_date: Optional[datetime.date] = None) -> datetime.time:
    """Last allowed new-entry time: 30 min before official session close."""
    day = for_date or now_ist().date()
    return _time_before_close(day, minutes=_ENTRY_WINDOW_PRE_CLOSE_MINUTES)


def get_eod_flatten_defaults(for_date: Optional[datetime.date] = None) -> tuple:
    """
    Default EOD MIS flatten window (start, end) for the active session regime.

    Legacy (close 15:30): 15:10–15:15 — ahead of typical broker MIS auto square-off.
    Extended (close 15:40): 15:20–15:25 — shifted with CAS; still ends at safe-window
    close so flatten completes before closing-auction / gamma noise (VWAP 15:10–15:40).
    Override anytime via EOD_FLATTEN_START / EOD_FLATTEN_END env vars.
    """
    if uses_extended_nse_fo_session(for_date):
        return _EXTENDED_EOD_FLATTEN_START, _EXTENDED_EOD_FLATTEN_END
    return _LEGACY_EOD_FLATTEN_START, _LEGACY_EOD_FLATTEN_END


def _normalize_underlying(underlying: str = "NIFTY") -> str:
    key = (underlying or "NIFTY").upper()
    if "BANKNIFTY" in key or "BNF" in key:
        return "BANKNIFTY"
    if "SENSEX" in key:
        return "SENSEX"
    return "NIFTY"


def _monthly_expiry_weekday(underlying: str = "NIFTY") -> int:
    """0=Mon … 3=Thu. NIFTY/BANKNIFTY monthly = last Tuesday; SENSEX = last Thursday."""
    return 3 if _normalize_underlying(underlying) == "SENSEX" else 1


def _last_weekday_of_month(year: int, month: int, weekday: int) -> datetime.date:
    probe = datetime.date(year, month, 28) + datetime.timedelta(days=4)
    last_day = probe - datetime.timedelta(days=probe.day)
    return last_day - datetime.timedelta(days=(last_day.weekday() - weekday) % 7)


def _shift_expiry_for_holiday(expiry: datetime.date) -> datetime.date:
    """If scheduled expiry falls on an F&O holiday, shift to previous trading day."""
    shifted = expiry
    while shifted in MARKET_HOLIDAYS or shifted.weekday() >= 5:
        shifted -= datetime.timedelta(days=1)
    return shifted


def get_monthly_expiry_for_month(year: int, month: int, underlying: str = "NIFTY") -> datetime.date:
    """Return the monthly futures/options expiry for the given index and month."""
    weekday = _monthly_expiry_weekday(underlying)
    raw = _last_weekday_of_month(year, month, weekday)
    return _shift_expiry_for_holiday(raw)


def get_nifty_expiry_for_month(year: int, month: int) -> datetime.date:
    """Backward-compatible alias — NIFTY monthly expiry (last Tuesday)."""
    return get_monthly_expiry_for_month(year, month, "NIFTY")


def _weekly_expiry_weekday(underlying: str = "NIFTY") -> Optional[int]:
    """
    Weekly derivatives expiry weekday (0=Mon … 3=Thu).
    NIFTY: Tuesday. SENSEX: Thursday. BANKNIFTY weekly discontinued (None).
    """
    key = _normalize_underlying(underlying)
    if key == "NIFTY":
        return 1
    if key == "SENSEX":
        return 3
    return None


def is_weekly_expiry_day(d: datetime.date = None, underlying: str = "NIFTY") -> bool:
    """True on weekly F&O expiry session for NIFTY (Tue) or SENSEX (Thu)."""
    day = d or now_ist().date()
    weekday = _weekly_expiry_weekday(underlying)
    if weekday is None:
        return False
    if day.weekday() >= 5 or day in MARKET_HOLIDAYS:
        return False
    return day.weekday() == weekday


def is_monthly_expiry_day(d: datetime.date = None, underlying: str = "NIFTY") -> bool:
    """Monthly front-contract expiry (holiday-shifted)."""
    day = d or now_ist().date()
    expected = get_monthly_expiry_for_month(day.year, day.month, underlying)
    return day == expected


def is_expiry_day(
    d: datetime.date = None,
    underlying: str = "NIFTY",
    *,
    include_weekly: bool = True,
) -> bool:
    """
    Expiry session for the given index.
    Monthly: NIFTY/BANKNIFTY last Tuesday; SENSEX last Thursday (holiday-shifted).
    Weekly (optional): NIFTY Tuesday, SENSEX Thursday; BANKNIFTY has no weekly.
    """
    day = d or now_ist().date()
    if is_monthly_expiry_day(day, underlying):
        return True
    if include_weekly and is_weekly_expiry_day(day, underlying):
        return True
    return False


def now_ist() -> datetime.datetime:
    """
    Returns current IST time.

    DEV CLOSED-MARKET TESTING SUPPORT:
    When DEV_FIXED_SIM_TIME is set (with DEV_FORCE_MARKET_OPEN or DEV_MODE) and
    FORCE_DRY_RUN is active (paper only), returns the fixed synthetic time.
    Never honored in LIVE_MODE — real wall clock is always used for live capital.
    """
    import os
    fixed = os.getenv("DEV_FIXED_SIM_TIME")
    dev_active = (
        os.getenv("DEV_FORCE_MARKET_OPEN", "false").strip().lower() in {"1", "true", "yes", "on"}
        or os.getenv("DEV_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}
        or os.getenv("DEV_SESSION_ACTIVE") == "true"
    )
    force_dry = os.getenv("FORCE_DRY_RUN", "true").strip().lower() not in {"0", "false", "no"}

    if fixed and dev_active and force_dry:
        try:
            from app.state_machine import state_machine, SystemState
            if state_machine.get_state() == SystemState.LIVE_MODE:
                logger.warning("[DEV] DEV_FIXED_SIM_TIME ignored — LIVE_MODE is active.")
            else:
                fixed = fixed.replace("T", " ").strip()
                dt = datetime.datetime.strptime(fixed, "%Y-%m-%d %H:%M:%S")
                return dt.replace(tzinfo=IST)
        except Exception:
            pass

    return datetime.datetime.now(tz=IST)


def is_real_market_open(at: datetime.datetime = None) -> bool:
    """
    Return True when the NSE F&O regular trading session is actually open.
    Never bypassed by DEV_FORCE_MARKET_OPEN — use for price-feed decisions.
    """
    current = at or now_ist()
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    current = current.astimezone(IST)

    today = current.date()
    current_time = current.time()

    if today in MUHURAT_TRADING_2026:
        return False

    if today.weekday() >= 5:
        return False

    if today in MARKET_HOLIDAYS:
        return False

    market_open = datetime.time(9, 15)
    market_close = get_nse_fo_market_close(today)
    return market_open <= current_time <= market_close


def is_market_open(at: datetime.datetime = None) -> bool:
    """
    Return True when the NSE F&O regular trading session is open.

    Rules:
    - Monday to Friday
    - 09:15 AM - 03:30 PM IST (03:40 PM from 2026-08-03)
    - Official NSE F&O holidays excluded
    - Muhurat trading date is known, but time must be enabled only after NSE
      publishes the exact circular.

    DEV TESTING SUPPORT:
    - Set DEV_FORCE_MARKET_OPEN=true (or 1) to bypass all calendar checks.
      This ONLY works when FORCE_DRY_RUN is also active (paper mode).
      It will NEVER allow trading in true LIVE_MODE.
      Perfect for developing/testing entry logic, ATR rolling, regime detection,
      risk gates, etc. when the real market is closed.
    """
    # --- Developer override for closed-market testing (safe) ---
    import os
    dev_force = os.getenv("DEV_FORCE_MARKET_OPEN", "false").strip().lower() in {"1", "true", "yes", "on"}
    force_dry = os.getenv("FORCE_DRY_RUN", "true").strip().lower() not in {"0", "false", "no"}

    if dev_force:
        # Hard safety: never allow in real live capital mode
        try:
            from app.state_machine import state_machine, SystemState
            if state_machine.get_state() == SystemState.LIVE_MODE:
                logger.warning("[DEV] DEV_FORCE_MARKET_OPEN ignored — LIVE_MODE is active. Real calendar used.")
            elif force_dry:
                return True  # Safe: only in paper + explicit dev flag
        except Exception:
            pass
    # --- end dev override ---

    return is_real_market_open(at)


def is_entry_window_open(at: datetime.datetime = None) -> bool:
    current = at or now_ist()
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    current = current.astimezone(IST)

    if not is_market_open(current):
        return False

    # When dev-forcing market open, also relax the strict entry window for testing
    import os
    dev_force = os.getenv("DEV_FORCE_MARKET_OPEN", "false").strip().lower() in {"1", "true", "yes", "on"}
    force_dry = os.getenv("FORCE_DRY_RUN", "true").strip().lower() not in {"0", "false", "no"}
    if dev_force and force_dry:
        return True

    first_entry = datetime.time(9, 30)
    last_entry = get_entry_window_end(current.date())
    return first_entry <= current.time() <= last_entry


def is_safe_trading_window(at: datetime.datetime = None) -> bool:
    """
    Conservative trading window that avoids the worst noise periods.
    Trader reality: First 30 minutes after 9:15 and last ~15 minutes before session
    close (15:30 legacy; 15:40 from 2026-08-03) are full of fake breakouts, auction
    effects, and gamma hedging noise on Nifty.
    """
    current = at or now_ist()
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    current = current.astimezone(IST)

    if not is_market_open(current):
        return False

    # Dev override: relax safe window too when explicitly testing
    import os
    dev_force = os.getenv("DEV_FORCE_MARKET_OPEN", "false").strip().lower() in {"1", "true", "yes", "on"}
    force_dry = os.getenv("FORCE_DRY_RUN", "true").strip().lower() not in {"0", "false", "no"}
    if dev_force and force_dry:
        return True

    safe_open = datetime.time(9, 45)
    safe_close = get_safe_trading_window_end(current.date())
    return safe_open <= current.time() <= safe_close


def _parse_ist_time_env(env_key: str, default_h: int, default_m: int) -> datetime.time:
    import os

    raw = os.getenv(env_key, "").strip()
    if not raw:
        return datetime.time(default_h, default_m)
    try:
        parts = raw.replace(".", ":").split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        return datetime.time(hour, minute)
    except Exception:
        return datetime.time(default_h, default_m)


def is_eod_flatten_window(at: datetime.datetime = None) -> bool:
    """
    Window to proactively flatten MIS before broker auto square-off (~15:15 IST legacy).
    Default: 15:10–15:15 IST before 2026-08-03; 15:20–15:25 IST on/after that date
    (override via EOD_FLATTEN_START / EOD_FLATTEN_END).
    """
    import os

    if os.getenv("EOD_MIS_FLATTEN", "true").strip().lower() in {"0", "false", "no", "off"}:
        return False

    current = at or now_ist()
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    current = current.astimezone(IST)

    if not is_real_market_open(current):
        return False

    default_start, default_end = get_eod_flatten_defaults(current.date())
    start = _parse_ist_time_env(
        "EOD_FLATTEN_START",
        default_start.hour,
        default_start.minute,
    )
    end = _parse_ist_time_env(
        "EOD_FLATTEN_END",
        default_end.hour,
        default_end.minute,
    )
    return start <= current.time() <= end


# Extend holidays for 2027 (placeholder — update when NSE publishes)
MARKET_HOLIDAYS_2027 = {
    datetime.date(2027, 1, 26),
    datetime.date(2027, 3, 8),   # approx Holi etc. (update from official circular)
}
MARKET_HOLIDAYS = MARKET_HOLIDAYS_2026 | MARKET_HOLIDAYS_2027  # union for multi-year support

HOLIDAYS_META_FILE = PROJECT_ROOT / "data" / "market_holidays.json"
_holiday_name_map: Dict[datetime.date, str] = {}


def reload_holidays_from_disk() -> int:
    """Merge synced NSE holidays from data/market_holidays.json into MARKET_HOLIDAYS."""
    global MARKET_HOLIDAYS, _holiday_name_map
    if not HOLIDAYS_META_FILE.exists():
        return 0
    try:
        with HOLIDAYS_META_FILE.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        added = 0
        for row in payload.get("holidays") or []:
            segment = str(
                row.get("segment") or row.get("exchange_segment") or row.get("type") or "FO"
            ).upper()
            if segment in {"SETTLEMENT", "CLEARING", "BANK", "BANKING"}:
                continue
            markets = row.get("markets") or row.get("exchanges")
            if isinstance(markets, list):
                normalized = {str(m).upper() for m in markets}
                if normalized and not normalized.intersection({"NSE", "FO", "F&O", "DERIVATIVES"}):
                    continue
            raw = str(row.get("date", ""))
            try:
                d = datetime.date.fromisoformat(raw)
            except ValueError:
                continue
            if d not in MARKET_HOLIDAYS:
                added += 1
            MARKET_HOLIDAYS = MARKET_HOLIDAYS | {d}
            _holiday_name_map[d] = str(row.get("description") or "Holiday")
        return added
    except Exception as exc:
        logger.warning("reload_holidays_from_disk failed: %s", exc)
        return 0


def is_trading_holiday(d: Optional[datetime.date] = None) -> bool:
    day = d or now_ist().date()
    return day in MARKET_HOLIDAYS


def holiday_name(d: Optional[datetime.date] = None) -> Optional[str]:
    day = d or now_ist().date()
    if day in _holiday_name_map:
        return _holiday_name_map[day]
    if day in MARKET_HOLIDAYS_2026 or day in MARKET_HOLIDAYS_2027:
        return "NSE F&O holiday"
    return None


def next_trading_day(d: Optional[datetime.date] = None, *, max_lookahead: int = 14) -> datetime.date:
    """Next weekday that is not an F&O holiday."""
    probe = (d or now_ist().date()) + datetime.timedelta(days=1)
    for _ in range(max_lookahead):
        if probe.weekday() < 5 and probe not in MARKET_HOLIDAYS:
            return probe
        probe += datetime.timedelta(days=1)
    return probe


# Load synced holidays at import (non-fatal)
try:
    reload_holidays_from_disk()
except Exception:
    pass


def _normalize_at(at: Optional[datetime.datetime] = None) -> datetime.datetime:
    current = at or now_ist()
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    return current.astimezone(IST)


def load_market_events(
    path: Optional[Path] = None,
    *,
    reload: bool = False,
) -> Dict[str, Any]:
    """Load high-impact market events from JSON (cached by default)."""
    global _market_events_cache

    events_path = Path(path or DEFAULT_MARKET_EVENTS_PATH)
    if _market_events_cache is not None and not reload and path is None:
        return _market_events_cache

    if not events_path.exists():
        logger.warning("Market events file missing: %s — event calendar gates disabled", events_path)
        payload: Dict[str, Any] = {"metadata": {}, "events": []}
        if path is None:
            _market_events_cache = payload
        return payload

    with events_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if path is None:
        _market_events_cache = payload
    return payload


def _parse_event_datetime(raw: str) -> datetime.datetime:
    text = (raw or "").strip().replace(" ", "T")
    if len(text) == 10:
        text = f"{text}T00:00:00"
    parsed = datetime.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)


def list_high_impact_events(
    *,
    at: Optional[datetime.datetime] = None,
    lookahead_days: int = HIGH_IMPACT_LOOKAHEAD_DAYS,
    events_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Return upcoming high-impact events sorted by datetime (IST)."""
    current = _normalize_at(at)
    horizon = current + datetime.timedelta(days=max(lookahead_days, 1))
    upcoming: List[Dict[str, Any]] = []

    for event in load_market_events(events_path).get("events", []):
        if str(event.get("impact", "")).lower() != "high":
            continue
        event_dt = _parse_event_datetime(str(event.get("datetime", "")))
        if event_dt < current or event_dt > horizon:
            continue
        upcoming.append({**event, "event_datetime": event_dt})

    upcoming.sort(key=lambda item: item["event_datetime"])
    return upcoming


def get_next_high_impact_event(
    at: Optional[datetime.datetime] = None,
    *,
    events_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Next high-impact event after ``at``, or None if none scheduled in lookahead window."""
    events = list_high_impact_events(at=at, events_path=events_path)
    return events[0] if events else None


def get_hours_to_high_impact_event(
    at: Optional[datetime.datetime] = None,
    *,
    events_path: Optional[Path] = None,
) -> float:
    """
    Hours until the nearest upcoming high-impact event (IST).

    Returns ``float('inf')`` when no high-impact event is scheduled within the
    lookahead window — FO rules treat that as outside the pre-event block zone.
    """
    current = _normalize_at(at)
    min_hours = float("inf")

    for event in list_high_impact_events(at=current, events_path=events_path):
        event_dt = event["event_datetime"]
        delta_hours = (event_dt - current).total_seconds() / 3600.0
        if delta_hours < 0:
            continue
        min_hours = min(min_hours, delta_hours)

    return min_hours


def is_within_pre_event_block_window(
    at: Optional[datetime.datetime] = None,
    *,
    block_hours: float = DEFAULT_PRE_EVENT_BLOCK_HOURS,
    events_path: Optional[Path] = None,
) -> bool:
    """True when ``at`` is within ``block_hours`` before a high-impact event."""
    hours = get_hours_to_high_impact_event(at=at, events_path=events_path)
    return hours <= float(block_hours)


def get_event_calendar_status(at: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Summary for dashboards and FO rule context."""
    current = _normalize_at(at)
    hours = get_hours_to_high_impact_event(at=current)
    next_event = get_next_high_impact_event(at=current)
    block_hours = float(
        load_market_events().get("metadata", {}).get("default_block_hours", DEFAULT_PRE_EVENT_BLOCK_HOURS)
    )

    status: Dict[str, Any] = {
        "hours_to_high_impact_event": hours,
        "within_pre_event_block_window": is_within_pre_event_block_window(
            at=current,
            block_hours=block_hours,
        ),
        "default_block_hours": block_hours,
        "next_high_impact_event": None,
    }

    if next_event:
        event_dt = next_event["event_datetime"]
        status["next_high_impact_event"] = {
            "id": next_event.get("id"),
            "name": next_event.get("name"),
            "category": next_event.get("category"),
            "datetime": event_dt.isoformat(),
            "hours_away": round(hours, 2) if hours != float("inf") else None,
        }

    return status


def get_market_status() -> dict:
    """
    Rich status for the web GUI / terminals.
    Senior finance dev requirement: always know exactly where you are in the session.
    """
    import os
    now = now_ist()
    open_ = is_market_open(now)
    safe = is_safe_trading_window(now)
    today = now.date()
    expiry_monthly = any(is_monthly_expiry_day(today, u) for u in ("NIFTY", "BANKNIFTY", "SENSEX"))
    expiry_weekly = any(is_weekly_expiry_day(today, u) for u in ("NIFTY", "SENSEX"))
    expiry = expiry_monthly or expiry_weekly
    eod_flatten = is_eod_flatten_window(now)

    dev_force = os.getenv("DEV_FORCE_MARKET_OPEN", "false").strip().lower() in {"1", "true", "yes", "on"}
    force_dry = os.getenv("FORCE_DRY_RUN", "true").strip().lower() not in {"0", "false", "no"}

    event_status = get_event_calendar_status(now)
    hours_to_event = event_status.get("hours_to_high_impact_event", float("inf"))
    within_event_block = bool(event_status.get("within_pre_event_block_window", False))
    next_macro = event_status.get("next_high_impact_event")

    safe_end = get_safe_trading_window_end(today)
    close_time = get_nse_fo_market_close(today)
    if not open_:
        next_event = "Market open 09:15 IST"
    else:
        next_event = (
            f"Safe window closes {safe_end.strftime('%H:%M')} IST "
            f"(session close {close_time.strftime('%H:%M')})"
        )
    if expiry:
        next_event = "EXPIRY DAY — trade with extreme caution"
    if within_event_block and next_macro:
        next_event = (
            f"PRE-EVENT BLOCK — {next_macro.get('name')} in "
            f"{hours_to_event:.1f}h (no new entries)"
        )
    elif next_macro and hours_to_event != float("inf"):
        next_event = f"Next macro event: {next_macro.get('name')} in {hours_to_event:.1f}h"
    if dev_force and force_dry and not open_:
        next_event = "DEV MODE — Market hours forced open for testing (paper only)"

    is_holiday = is_trading_holiday(today)

    status = {
        "ist_time": now.strftime("%H:%M:%S IST"),
        "date": now.strftime("%Y-%m-%d"),
        "is_trading_holiday": is_holiday,
        "holiday_name": holiday_name(today),
        "next_trading_day": next_trading_day(today).isoformat() if is_holiday or not open_ else None,
        "is_market_open": open_,
        "is_safe_trading_window": safe,
        "is_expiry_day": expiry,
        "is_monthly_expiry_day": expiry_monthly,
        "is_weekly_expiry_day": expiry_weekly,
        "is_eod_flatten_window": eod_flatten,
        "session_close": close_time.strftime("%H:%M"),
        "safe_window_end": safe_end.strftime("%H:%M"),
        "uses_extended_nse_fo_session": uses_extended_nse_fo_session(today),
        "session_status": "OPEN" if open_ else "CLOSED",
        "trading_allowed": safe and not within_event_block and not eod_flatten,
        "next_event": next_event,
        "day_of_week": now.strftime("%A"),
        "hours_to_high_impact_event": hours_to_event,
        "within_pre_event_block_window": within_event_block,
        "next_high_impact_event": next_macro,
    }

    if dev_force and force_dry:
        status["dev_mode"] = "DEV_FORCE_MARKET_OPEN active — calendar bypassed for testing (paper only)"
        status["session_status"] = "DEV-OPEN (testing)"

    fixed_sim = os.getenv("DEV_FIXED_SIM_TIME")
    if fixed_sim and force_dry and (dev_force or os.getenv("DEV_MODE") or os.getenv("DEV_SESSION_ACTIVE")):
        status["dev_fixed_sim_time"] = fixed_sim
        status["ist_time"] = f"{fixed_sim} (FIXED for testing)"
        status["note"] = "Using synthetic fixed time via DEV_FIXED_SIM_TIME (reproducible testing)"

    return status