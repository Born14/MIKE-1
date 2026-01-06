"""
Date utilities for MIKE-1.

Handles expiration date calculations for options trading.
"""

from datetime import datetime, timedelta
from typing import List


def get_next_fridays(count: int = 4, from_date: datetime = None) -> List[str]:
    """
    Get the next N Fridays (standard options expiration dates).

    Args:
        count: Number of Fridays to return
        from_date: Starting date (defaults to today)

    Returns:
        List of YYYY-MM-DD strings for next N Fridays
    """
    if from_date is None:
        from_date = datetime.now()

    fridays = []
    current = from_date

    while len(fridays) < count:
        # Find next Friday
        days_until_friday = (4 - current.weekday()) % 7
        if days_until_friday == 0 and current.date() != from_date.date():
            # Already on a Friday and not the start date
            next_friday = current
        else:
            next_friday = current + timedelta(days=days_until_friday if days_until_friday > 0 else 7)

        fridays.append(next_friday.strftime("%Y-%m-%d"))
        current = next_friday + timedelta(days=1)

    return fridays


def calculate_dte(expiration: str, from_date: datetime = None) -> int:
    """
    Calculate days to expiration.

    Args:
        expiration: Expiration date string (YYYY-MM-DD)
        from_date: Reference date (defaults to today)

    Returns:
        Number of days until expiration
    """
    if from_date is None:
        from_date = datetime.now()

    exp_date = datetime.strptime(expiration, "%Y-%m-%d")
    delta = exp_date - from_date
    return delta.days


def filter_expirations_by_dte(
    expirations: List[str],
    min_dte: int,
    max_dte: int,
    from_date: datetime = None
) -> List[str]:
    """
    Filter expiration dates by DTE range.

    Args:
        expirations: List of expiration date strings
        min_dte: Minimum days to expiration (inclusive)
        max_dte: Maximum days to expiration (inclusive)
        from_date: Reference date (defaults to today)

    Returns:
        Filtered list of expiration dates within DTE range
    """
    filtered = []
    for exp in expirations:
        dte = calculate_dte(exp, from_date)
        if min_dte <= dte <= max_dte:
            filtered.append(exp)

    return filtered


def is_market_open(check_time: datetime = None) -> bool:
    """
    Check if US markets are open (simple weekday check).

    Args:
        check_time: Time to check (defaults to now)

    Returns:
        True if market is open (Mon-Fri, 9:30am-4pm ET)

    Note:
        This is a simplified check. Does not account for holidays.
    """
    if check_time is None:
        check_time = datetime.now()

    # Check if weekend
    if check_time.weekday() >= 5:  # Saturday=5, Sunday=6
        return False

    # TODO: Add market hours check (9:30am-4pm ET)
    # TODO: Add holiday calendar check

    return True
