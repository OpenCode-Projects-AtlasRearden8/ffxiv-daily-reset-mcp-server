"""FFXIV reset-window math.

All windows are defined in UTC (the game uses GMT/UTC as reference, immune to
DST).  Window indices are counted from a fixed anchor so that arithmetic about
"how many windows ago was this completed" is exact integer math, not
calendar guesswork.

Kinds
-----
daily   -> 15:00 UTC every day (beast tribe allowances, duty roulettes, etc.)
gc      -> 20:00 UTC every day (Grand Company supply & provisioning)
weekly  -> Tuesday 08:00 UTC (weekly lockouts, custom deliveries, etc.)
rolling -> every N hours (retainer ventures, treasure map allowance, ...)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

DAILY_RESET_HOUR_UTC = 15
GC_RESET_HOUR_UTC = 20
WEEKLY_RESET_WEEKDAY_UTC = 1  # Tuesday (Monday=0)
WEEKLY_RESET_HOUR_UTC = 8

SECONDS_PER_DAY = 86400
SECONDS_PER_WEEK = 7 * SECONDS_PER_DAY

CADENCES = ("daily", "gc", "weekly", "rolling")

# Unix epoch (1970-01-01 00:00 UTC) was a Thursday (weekday 3, Monday=0).
_EPOCH_WEEKDAY = 3


def _utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _ts(dt: datetime) -> float:
    return dt.timestamp()


def _daily_anchor(hour_utc: int) -> float:
    """Timestamp of the 00:00 UTC siding on day zero, shifted to `hour_utc`."""
    return hour_utc * 3600.0


def daily_window_index(moment: datetime, hour_utc: int) -> int:
    """Which daily window does `moment` fall in?

    Window n starts at hour:00 UTC on day n; consecutive windows are 24h apart.
    """
    return int((moment.timestamp() - hour_utc * 3600.0) // SECONDS_PER_DAY)


def daily_window_start(index: int, hour_utc: int) -> datetime:
    return _utc(index * SECONDS_PER_DAY + hour_utc * 3600.0)


def weekly_window_anchor() -> float:
    """Timestamp of the first Tuesday 08:00 UTC after the Unix epoch."""
    days_until_tuesday = (WEEKLY_RESET_WEEKDAY_UTC - _EPOCH_WEEKDAY) % 7
    return days_until_tuesday * SECONDS_PER_DAY + WEEKLY_RESET_HOUR_UTC * 3600.0


def weekly_window_index(moment: datetime) -> int:
    """Which weekly window does `moment` fall in?"""
    return int((moment.timestamp() - weekly_window_anchor()) // SECONDS_PER_WEEK)


def weekly_window_start(index: int) -> datetime:
    return _utc(index * SECONDS_PER_WEEK + weekly_window_anchor())


def rolling_window_index(moment: datetime, period_hours: int) -> int:
    if period_hours <= 0:
        raise ValueError("period_hours must be positive")
    return int(moment.timestamp() // (period_hours * 3600))


def window_index(moment: datetime, cadence: str, period_hours: int = 24) -> int:
    if cadence == "daily":
        return daily_window_index(moment, DAILY_RESET_HOUR_UTC)
    if cadence == "gc":
        return daily_window_index(moment, GC_RESET_HOUR_UTC)
    if cadence == "weekly":
        return weekly_window_index(moment)
    if cadence == "rolling":
        return rolling_window_index(moment, period_hours)
    raise ValueError(f"unknown cadence: {cadence!r}")


def window_start(index: int, cadence: str, period_hours: int = 24) -> datetime:
    """Start of window `index`, as a UTC datetime."""
    if cadence == "daily":
        return daily_window_start(index, DAILY_RESET_HOUR_UTC)
    if cadence == "gc":
        return daily_window_start(index, GC_RESET_HOUR_UTC)
    if cadence == "weekly":
        return weekly_window_start(index)
    if cadence == "rolling":
        if period_hours <= 0:
            raise ValueError("period_hours must be positive")
        return _utc(index * period_hours * 3600.0)
    raise ValueError(f"unknown cadence: {cadence!r}")


def window_end(index: int, cadence: str, period_hours: int = 24) -> datetime:
    """Inclusive start of the *next* window (i.e. end of this one)."""
    return window_start(index + 1, cadence, period_hours)


def next_daily_reset(moment: datetime, hour_utc: int) -> datetime:
    """Start of the next daily window strictly after `moment`."""
    idx = daily_window_index(moment, hour_utc)
    lower = daily_window_start(idx, hour_utc)
    if lower > moment:
        return lower
    return daily_window_start(idx + 1, hour_utc)


def next_weekly_reset(moment: datetime) -> datetime:
    idx = weekly_window_index(moment)
    lower = weekly_window_start(idx)
    if lower > moment:
        return lower
    return weekly_window_start(idx + 1)


def format_countdown(delta: timedelta) -> str:
    total_minutes = max(0, int(delta.total_seconds() // 60))
    days, rem = divmod(total_minutes, 1440)
    hours, minutes = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def describe_next_resets(now: datetime) -> str:
    daily = next_daily_reset(now, DAILY_RESET_HOUR_UTC)
    gc = next_daily_reset(now, GC_RESET_HOUR_UTC)
    weekly = next_weekly_reset(now)
    lines = [
        "Next resets (all UTC):",
        f"  daily reset (beast tribes, roulettes, duty support): "
        f"{daily:%a %d %b %Y %H:%M} UTC "
        f"(in {format_countdown(daily - now)})",
        f"  grand company turn-ins: {gc:%a %d %b %Y %H:%M} UTC "
        f"(in {format_countdown(gc - now)})",
        f"  weekly reset: {weekly:%a %d %b %Y %H:%M} UTC "
        f"(in {format_countdown(weekly - now)})",
    ]
    return "\n".join(lines)
