import datetime
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")

# Official NSE F&O trading holidays for calendar year 2026.
# Source: NSE circular NSE/FAOP/71777 dated December 12, 2025.
MARKET_HOLIDAYS_2026 = {
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


def now_ist() -> datetime.datetime:
    return datetime.datetime.now(tz=IST)


def is_market_open(at: datetime.datetime = None) -> bool:
    """
    Return True when the NSE F&O regular trading session is open.

    Rules:
    - Monday to Friday
    - 09:15 AM - 03:30 PM IST
    - Official NSE F&O holidays excluded
    - Muhurat trading date is known, but time must be enabled only after NSE
      publishes the exact circular.
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
    market_close = datetime.time(15, 30)
    return market_open <= current_time <= market_close


def is_entry_window_open(at: datetime.datetime = None) -> bool:
    current = at or now_ist()
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    current = current.astimezone(IST)

    if not is_market_open(current):
        return False

    first_entry = datetime.time(9, 30)
    last_entry = datetime.time(15, 0)
    return first_entry <= current.time() <= last_entry
