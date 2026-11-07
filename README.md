# ffxiv-daily-reset-mcp-server

A small, local-first **Model Context Protocol (MCP) server** that tracks your
FFXIV daily and weekly reset responsibilities — grand company turned-ins,
duty support queues, beast tribe allowances, item turn-ins, and any customized
per-character chores you care about — and returns readable **due / overdue
summaries** an AI agent can weave straight into chat.

Built by **Asdale Dynamics, LLC** as part of a beginner-friendly,
plain-English collection of AI tools and guides.

## Why this exists

FFXIV repeats a lot of content on fixed UTC windows (daily reset at
**15:00 UTC**, grand company turn-ins at **20:00 UTC**, weekly reset
**Tuesday 08:00 UTC**). Remembering *which* character has *which* chore — and
whether it's still "today's window" or already missed — is exactly the kind of
state a personal agent should hold for you. This server does that with:

- **All data local.** One JSON file on your machine. No accounts, no network,
  no telemetry, no secrets.
- **Plain summaries.** `summary` returns short, readable text you paste or pipe
  into a chat: what's due, what's overdue, when the next reset lands.
- **Dependency-free.** Pure Python standard library; nothing to install but the
  package itself (and pytest, if you want the tests).

## Install

```bash
# from this folder
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
# optional, for tests:
pip install -e ".[dev]"
```

Python 3.10+ is all it needs.

## Run it as an MCP server

```bash
ffxiv-reset-mcp                  # console script, or:
python -m ffxiv_reset_mcp
```

It speaks newline-delimited JSON-RPC 2.0 over stdin/stdout — the standard MCP
stdio transport — so wire it into any MCP client, e.g. Claude Desktop,
`claude mcp add`, or a config entry like:

```json
{
  "mcpServers": {
    "ffxiv-reset": {
      "command": "python",
      "args": ["-m", "ffxiv_reset_mcp"],
      "env": { "FFXIV_RESET_STORE": "~/.local/share/ffxiv-reset-mcp/tasks.json" }
    }
  }
}
```

## Tools

| Tool | What it does |
| --- | --- |
| `list_resets` | Next daily (15:00 UTC), GC (20:00 UTC) and weekly (Tue 08:00 UTC) resets with countdowns |
| `list_characters` | All characters that have tracked chores |
| `list_tasks` | Chores + per-task status (DONE / DUE / OVERDUE), filterable by character or category |
| `add_task` | Track a chore: required `character`, `title`; optional `category`, `cadence`, `period_hours`, `notes`, `allowance` |
| `complete_task` | Mark a chore completed (completion time = now) |
| `uncomplete_task` | Undo a completion |
| `delete_task` | Remove a chore |
| `summary` | **The one to call by default** — readable due/overdue report for chat |

### Categories & cadences

Categories (free-form lowercase slugs allowed; presets carry a default window):

- `gc_turnin` — grand company supply & provisioning, resets **20:00 UTC** daily
- `duty_support` — duty support queues / roulettes, resets **15:00 UTC** daily
- `beast_tribe` — tribal quest allowances, resets **15:00 UTC** daily
- `item_turnin` — item turn-ins and deliveries, defaults to the **20:00 UTC** daily window
- `custom` — anything you choose; defaults to the **15:00 UTC** daily window

Cadences: `daily` (15:00 UTC), `gc` (20:00 UTC), `weekly` (Tuesday 08:00 UTC),
or `rolling` (every `period_hours` hours — handy for retainer ventures or a
treasure map allowance). Every chore is recurring: complete it and it flips
back to DUE after the next relevant reset, or OVERDUE if a full window passes.

## Example agent flow

1. *"Add GC turn-ins for Miqo Tia, beast tribe dailies with 3 allowances, and
   my custom Rowena delivery for Lala Lin."*
2. *"What are my FFXIV chores?"* — agent calls `summary` and answers:

```
FFXIV reset report
as of 2026-11-07T18:30:00Z (Sat 07 Nov 2026 18:30 UTC)

Next resets (all UTC):
  daily reset ... Sat 08 Nov 2026 15:00 UTC (in 20h 30m)
  grand company turn-ins ... Sat 07 Nov 2026 20:00 UTC (in 1h 30m)
  weekly reset ... Tue 10 Nov 2026 08:00 UTC (in 2d 13h 30m)

2 chores outstanding (0 overdue); 3 tracked.
== Miqo Tia
* DUE *
  - Grand Company provisioning   (1 allowance/s)
      not completed yet (allowance unused); window ends 2026-11-07T20:00:00Z UTC
```

## Where the data lives

`FFXIV_RESET_STORE` env var, or the first match of:
`$XDG_DATA_HOME/ffxiv-reset-mcp/tasks.json`, otherwise
`~/.local/share/ffxiv-reset-mcp/tasks.json`.
Character names are the *only* personal input, and they never leave your disk.

## Tests

```bash
python -m pytest tests -v
```

Coverage includes the reset-window math (boundaries at 15:00/20:00 UTC,
Tuesday 08:00 weekly rollovers, rolling N-hour windows), store CRUD and
atomicity, status classification (done/due/overdue), summary text, and a full
stdio JSON-RPC round trip against the real server process.

## Related project

This is deliberately **not** [`ffxiv-weekly-lockout-tracker`](https://github.com/AsdaleDynamics)
(savage/raid lockout webapp). This one is the lightweight, agent-readable
*chore* server for dailies and turn-ins.

## License

MIT — Copyright (c) 2026 Asdale Dynamics, LLC. See [LICENSE](LICENSE).
