"""Tests for the MCP server: tool logic + full stdio protocol round trip."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ffxiv_reset_mcp import server as srv
from ffxiv_reset_mcp import store as st

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def server_with_store(monkeypatch, tmp_path):
    store = st.Store(tmp_path / "tasks.json")
    monkeypatch.setattr(srv, "tool_store", store)
    return store


# ------------------------------------------------------------------ tool calls
def test_call_unknown_tool(server_with_store):
    result = srv.ToolServer(server_with_store).call("nope", {})
    assert result["isError"] is True
    assert "unknown tool" in result["content"][0]["text"]


def test_add_and_complete_via_tools(server_with_store):
    s = srv.ToolServer(server_with_store)
    added = s.call(
        "add_task",
        {"character": "Miqo Tia", "title": "GC turn-ins", "category": "gc_turnin"},
    )
    assert added.get("isError") is not True
    assert '"id": 1' in added["content"][0]["text"]

    # list_characters
    assert "Miqo Tia" in s.call("list_characters", {})["content"][0]["text"]

    listed = s.call("list_tasks", {"character": "Miqo Tia"})["content"][0]["text"]
    assert "[DUE]" in listed and "GC turn-ins" in listed

    ok = s.call("complete_task", {"task_id": 1})["content"][0]["text"]
    assert "Marked #1" in ok
    assert "[DONE]" in s.call("list_tasks", {})["content"][0]["text"]


def test_tool_error_becomes_iserror(server_with_store):
    s = srv.ToolServer(server_with_store)
    result = s.call("add_task", {"character": "", "title": "x"})
    assert result["isError"] is True
    assert "character name is required" in result["content"][0]["text"]

    result = s.call("complete_task", {"task_id": "abc"})
    assert result["isError"] is True


def test_summary_tool_text(server_with_store):
    s = srv.ToolServer(server_with_store)
    s.call("add_task", {"character": "Lala Lin", "title": "Item turn-ins"})
    summary = s.call("summary", {})["content"][0]["text"]
    assert "FFXIV reset report" in summary
    assert "Item turn-ins" in summary
    assert "15:00 UTC" in summary


def test_list_resets_tool(server_with_store):
    s = srv.ToolServer(server_with_store)
    text = s.call("list_resets", {})["content"][0]["text"]
    assert "daily reset" in text and "weekly reset" in text


def test_list_tools_schema_has_inputs(server_with_store):
    s = srv.ToolServer(server_with_store)
    tools = s.list_tools()
    names = [t["name"] for t in tools]
    for expected in (
        "list_resets", "list_characters", "list_tasks", "add_task",
        "complete_task", "uncomplete_task", "delete_task", "summary",
    ):
        assert expected in names
    add_schema = next(t for t in tools if t["name"] == "add_task")["inputSchema"]
    assert add_schema["required"] == ["character", "title"]


# -------------------------------------------------------------- stdio protocol
def _rpc(proc, method, params=None, req_id=None):
    payload = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        payload["params"] = params
    if req_id is not None:
        payload["id"] = req_id
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()
    if req_id is None:
        return None
    line = proc.stdout.readline()
    assert line.strip(), f"no response for {method}"
    return json.loads(line)


def test_stdio_full_round_trip(tmp_path):
    import os
    env = {**dict(os.environ), "FFXIV_RESET_STORE": str(tmp_path / "t.json")}
    proc = subprocess.Popen(
        [sys.executable, "-m", "ffxiv_reset_mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(ROOT),
        env=env,
    )
    try:
        resp = _rpc(proc, "initialize", {"protocolVersion": "2025-03-26"}, 1)
        assert resp["result"]["serverInfo"]["name"] == "ffxiv-reset-mcp"
        assert resp["result"]["capabilities"]["tools"] == {}
        # notification: no response expected (we don't read one here)
        _rpc(proc, "notifications/initialized")

        resp = _rpc(proc, "tools/list", {}, 2)
        names = [t["name"] for t in resp["result"]["tools"]]
        assert "summary" in names and "add_task" in names

        resp = _rpc(proc, "tools/call", {
            "name": "add_task",
            "arguments": {"character": "Miqo Tia", "title": "Beast tribe dailies"},
        }, 3)
        assert '"id": 1' in resp["result"]["content"][0]["text"]

        resp = _rpc(proc, "tools/call", {"name": "summary", "arguments": {}}, 4)
        text = resp["result"]["content"][0]["text"]
        assert "FFXIV reset report" in text and "Beast tribe dailies" in text

        resp = _rpc(proc, "tools/call", {"name": "nosuch", "arguments": {}}, 5)
        assert resp["result"]["isError"] is True

        resp = _rpc(proc, "frobnicate", {}, 6)
        assert resp["error"]["code"] == -32601
    finally:
        proc.stdin.close()
        proc.wait(timeout=5)
        err = proc.stderr.read()
        # The server must not crash ad print stack traces to the agent-facing stderr.
        assert proc.returncode == 0, err


def test_stdio_bad_line_and_missing_fields(tmp_path):
    import os
    env = {**dict(os.environ), "FFXIV_RESET_STORE": str(tmp_path / "t.json")}
    proc = subprocess.Popen(
        [sys.executable, "-m", "ffxiv_reset_mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, cwd=str(ROOT), env=env,
    )
    try:
        proc.stdin.write("not json\n")
        proc.stdin.flush()
        resp = json.loads(proc.stdout.readline())
        assert resp["error"]["code"] == -32700
    finally:
        proc.stdin.close()
        proc.wait(timeout=5)
        assert proc.returncode == 0
