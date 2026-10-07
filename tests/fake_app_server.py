#!/usr/bin/env python3
"""Deterministic app-server faults; deliberately no inference or MCP emulation."""
import json
import os
import sys
from pathlib import Path

mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
history_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None
history = json.loads(history_path.read_text()) if history_path and history_path.exists() else []
thread_id = "thread-fixture"
initialized = False


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
        result = {"thread": {"id": thread_id, "turns": history}}
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
