"""Tests for FFXIV reset-window math.

All moments are constructed in UTC so the math is timezone-independent.
Reference points:
  2026-11-05 12:30 UTC  -> day before daily reset, inside Wednesday's window
  2026-11-07 14:59 UTC  -> still Thursday's daily reset window
  2026-11-07 15:00 UTC  -> daily reset rolls
  2026-11-03 (Tue) 08:00 UTC -> a weekly reset boundary
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ffxiv_reset_mcp import reset_windows as rw


def utc(*args, **kwargs):
    return datetime(*args, **kwargs, tzinfo=timezone.utc)


# ------------------------------------------------------------------ daily reset
def test_daily_window_index_boundary():
    before = utc(2026, 11, 7, 14, 59, 59)
    after = utc(2026, 11, 7, 15, 0, 0)
    assert rw.daily_window_index(before, 15) + 1 == rw.daily_window_index(after, 15)
    # Same instant boundaries: 15:00:00 belongs to the new window.
    assert rw.daily_window_index(utc(2026, 11, 7, 15, 0), 15) == rw.daily_window_index(
        utc(2026, 11, 7, 15, 0, 1), 15
    )
    assert rw.daily_window_index(utc(2026, 11, 7, 14, 59), 15) == (
        rw.daily_window_index(utc(2026, 11, 7, 15, 0), 15) - 1
    )


def test_daily_window_start_is_at_reset_time():
    idx = rw.daily_window_index(utc(2026, 11, 7, 16, 5), 15)
    start = rw.window_start(idx, "daily")
    assert start == utc(2026, 11, 7, 15, 0)
    idx_gc = rw.daily_window_index(utc(2026, 11, 7, 16, 5), 20)
    start_gc = rw.window_start(idx_gc, "gc")
    assert start_gc == utc(2026, 11, 7, 20, 0) or start_gc == utc(2026, 11, 6, 20, 0)


def test_daily_window_start_rolls_consecutively():
    idx = rw.daily_window_index(utc(2026, 11, 7, 16, 5), 15)
    assert rw.window_start(idx + 1, "daily") - rw.window_start(idx, "daily") == timedelta(
        days=1
    )
    assert rw.window_start(idx, "daily").hour == 15


def test_next_daily_reset_just_before_and_after():
    now = utc(2026, 11, 7, 14, 59)
    nxt = rw.next_daily_reset(now, 15)
    assert nxt == utc(2026, 11, 7, 15, 0)
    assert rw.next_daily_reset(utc(2026, 11, 7, 15, 0), 15) == utc(2026, 11, 8, 15, 0)
    assert rw.next_daily_reset(utc(2026, 11, 7, 23, 0), 15) == utc(2026, 11, 8, 15, 0)


def test_daily_reset_respects_utc_not_local():
    # A window spans calendar dates: 15:00 Nov 6 -> 15:00 Nov 7 covers 06:00 on Nov 7.
    idx_night = rw.daily_window_index(utc(2026, 11, 7, 6, 0), 15)
    idx_day = rw.daily_window_index(utc(2026, 11, 7, 16, 0), 15)
    assert idx_night == idx_day - 1


# ------------------------------------------------------------------ weekly reset
def test_weekly_reset_is_tuesday_0800_utc():
    now = utc(2026, 11, 7, 12, 0)  # Saturday
    nxt = rw.next_weekly_reset(now)
    assert nxt.weekday() == 1  # Tuesday
    assert nxt.hour == 8 and nxt.minute == 0
    assert nxt == utc(2026, 11, 10, 8, 0)  # first Tuesday after Nov 7


def test_weekly_index_round_trip_and_boundaries():
    just_before = utc(2026, 11, 10, 7, 59)  # Tuesday 07:59
    boundary = utc(2026, 11, 10, 8, 0)
    one_week_later = boundary + timedelta(days=7)
    assert rw.weekly_window_index(boundary) == rw.weekly_window_index(one_week_later) - 1
    assert rw.weekly_window_index(just_before) == rw.weekly_window_index(boundary) - 1
    assert rw.window_start(rw.weekly_window_index(boundary), "weekly") == boundary


def test_weekly_window_contains_wednesday_through_sunday():
    anchor = utc(2026, 11, 3, 20, 0)  # Tuesday 20:00, inside week starting Nov 3 08:00
    idx = rw.weekly_window_index(anchor)
    assert rw.window_start(idx, "weekly") == utc(2026, 11, 3, 8, 0)
    assert rw.weekly_window_index(utc(2026, 11, 9, 23, 59)) == idx  # Monday pre-reset
    assert rw.weekly_window_index(utc(2026, 11, 10, 8, 1)) == idx + 1


# ------------------------------------------------------------------- windows API
def test_window_index_start_end_round_trip_all_cadences():
    now = utc(2026, 11, 7, 12, 0)
    for cadence, period_hours in [("daily", 24), ("gc", 24), ("weekly", 24), ("rolling", 48)]:
        idx = rw.window_index(now, cadence, period_hours)
        start = rw.window_start(idx, cadence, period_hours)
        end = rw.window_end(idx, cadence, period_hours)
        assert start <= now < end
        assert rw.window_index(start, cadence, period_hours) == idx
        assert rw.window_index(end - timedelta(minutes=1), cadence, period_hours) == idx
        assert rw.window_index(end, cadence, period_hours) == idx + 1


def test_rolling_window_uses_period():
    now = utc(2026, 11, 7, 5, 0)
    idx = rw.rolling_window_index(now, 12)
    start = rw.window_start(idx, "rolling", 12)
    end = rw.window_end(idx, "rolling", 12)
    assert end - start == timedelta(hours=12)
    assert start <= now < end
    with pytest.raises(ValueError):
        rw.rolling_window_index(now, 0)


def test_unknown_cadence_raises():
    with pytest.raises(ValueError):
        rw.window_index(utc(2026, 11, 7, 12, 0), "monthly")


def test_describe_next_resets_mentions_all_three():
    text = rw.describe_next_resets(utc(2026, 11, 7, 12, 0))
    for needle in ("daily reset", "grand company", "weekly reset", "UTC"):
        assert needle in text


def test_format_countdown():
    assert rw.format_countdown(timedelta(minutes=90)) == "1h 30m"
    assert rw.format_countdown(timedelta(days=2, hours=3)) == "2d 3h 0m"
    assert rw.format_countdown(timedelta(days=17, hours=1)) == "17d 1h 0m"
    assert rw.format_countdown(timedelta(minutes=5)) == "5m"
    assert rw.format_countdown(timedelta(seconds=-3)) == "0m"
