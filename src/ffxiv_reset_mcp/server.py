"""Minimal MCP (Model Context Protocol) stdio server.

Speaks newline-delimited JSON-RPC 2.0 over stdin/stdout, which is the MCP
stdio transport.  Implements just enough of the protocol for tool servers:
`initialize`, `tools/list`, `tools/call`.  Zero third-party dependencies —
everything is stdlib, all data stays in one local JSON file.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable

from . import reset_windows as rw
from . import store as st

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "ffxiv-reset-mcp"
SERVER_VERSION = "0.1.0"


def _text_result(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _error_result(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": True}


class ToolServer:
    """Tool registry + dispatch, isolated from transport for easy testing."""

    def __init__(self, store: st.Store | None = None) -> None:
        self.store = store or st.Store()
        self.tools: dict[str, tuple[dict[str, Any], Callable[[dict[str, Any]], str]]] = {}
        for definition, handler in TOOL_DEFS:
            self.tools[definition["name"]] = (definition, handler)

    # ------------------------------------------------------------------ calls
    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        entry = self.tools.get(name)
        if entry is None:
            return _error_result(f"unknown tool: {name}")
        _definition, handler = entry
        try:
            return _text_result(handler(arguments))
        except st.StoreError as exc:
            return _error_result(f"error: {exc}")
        except Exception as exc:  # noqa: BLE001 - surface anything to the agent
            return _error_result(f"internal error: {exc.__class__.__name__}: {exc}")

    def list_tools(self) -> list[dict[str, Any]]:
        return [definition for definition, _ in TOOL_DEFS]


# ---------------------------------------------------------------- handlers
def _handle_list_resets(args: dict[str, Any]) -> str:
    return rw.describe_next_resets(st.now_utc())


def _require(args: dict[str, Any], key: str) -> Any:
    if key not in args or args[key] is None:
        raise st.StoreError(f"missing required argument: {key}")
    return args[key]
    return args[key]


def _simplify(task: dict[str, Any], now: Any) -> str:
    status = st.task_status(task, now)
    flag = {"done": "DONE", "due": "DUE", "overdue": "OVERDUE"}.get(
        status["status"], status["status"].upper()
    )
    return (
        f"#{status['id']} [{flag}] {status['character']}: {status['title']} "
        f"({status['category']} / {status['cadence']})"
        + (f" — {status['detail']}" if status["detail"] else "")
    )


def _int_or_fail(args: dict[str, Any], key: str) -> int:
    value = args.get(key)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise st.StoreError(f"{key} must be an integer") from exc


def _h_list_tasks(args: dict[str, Any]) -> str:
    srv_store = tool_store
    tasks = srv_store.list_tasks(
        character=args.get("character"), category=args.get("category")
    )
    if not tasks:
        return "No tasks match. (Add one with add_task.)"
    now = st.now_utc()
    lines = []
    for task in tasks:
        lines.append(_simplify({**task}, now))
    return "\n".join(lines)


def _h_add_task(args: dict[str, Any]) -> str:
    character = str(_require(args, "character"))
    task = tool_store.add_task(
        character=character,
        title=str(_require(args, "title")),
        category=str(args.get("category") or "custom"),
        cadence=str(args.get("cadence") or ""),
        period_hours=int(args.get("period_hours") or 24),
        notes=str(args.get("notes") or ""),
        allowance=int(args.get("allowance") or 0),
    )
    return json.dumps({**task, "character": character}, indent=1)


def _h_complete_task(args: dict[str, Any]) -> str:
    task_id = _int_or_fail(args, "task_id")
    task = tool_store.complete_task(task_id)
    return f"Marked #{task_id} '{task['title']}' complete (at {task['completed_at']})."


def _h_uncomplete_task(args: dict[str, Any]) -> str:
    task_id = _int_or_fail(args, "task_id")
    task = tool_store.uncomplete_task(task_id)
    return f"Cleared completion on #{task_id} '{task['title']}'."


def _h_delete_task(args: dict[str, Any]) -> str:
    task_id = _int_or_fail(args, "task_id")
    task = tool_store.delete_task(task_id)
    return f"Deleted #{task_id} '{task['title']}' from {task['character']}."


def _h_list_characters(args: dict[str, Any]) -> str:
    names = tool_store.list_characters()
    return "Characters tracked: " + (", ".join(names) if names else "(none yet)")


def _h_summary(args: dict[str, Any]) -> str:
    tasks = tool_store.list_tasks(character=args.get("character") or None)
    return st.build_summary(tasks, st.now_utc(), character=args.get("character"))


# ------------------------------------------------------------------ schema
TOOL_DEFS: list[tuple[dict[str, Any], Callable[[dict[str, Any]], str]]] = []


def _register(definition: dict[str, Any], handler: Callable[[dict[str, Any]], str]) -> None:
    TOOL_DEFS.append((definition, handler))


tool_store = st.Store()  # set per-server-instance at runtime


def _make_server(store: st.Store | None = None) -> ToolServer:
    global tool_store
    tool_store = store or st.Store()
    return ToolServer(tool_store)


TOOL_DEFS = [
    (
        {
            "name": "list_resets",
            "description": (
                "Show the next FFXIV daily, grand-company, and weekly reset "
                "times in UTC with countdowns. No arguments."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        _handle_list_resets,
    ),
    (
        {
            "name": "list_characters",
            "description": "List all characters that have tracked chores.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        _h_list_characters,
    ),
    (
        {
            "name": "list_tasks",
            "description": (
                "List chores and their reset-window status. Optional filters: "
                "character, category (gc_turnin, duty_support, beast_tribe, "
                "item_turnin, custom)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "character": {"type": "string"},
                    "category": {"type": "string"},
                },
            },
        },
        _h_list_tasks,
    ),
    (
        {
            "name": "add_task",
            "description": (
                "Track a per-character chore. Required: character, title. "
                "Optional: category (default custom), cadence "
                "(daily|gc|weekly|rolling; gc = 20:00 UTC turn-in window), "
                "period_hours (rolling cadence only), notes, allowance "
                "(e.g. beast tribe allowance count)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "character": {"type": "string"},
                    "title": {"type": "string"},
                    "category": {"type": "string"},
                    "cadence": {"type": "string"},
                    "period_hours": {"type": "integer"},
                    "notes": {"type": "string"},
                    "allowance": {"type": "integer"},
                },
                "required": ["character", "title"],
            },
        },
        _h_add_task,
    ),
    (
        {
            "name": "complete_task",
            "description": "Mark a chore completed (completed_at = now). Use the numeric task id.",
            "inputSchema": {
                "type": "object",
                "properties": {"task_id": {"type": "integer"}},
                "required": ["task_id"],
            },
        },
        _h_complete_task,
    ),
    (
        {
            "name": "uncomplete_task",
            "description": "Undo a completion (clears completed_at).",
            "inputSchema": {
                "type": "object",
                "properties": {"task_id": {"type": "integer"}},
                "required": ["task_id"],
            },
        },
        _h_uncomplete_task,
    ),
    (
        {
            "name": "delete_task",
            "description": "Remove a tracked chore entirely.",
            "inputSchema": {
                "type": "object",
                "properties": {"task_id": {"type": "integer"}},
                "required": ["task_id"],
            },
        },
        _h_delete_task,
    ),
    (
        {
            "name": "summary",
            "description": (
                "Return a readable due/overdue report the agent can paste into "
                "chat. Optional character filter. Best default reply for "
                "'what are my FFXIV chores?'"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"character": {"type": "string"}},
            },
        },
        _h_summary,
    ),
]


# ------------------------------------------------------------------ transport
class StdioTransport:
    """Newline-delimited JSON-RPC over stdin/stdout."""

    def serve(self, server: ToolServer) -> None:
        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                self._send({"jsonrpc": "2.0", "id": None,
                            "error": {"code": -32700, "message": "Parse error"}})
                continue
            response = self._handle(request, server)
            if response is not None:
                self._send(response)

    @staticmethod
    def _send(payload: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()

    def _handle(self, request: dict[str, Any], server: ToolServer) -> dict[str, Any] | None:
        req_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params") or {}
        notification = req_id is None

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            }
        if method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}
        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": server.list_tools()},
            }
        if method == "tools/call":
            name = params.get("name", "")
            arguments = params.get("arguments") or {}
            result = server.call(name, arguments)
            return {"jsonrpc": "2.0", "id": req_id, "result": result}
        if method.startswith("notifications/"):
            return None
        if notification:
            return None
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }


def main() -> None:
    StdioTransport().serve(_make_server())


if __name__ == "__main__":
    main()
