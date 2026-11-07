# STATUS.md — ffxiv-daily-reset-mcp-server

Last updated: 2026-11-07
Owner: Asdale Dynamics, LLC (MIT — see LICENSE)

## What was built

A dependency-free Python MCP server that tracks FFXIV daily/weekly reset
chores per character and returns readable due/overdue reports an AI agent can
paste into chat.

- **`src/ffxiv_reset_mcp/reset_windows.py`** — pure reset-window math, all
  UTC-based (the game's reference clock):
  - daily reset 15:00 UTC (beast tribes, duty support, roulettes)
  - grand company supply/provisioning 20:00 UTC daily
  - weekly reset Tuesday 08:00 UTC
  - generic `rolling` windows every N hours (retainer ventures, etc.)
  Window indices are exact integer math anchored to the Unix epoch; helper
  `describe_next_resets()` renders countdowns.
- **`src/ffxiv_reset_mcp/store.py`** — local JSON task store. One file
  (`FFXIV_RESET_STORE` env override; default `~/.local/share/ffxiv-reset-mcp/tasks.json`),
  written atomically (temp file + rename). Category presets with default
  windows: `gc_turnin`, `duty_support`, `beast_tribe`, `item_turnin`, `custom`.
  Task status per reset window: `done` / `due` / `overdue`
  (overdue = missed a full window since last completion). `build_summary()`
  renders the chat-readable report.
- **`src/ffxiv_reset_mcp/server.py`** — minimal MCP stdio server implementing
  newline-delimited JSON-RPC 2.0: `initialize`, `tools/list`, `tools/call`
  (plus `ping`, notifications). Eight tools: `list_resets`, `list_characters`,
  `list_tasks`, `add_task`, `complete_task`, `uncomplete_task`, `delete_task`,
  `summary`. Tool errors surface as `isError: true` results, not crashes.
- **`tests/`** — pytest suite: window-boundary math, weekly rollovers, rolling
  windows, store CRUD/validation/atomicity, summary text, and a full stdio
  round trip against the real server subprocess (including malformed-JSON
  handling).
- **README.md** — beginner-friendly install/run/config docs with MCP client
  example. **LICENSE** — MIT, Copyright (c) 2026 Asdale Dynamics, LLC.

## How to run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .            # tests: pip install -e ".[dev]"

ffxiv-reset-mcp             # or: python -m ffxiv_reset_mcp
```

Wire it into any MCP client over stdio (example JSON config in README).
Point `FFXIV_RESET_STORE` at any path to relocate the task file.
Everything is local — no network calls, no accounts, no secrets.

## Verification results (all passing)

- `python -m pytest tests -q` → **35 passed** in ~0.1 s
- `python -m py_compile src/ffxiv_reset_mcp/*.py` → clean
- `pip install -e .` in a fresh venv → installs; console script `ffxiv-reset-mcp` works
- Manual smoke test (scripted tool calls against a temp store):
  added 5 chores for 2 characters across all categories, completed some with
  backdated times, and got a correct summary — 1 overdue GC turn-in (2 windows
  ago), duty support marked done-this-window, never-done chores listed as DUE,
  rolling retainer-venture window ending 12:00 UTC, and correct countdowns to
  the next daily/GC/weekly resets.
- Stdio handshake verified end-to-end: `initialize` → server info + tool
  capabilities, `tools/list` → 8 tools with input schemas, `tools/call` →
  results and structured JSON errors.

## Known gaps / next steps

- **Time-zone display is UTC only.** The summary lists UTC times (no local
  conversion); fine for agent chat, but a `tz` argument for `summary` would be
  friendlier.
- **No edit/rename tool** — delete + re-add covers it for now.
- **No satchel/allowance ledger** — an `allowance` field (e.g. beast tribe 3)
  is displayed but not consumed per-quest; each quest is tracked as one task.
- **Single-file store, no locking** — safe for one client session at a time;
  concurrent writers could race (last write wins).
- **MCP feature scope** — implements tools only (no prompts/resources), which
  is all a chore tracker needs today.
