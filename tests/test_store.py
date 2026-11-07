"""Tests for the store: statuses, persistence, validation, summary text."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from ffxiv_reset_mcp import store as st
from ffxiv_reset_mcp import reset_windows as rw


def make_store(tmp_path):
    s = st.Store(tmp_path / "tasks.json")
    now = datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc)
    return s, now


def utc(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


def set_clock(monkeypatch, moment):
    real = st.now_utc

    def fake():
        return moment

    monkeypatch.setattr(st, "now_utc", fake)
    return real


# --------------------------------------------------------------------- CRUD
def test_add_and_complete_round_trip(tmp_path):
    s, now = make_store(tmp_path)
    task = s.add_task(
        character="Miqo Tia",
        title="Grand Company provisioning turn-in",
        category="gc_turnin",
    )
    assert task["cadence"] == "gc"
    assert task["completed_at"] is None
    tasks = s.list_tasks(character="Miqo Tia")
    assert [t["id"] for t in tasks] == [task["id"]]
    done = s.complete_task(task["id"], moment=utc(2026, 11, 7, 16, 30))
    assert done["completed_at"] == "2026-11-07T16:30:00Z"
    char, fetched = s.get_task(task["id"])
    assert fetched["completed_at"] == "2026-11-07T16:30:00Z"
    s.delete_task(task["id"])
    assert s.list_tasks() == []


def test_duplicate_title_rejected(tmp_path):
    s, _ = make_store(tmp_path)
    s.add_task("Rowena D", "Feed the retainer", category="custom")
    with pytest.raises(st.StoreError, match="duplicate"):
        s.add_task("Rowena D", "Feed the retainer")
    # Different characters may share a title.
    s.add_task("Rowena Z", "Feed the retainer")
    assert len(s.list_tasks()) == 2


def test_character_and_category_validation(tmp_path):
    s, _ = make_store(tmp_path)
    with pytest.raises(st.StoreError):
        s.add_task("", "x")
    with pytest.raises(st.StoreError):
        s.add_task("Miqo<script>", "x")
    with pytest.raises(st.StoreError):
        s.add_task("Miqo Tia", "x", category="Grand Company!")  # not a slug
    task = s.add_task("Miqo Tia", "x", category="ITEM_TURNIN")
    assert task["category"] == "item_turnin"
    with pytest.raises(st.StoreError):
        s.add_task("Miqo Tia", "y", cadence="monthly")
    with pytest.raises(st.StoreError):
        s.add_task("Miqo Tia", "y", period_hours=0)


def test_unknown_task_id(tmp_path):
    s, _ = make_store(tmp_path)
    with pytest.raises(st.StoreError, match="no task"):
        s.complete_task(999)


def test_store_is_atomic_json(tmp_path):
    s, _ = make_store(tmp_path)
    s.add_task("Lala Lin", "Beast tribe dailies", category="beast_tribe", allowance=3)
    task = s.add_task("Lala Lin", "Item turn-ins with Rowena", category="item_turnin")
    path = tmp_path / "tasks.json"
    data = json.loads(path.read_text())
    assert data["characters"]["Lala Lin"]["tasks"][0]["title"] == "Beast tribe dailies"
    # Deleting the task removes it from the file; no stray tmp files remain.
    s.delete_task(task["id"])
    leftovers = [p for p in path.parent.iterdir() if p.name.startswith(".tasks-")]
    assert leftovers == []


# ------------------------------------------------------------------ statuses
def test_status_due_same_window_never_done_and_after_reset(tmp_path):
    s, _ = make_store(tmp_path)
    # Daily task completed 2026-11-06 16:00 UTC (inside daily window Nov 6? no: 16:00 on Nov 6 is in window started Nov 6 15:00). Now Nov 8 12:00 -> Nov 7 window missed entirely? Completed window = Nov 6 15:00->Nov 7 15:00 = idx D0.
    task = s.add_task("Miqo Tia", "Duty roulettes", category="duty_support")
    # never done -> due
    status = st.task_status({**task, "character": "Miqo Tia"}, utc(2026, 11, 6, 16, 0))
    assert status["status"] == "due"
    # complete it inside window D0 (Nov 6 16:00) -> done at same window
    task["completed_at"] = "2026-11-06T16:00:00Z"
    status = st.task_status(task, utc(2026, 11, 6, 17, 0))
    assert status["status"] == "done"
    # one window later (Nov 7 15:30 is window D1; completed at D0) -> due
    status = st.task_status(task, utc(2026, 11, 7, 15, 30))
    assert status["status"] == "due"
    assert status["windows_behind"] == 1
    # still within D1 later that window -> due
    status = st.task_status(task, utc(2026, 11, 8, 14, 0))
    assert status["status"] == "due"
    # missed D1 as well: now in D2 (Nov 8 15:30) -> overdue
    status = st.task_status(task, utc(2026, 11, 8, 15, 30))
    assert status["status"] == "overdue"
    assert status["windows_behind"] == 2


def test_status_gc_turnin_uses_20_00_window(tmp_path):
    s, _ = make_store(tmp_path)
    task = s.add_task("Rowena D", "GC supply & provisioning", category="gc_turnin")
    # Completed at 21:00 Nov 6 (window Nov 6 20:00 -> Nov 7 20:00).
    task["completed_at"] = "2026-11-06T21:00:00Z"
    # 19:00 Nov 7 is 15:00-reset-window of a new day but *same* GC window.
    status = st.task_status(task, utc(2026, 11, 7, 19, 0))
    assert status["status"] == "done"
    # At 20:01 Nov 7 the GC window rolls over -> due.
    status = st.task_status(task, utc(2026, 11, 7, 20, 1))
    assert status["status"] == "due"


def test_status_weekly_task(tmp_path):
    s, _ = make_store(tmp_path)
    task = s.add_task("Miqo Tia", "Custom deliveries", category="custom", cadence="weekly")
    # Completed Tuesday Nov 3 09:00 (inside Nov 3 08:00 weekly window).
    task["completed_at"] = "2026-11-03T09:00:00Z"
    assert st.task_status(task, utc(2026, 11, 6, 12, 0))["status"] == "done"
    # Next weekly reset (Nov 10 08:00) -> overdue by 1 window? behind == 1 => due.
    status = st.task_status(task, utc(2026, 11, 10, 8, 30))
    assert status["status"] == "due"
    assert status["windows_behind"] == 1


def test_status_rolling_chore(tmp_path):
    s, _ = make_store(tmp_path)
    task = s.add_task("Miqo Tia", "Treasure map allowance", cadence="rolling", period_hours=18)
    task["completed_at"] = "2026-11-07T10:00:00Z"
    idx_done = rw.window_index(utc(2026, 11, 7, 10, 0), "rolling", 18)
    next_rollover = rw.window_start(idx_done + 1, "rolling", 18)
    assert st.task_status(task, next_rollover - timedelta(seconds=1))["status"] == "done"
    assert st.task_status(task, next_rollover + timedelta(hours=18))["status"] == "overdue"


def test_status_bad_timestamp_falls_back_to_due(tmp_path):
    s, _ = make_store(tmp_path)
    task = s.add_task("Miqo Tia", "Whatever")
    sneaky = {**task, "completed_at": "not-a-timestamp", "character": "Miqo Tia"}
    assert st.task_status(sneaky, utc(2026, 11, 7, 12, 0))["status"] == "due"


# ------------------------------------------------------------------ summary
def test_summary_groups_and_flags(tmp_path):
    s, _ = make_store(tmp_path)
    s.add_task("Miqo Tia", "Grand Company provisioning", category="gc_turnin")
    s.add_task("Miqo Tia", "Beast tribe allowances (3)", category="beast_tribe", allowance=3)
    other = s.add_task("Lala Lin", "Duty support queue", category="duty_support")
    s.complete_task(other["id"], moment=utc(2026, 11, 7, 14, 0))
    summary = st.build_summary(
        s.list_tasks(), utc(2026, 11, 7, 16, 0), character=None
    )
    assert "== Lala Lin" in summary and "== Miqo Tia" in summary
    assert "OVERDUE" not in summary
    assert "DUE **" in summary or "DUE *" in summary
    assert "(3 allowance)" in summary
    assert "15:00 UTC" in summary
    assert "Tuesday" in summary
    assert "chore" in summary  # count line


def test_summary_overdue_counting(tmp_path):
    s, _ = make_store(tmp_path)
    t = s.add_task("Miqo Tia", "Duty support queue", category="duty_support")
    # Completed Nov 5 10:00 (daily window started Nov 4 15:00). At Nov 7 16:00 we're 2 windows later.
    s.complete_task(t["id"], moment=utc(2026, 11, 5, 10, 0))
    s.add_task("Miqo Tia", "Never done chore", category="custom")
    summary = st.build_summary(s.list_tasks(), utc(2026, 11, 7, 16, 0))
    assert "windows ago" in summary
    assert "2 chores outstanding (1 overdue)" in summary


def test_summary_empty_store(tmp_path):
    s, _ = make_store(tmp_path)
    summary = st.build_summary([], utc(2026, 11, 7, 16, 0))
    assert "No chores tracked yet" in summary


def test_default_category_cadence_map():
    assert st.DEFAULT_CATEGORY_CADENCE["gc_turnin"] == "gc"
    assert st.DEFAULT_CATEGORY_CADENCE["beast_tribe"] == "daily"
