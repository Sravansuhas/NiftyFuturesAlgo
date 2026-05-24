import datetime

# ============================================================
# MARKET CALENDAR - Indian Stock Market (NSE)
# Update this file every year with official NSE holidays
# Source: https://www.nseindia.com/products-services/equity-market-timings-holidays
# ============================================================

# Market Holidays for 2026 (Update annually)
MARKET_HOLIDAYS_2026 = {
    datetime.date(2026, 1, 26),   # Republic Day
    datetime.date(2026, 3, 17),   # Holi
    datetime.date(2026, 4, 3),    # Mahavir Jayanti
    datetime.date(2026, 4, 10),   # Good Friday
    datetime.date(2026, 8, 15),   # Independence Day
    datetime.date(2026, 8, 28),   # Ganesh Chaturthi
    datetime.date(2026, 10, 2),   # Gandhi Jayanti
    datetime.date(2026, 10, 20),  # Dussehra
    datetime.date(2026, 11, 14),  # Guruparv
    datetime.date(2026, 12, 25),  # Christmas
}

# Muhurat Trading Dates (Diwali Special Session)
# Usually happens on Diwali Laxmi Pujan day in the evening (1 hour session)
MUHURAT_TRADING_2026 = {
    datetime.date(2026, 10, 21),  # Diwali Muhurat Trading
}

# Combine all holidays
MARKET_HOLIDAYS = MARKET_HOLIDAYS_2026


def is_market_open() -> bool:
    """
    Checks if Indian F&O market is currently open.
    
    Rules:
    - Monday to Friday
    - 09:15 AM – 03:30 PM IST
    - Not on official NSE holidays
    - Includes Muhurat Trading session (evening on Diwali)
    
    Returns:
        bool: True if market is open for trading
    """
    now = datetime.datetime.now()
    today = now.date()
    current_time = now.time()

    # 1. Weekend Check
    if today.weekday() >= 5:  # Saturday or Sunday
        return False

    # 2. Holiday Check
    if today in MARKET_HOLIDAYS:
        return False

    # 3. Muhurat Trading Check (Special 1-hour session on Diwali)
    if today in MUHURAT_TRADING_2026:
        muhurat_open = datetime.time(18, 0)   # 6:00 PM
        muhurat_close = datetime.time(19, 0)  # 7:00 PM
        return muhurat_open <= current_time <= muhurat_close

    # 4. Normal Market Hours Check
    market_open = datetime.time(9, 15)
    market_close = datetime.time(15, 30)

    return market_open <= current_time <= market_close