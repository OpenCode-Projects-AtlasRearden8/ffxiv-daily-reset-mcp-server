"""Local-first task store and due/overdue status logic.

Everything lives in one JSON file (no network, no account, no telemetry).
Location defaults to a per-user data directory; point FFXIV_RESET_STORE at
any path to keep the file wherever you like (e.g. a notes vault).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import reset_windows as rw

CHARACTER_ID_RE = re.compile(r"^[A-Za-z][A-Za-z '\-\.]{0,31}$")
CATEGORY_RE = re.compile(r"^[a-z0-9_\-]{1,40}$")
CATEGORIES_HINT = [
    "gc_turnin",
    "duty_support",
    "beast_tribe",
    "item_turnin",
    "custom",
]
DEFAULT_CATEGORY_CADENCE = {
    "gc_turnin": "gc",
    "duty_support": "daily",
    "beast_tribe": "daily",
    "item_turnin": "gc",
    "custom": "daily",
}

STORE_VERSION = 1


def default_store_path() -> Path:
    env = os.environ.get("FFXIV_RESET_STORE")
    if env:
        return Path(env).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "ffxiv-reset-mcp" / "tasks.json"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class StoreError(ValueError):
    pass


class Store:
    def __init__(self, path: os.PathLike[str] | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_store_path()

    # ------------------------------------------------------------------ load
    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": STORE_VERSION, "characters": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StoreError(f"store file {self.path} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("characters"), dict):
            raise StoreError(f"store file {self.path} has an unexpected shape")
        return data

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data["version"] = STORE_VERSION
        data.setdefault("characters", {})
        # Atomic-ish write: never leave a corrupt task file behind.
        tmp_fd, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=".tasks-", suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
                fh.write("\n")
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    # -------------------------------------------------------------- validate
    @staticmethod
    def _norm_character(name: str) -> str:
        name = (name or "").strip()
        if not name:
            raise StoreError("character name is required")
        if not CHARACTER_ID_RE.match(name):
            raise StoreError(
                "character name must be 1-32 letters/spaces/-' "
                "(no FFXIV/Lodestone identifiers are stored or sent anywhere)"
            )
        return name

    @staticmethod
    def _norm_category(category: str) -> str:
        cat = (category or "").strip().lower()
        if not cat:
            raise StoreError("category is required")
        if not CATEGORY_RE.match(cat):
            raise StoreError("category must be a short lowercase slug")
        return cat

    @staticmethod
    def _norm_cadence(cadence: str, category: str) -> str:
        cad = (cadence or DEFAULT_CATEGORY_CADENCE.get(category, "daily")).strip().lower()
        if cad not in rw.CADENCES:
            raise StoreError(
                f"cadence must be one of {', '.join(rw.CADENCES)}; got {cad!r}"
            )
        return cad

    # ------------------------------------------------------------ characters
    def get_character(self, name: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        name = self._norm_character(name)
        data = data if data is not None else self.load()
        return data["characters"].setdefault(name, {"tasks": []})

    def list_characters(self, data: dict[str, Any] | None = None) -> list[str]:
        data = data if data is not None else self.load()
        return sorted(data["characters"].keys())

    def delete_character(self, name: str) -> bool:
        name = self._norm_character(name)
        data = self.load()
        existed = name in data["characters"]
        if existed:
            del data["characters"][name]
            self.save(data)
        return existed

    # ----------------------------------------------------------------- tasks
    def add_task(
        self,
        character: str,
        title: str,
        category: str = "custom",
        cadence: str | None = None,
        period_hours: int = 24,
        notes: str = "",
        allowance: int = 0,
    ) -> dict[str, Any]:
        character = self._norm_character(character)
        title = (title or "").strip()
        if not title:
            raise StoreError("task title is required")
        if len(title) > 120:
            raise StoreError("task title is too long (max 120 characters)")
        category = self._norm_category(category)
        cadence = self._norm_cadence(cadence or "", category)
        try:
            period_hours = int(period_hours)
        except (TypeError, ValueError) as exc:
            raise StoreError("period_hours must be an integer") from exc
        if period_hours <= 0 or period_hours > 24 * 30:
            raise StoreError("period_hours must be between 1 and 720")
        try:
            allowance = int(allowance)
        except (TypeError, ValueError) as exc:
            raise StoreError("allowance must be an integer") from exc
        if allowance < 0 or allowance > 100:
            raise StoreError("allowance must be between 0 and 100")
        notes = (notes or "").strip()[:300]

        data = self.load()
        tasks = self.get_character(character, data)["tasks"]
        stamps = []
        for t in tasks:
            if t.get("title", "").strip().lower() == title.lower():
                stamps.append(t["id"])
        if stamps:
            raise StoreError(f"duplicate task title for this character (existing task {stamps[0]})")
        tasks.append(
            {
                "id": self._next_id(data),
                "title": title,
                "category": category,
                "cadence": cadence,
                "period_hours": period_hours if cadence == "rolling" else 24,
                "notes": notes,
                "allowance": allowance,
                "recurring": True,
                "created_at": _iso(now_utc()),
                "completed_at": None,
            }
        )
        self.save(data)
        return tasks[-1]

    def _next_id(self, data: dict[str, Any]) -> int:
        top = 0
        for char in data["characters"].values():
            for task in char.get("tasks", []):
                try:
                    top = max(top, int(task.get("id", 0)))
                except (TypeError, ValueError):
                    continue
        return top + 1

    def _find(self, data: dict[str, Any], task_id: int) -> tuple[str, dict[str, Any]]:
        for char_name, char in data["characters"].items():
            for task in char.get("tasks", []):
                if task.get("id") == task_id:
                    return char_name, task
        raise StoreError(f"no task with id {task_id}")

    def get_task(self, task_id: int) -> tuple[str, dict[str, Any]]:
        return self._find(self.load(), int(task_id))

    def complete_task(self, task_id: int, moment: datetime | None = None) -> dict[str, Any]:
        task_id = int(task_id)
        moment = moment or now_utc()
        data = self.load()
        _char, task = self._find(data, task_id)
        task["completed_at"] = _iso(moment)
        self.save(data)
        return task

    def uncomplete_task(self, task_id: int) -> dict[str, Any]:
        task_id = int(task_id)
        data = self.load()
        _char, task = self._find(data, task_id)
        task["completed_at"] = None
        self.save(data)
        return task

    def delete_task(self, task_id: int) -> dict[str, Any]:
        task_id = int(task_id)
        data = self.load()
        char_name, task = self._find(data, task_id)
        data["characters"][char_name]["tasks"].remove(task)
        self.save(data)
        return task

    def list_tasks(
        self,
        character: str | None = None,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        data = self.load()
        out: list[dict[str, Any]] = []
        wanted = self._norm_character(character) if character else None
        cat = self._norm_category(category) if category else None
        for char_name, char in data["characters"].items():
            if wanted is not None and char_name != wanted:
                continue
            for task in char.get("tasks", []):
                if cat is not None and task.get("category") != cat:
                    continue
                out.append({**task, "character": char_name})
        out.sort(key=lambda t: (t["character"], t["id"]))
        return out


# ------------------------------------------------------------------ statuses
def task_status(task: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Classify one task against its reset window.

    Statuses:
      done      - completed inside the current window
      due       - completed in the previous window (or new) but not yet this one
      overdue   - missed at least one full window since the last completion
      permanent - completed and not recurring
    """
    now = now or now_utc()
    cadence = task.get("cadence", "daily")
    period_hours = int(task.get("period_hours") or 24)
    if cadence not in rw.CADENCES:
        cadence = "daily"
    current_idx = rw.window_index(now, cadence, period_hours)

    raw = task.get("completed_at")
    info = {
        "id": task.get("id"),
        "character": task.get("character", ""),
        "title": task.get("title", ""),
        "category": task.get("category", "custom"),
        "cadence": cadence,
        "notes": task.get("notes", ""),
        "allowance": int(task.get("allowance") or 0),
        "current_window_start": _iso(rw.window_start(current_idx, cadence, period_hours)),
        "current_window_resets": _iso(rw.window_end(current_idx, cadence, period_hours)),
    }
    if raw is None:
        info["status"] = "due"
        info["detail"] = "not completed yet (allowance unused)"
        info["windows_behind"] = None
        return info
    try:
        done_dt = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        info["status"] = "due"
        info["detail"] = "unparseable completion timestamp, treating as not completed"
        info["windows_behind"] = None
        return info
    done_idx = rw.window_index(done_dt, cadence, period_hours)
    behind = current_idx - done_idx
    if behind <= 0:
        info["status"] = "done"
        info["detail"] = f"completed this window ({done_dt:%a %H:%M} UTC)"
        info["windows_behind"] = 0
        return info
    if behind == 1:
        info["status"] = "due"
        info["detail"] = f"last completed {done_dt:%a %d %b %H:%M} UTC"
        info["windows_behind"] = 1
        return info
    info["status"] = "overdue"
    info["detail"] = f"last completed {done_dt:%a %d %b %H:%M} UTC "
    info["detail"] += f"({behind} windows ago)"
    info["windows_behind"] = behind
    return info


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def build_summary(
    tasks: list[dict[str, Any]],
    now: datetime | None = None,
    character: str | None = None,
) -> str:
    now = now or now_utc()
    header = ["FFXIV reset report", f"as of {_iso(now)} ({now:%a %d %b %Y %H:%M} UTC)", ""]
    header.append(rw.describe_next_resets(now))
    if not tasks:
        header.append("")
        header.append("No chores tracked yet — add some with add_task.")
        return "\n".join(header)

    header.append("")
    statuses = [dict(task_status(t, now), character=t.get("character", "")) for t in tasks]
    by_char: dict[str, list[dict[str, Any]]] = {}
    for st in statuses:
        by_char.setdefault(st["character"], []).append(st)

    lines = header
    total_due = sum(1 for s in statuses if s["status"] in {"due", "overdue"})
    total_overdue = sum(1 for s in statuses if s["status"] == "overdue")
    lines.append(
        f"{_plural(total_due, 'chore')} outstanding "
        f"({_plural(total_overdue, 'overdue')}); {len(statuses)} tracked."
    )

    for char_name in sorted(by_char):
        lines.append("")
        if character:
            lines.append(f"== {char_name} ==")
        else:
            lines.append(f"== {char_name}")
        rows = by_char[char_name]
        for symbol, label in (("**", "overdue"), ("*", "due")):
            picked = [s for s in rows if s["status"] == label]
            if not picked:
                continue
            lines.append(f"{symbol} {label.upper()} {symbol}")
            for s in picked:
                line = f"  - {s['title']}"
                if s["allowance"]:
                    line += f"  ({s['allowance']} allowance)"
                if s["notes"]:
                    line += f"  [{s['notes']}]"
                lines.append(line)
                lines.append(f"      {s['detail']}; window ends "
                             f"{s['current_window_resets']}")
        finished = [s for s in rows if s["status"] == "done"]
        if finished:
            lines.append("  done this window:")
            for s in finished:
                lines.append(f"  + {s['title']} — {s['detail']}")
    lines.append("")
    lines.append("Times are UTC; FFXIV windows reset at 15:00 UTC daily, "
                 "20:00 UTC for GC turn-ins, Tuesday 08:00 UTC weekly.")
    return "\n".join(lines)


def next_reset_line(now: datetime | None = None) -> str:
    return rw.describe_next_resets(now or now_utc())
