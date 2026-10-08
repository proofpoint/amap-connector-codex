#!/usr/bin/env python3
"""Deterministic app-server faults; deliberately no inference and no MCP
servers. Its MCP registry is a model of the measured one (codex-cli 0.160.1
and 0.161.0): the agent's own registrations (FAKE_AMBIENT_MCP, JSON), which
config/read reports, merged per server with the thread's override, where
`enabled = false` leaves a server listed with no tools. FAKE_UNREPORTED_MCP
names a registration config/read does not report; FAKE_IGNORE_DISABLE keeps
a disabled server's tools. FAKE_CHATGPT_APPS models a ChatGPT login, under
which Codex adds `codex_apps` unless the thread sets `features.apps = false`."""
import json
import os
import sys
from pathlib import Path

mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
history_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None
history = json.loads(history_path.read_text()) if history_path and history_path.exists() else []
thread_id = "thread-fixture"
initialized = False
ambient = json.loads(os.environ.get("FAKE_AMBIENT_MCP", "{}"))
unreported = os.environ.get("FAKE_UNREPORTED_MCP")
override = {}
apps_off = False


def registry():
    names = set(ambient) | set(override) | ({unreported} if unreported else set())
    if os.environ.get("FAKE_CHATGPT_APPS") and not apps_off:
        names.add("codex_apps")
    out = []
    for name in sorted(names):
        server = {**ambient.get(name, {}), **override.get(name, {})}
        tools = server.get("enabled_tools", ["agent_tool"])
        if server.get("enabled") is False and not os.environ.get("FAKE_IGNORE_DISABLE"):
            tools = []
        out.append({"name": name, "tools": {t: {} for t in tools}})
    return out


def emit(frame):
    print(json.dumps(frame), flush=True)


def save():
    if history_path:
        history_path.write_text(json.dumps(history))


for raw in sys.stdin:
    request = json.loads(raw)
    if "method" not in request:
        if mode == "approval" and request.get("id") == "approval-1":
            if request.get("result", {}).get("decision") != "cancel":
                sys.exit(9)
        continue
    method, rpc_id, params = request["method"], request.get("id"), request.get("params", {})
    result = {}
    if method == "initialize":
        result = {"userAgent": "codex-fixture/0.160.1"}
        initialized = True
    elif method == "initialized":
        continue
    elif not initialized:
        sys.exit(10)
    elif method in {"thread/start", "thread/resume"}:
        if mode == "resume_error" and method == "thread/resume":
            emit({"id": rpc_id, "error": {"code": -32001, "message": "missing"}})
            continue
        override = (params.get("config") or {}).get("mcp_servers", {})
        apps_off = ((params.get("config") or {}).get("features") or {}).get("apps") is False
        result = {"thread": {"id": thread_id, "turns": history}}
    elif method == "config/read":
        result = {"config": {"mcp_servers": ambient}, "origins": {}}
    elif method == "mcpServerStatus/list":
        result = {"data": registry(), "nextCursor": None}
    elif method == "thread/read":
        result = {"thread": {"id": thread_id, "turns": history}}
    elif method == "turn/start":
        turn_id = f"turn-{len(history) + 1}"
        item = {"type": "functionCallOutput", "id": f"item-{turn_id}", **params["toolOutput"]} if "toolOutput" in params else {
            "type": "userMessage", "id": f"item-{turn_id}", "content": params["input"]}
        turn = {"id": turn_id, "status": "inProgress", "items": [item]}
        history.append(turn)
        save()
        if mode == "death":
            os._exit(17)
        if mode == "timeout":
            continue
        if mode == "rpc_error":
            emit({"id": rpc_id, "error": {"code": -32603, "message": "generic"}})
            continue
        if mode == "malformed":
            print("not JSON", flush=True)
            continue
        if mode == "oversized":
            print("x" * 10000, flush=True)
            continue
        if mode == "early_completed":
            turn["status"] = "completed"
            save()
            emit({"method": "turn/completed", "params": {"threadId": thread_id, "turn": turn}})
        result = {"turn": turn}
        emit({"id": rpc_id, "result": result})
        if mode in {"approval", "user_input"}:
            emit({"id": "approval-1", "method": "item/commandExecution/requestApproval" if mode == "approval" else "item/tool/requestUserInput",
                  "params": {"threadId": thread_id, "turnId": turn_id}})
        elif mode not in {"busy", "early_completed"}:
            turn["status"] = "completed"
            save()
            emit({"method": "turn/completed", "params": {"threadId": thread_id, "turn": turn}})
        continue
    elif method == "turn/interrupt":
        for turn in history:
            if turn["id"] == params["turnId"]:
                turn["status"] = "interrupted"
                emit({"method": "turn/completed", "params": {"threadId": thread_id, "turn": turn}})
        save()
    if rpc_id is not None:
        emit({"id": rpc_id, "result": result})
