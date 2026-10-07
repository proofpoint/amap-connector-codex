"""JSONL app-server transport. A write may succeed even when its RPC fails."""

from __future__ import annotations

import asyncio
import copy
import contextlib
import json
import os
import signal
from dataclasses import dataclass
from typing import Any


class NotSubmitted(Exception):
    """Positive local evidence that no bytes were submitted."""


class Uncertain(Exception):
    """The request may have reached the server. Never automatically replay."""


class RPCError(Exception):
    def __init__(self, error: dict):
        self.code = error.get("code")
        # Server strings can contain sender data; expose only the error code.
        super().__init__(f"app-server RPC error {self.code}")


TRUSTED_SERVERS = frozenset({"inbox", "delegation", "inbox_submit"})


def version_matches(user_agent: str, expected: str) -> bool:
    return (isinstance(user_agent, str) and bool(user_agent.split())
            and user_agent.split()[0].rsplit("/", 1)[-1] == expected.split()[-1])


@dataclass(frozen=True)
class DeliveryResult:
    state: str
    turn_id: str | None = None
    detail: str = ""


class AppServerClient:
    def __init__(self, argv: list[str] | tuple[str, ...], *, timeout: float = 30,
                 frame_limit: int = 64 * 1024 * 1024, on_spawn=None, expected_version=None, env=None):
        self.argv = argv
        self.timeout = timeout
        self.frame_limit = frame_limit
        self.process: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 1
        self._write_lock = asyncio.Lock()
        self.notifications: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._requests: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._reader: asyncio.Task | None = None
        self._responder: asyncio.Task | None = None
        self.blocked: list[dict] = []
        self.last_error: str | None = None
        self.closed = False
        self.pid_start: str | None = None
        self.on_spawn = on_spawn
        self.initialize_result: dict = {}
        self.expected_version = expected_version
        self.env = env

    def reserve_id(self) -> int:
        value = self._next_id
        self._next_id += 1
        return value

    async def start(self) -> None:
        if self.process is not None:
            raise RuntimeError("client already started")
        self.process = await asyncio.create_subprocess_exec(
            *self.argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            # Deployment diagnostics remain on stderr, never in the protocol.
            stderr=None, start_new_session=True, limit=self.frame_limit + 1, env=self.env)
        from .claims import _proc_start
        self.pid_start = _proc_start(self.process.pid)
        if self.on_spawn:
            try:
                self.on_spawn(self.process.pid, self.pid_start)
            except BaseException:
                await self.stop()
                raise
        self._reader = asyncio.create_task(self._read_loop())
        self._responder = asyncio.create_task(self._respond_loop())
        try:
            self.initialize_result = await self.request("initialize", {
                "clientInfo": {"name": "amap_connector", "title": "AMAP Codex", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True},
            })
            if self.expected_version and not version_matches(self.initialize_result.get("userAgent"), self.expected_version):
                raise RuntimeError("launched app-server reports a version other than the build the configuration states")
            await self.notify("initialized", {})
        except BaseException:
            await self.stop()
            raise

    async def _send(self, frame: dict) -> None:
        try:
            wire = (json.dumps(frame, separators=(",", ":"), ensure_ascii=True) + "\n").encode()
        except (TypeError, ValueError) as exc:
            raise NotSubmitted("invalid local frame") from exc
        if len(wire) > self.frame_limit:
            raise NotSubmitted("local frame exceeds configured limit")
        async with self._write_lock:
            process = self.process
            if self.closed or process is None or process.returncode is not None or process.stdin is None:
                raise NotSubmitted("app-server unavailable before write")
            try:
                process.stdin.write(wire)
                await asyncio.wait_for(process.stdin.drain(), self.timeout)
            except asyncio.TimeoutError as exc:
                raise Uncertain("protocol write timeout after dispatch") from exc
            except (ConnectionError, OSError) as exc:
                raise Uncertain("protocol write failed") from exc

    async def notify(self, method: str, params: dict) -> None:
        await self._send({"method": method, "params": params})

    async def request(self, method: str, params: dict, *, rpc_id: int | None = None) -> dict:
        rpc_id = self.reserve_id() if rpc_id is None else rpc_id
        if rpc_id in self._pending:
            raise ValueError("RPC ID already in use")
        future = asyncio.get_running_loop().create_future()
        self._pending[rpc_id] = future
        try:
            try:
                await asyncio.wait_for(self._send({"id": rpc_id, "method": method, "params": params}), self.timeout)
            except asyncio.TimeoutError as exc:
                raise Uncertain("RPC write timeout after dispatch") from exc
            try:
                return await asyncio.wait_for(asyncio.shield(future), self.timeout)
            except asyncio.TimeoutError as exc:
                raise Uncertain("RPC response timeout after dispatch") from exc
        finally:
            self._pending.pop(rpc_id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                # Retrieve exceptions if sending failed while the reader closed.
                future.exception()

    async def _read_loop(self) -> None:
        assert self.process and self.process.stdout
        try:
            while raw := await self.process.stdout.readline():
                if len(raw) > self.frame_limit or not raw.endswith(b"\n"):
                    raise ValueError("invalid protocol frame length/framing")
                frame = json.loads(raw)
                if not isinstance(frame, dict):
                    raise ValueError("protocol frame must be an object")
                if "id" in frame and (type(frame["id"]) not in (int, str)):
                    raise ValueError("invalid protocol request ID")
                if "method" in frame and "id" in frame:
                    if not isinstance(frame["method"], str) or not isinstance(frame.get("params", {}), dict):
                        raise ValueError("invalid server request")
                    self._requests.put_nowait(frame)
                elif "id" in frame:
                    future = self._pending.get(frame["id"])
                    if future and not future.done():
                        if "error" in frame:
                            if not isinstance(frame["error"], dict):
                                raise ValueError("invalid RPC error")
                            future.set_exception(RPCError(frame["error"]))
                        elif isinstance(frame.get("result"), dict):
                            future.set_result(frame["result"])
                        else:
                            raise ValueError("malformed RPC response")
                elif frame.get("method") in {
                    "turn/started", "turn/completed", "thread/status/changed", "error",
                }:
                    if not isinstance(frame.get("params", {}), dict):
                        raise ValueError("invalid lifecycle notification")
                    self.notifications.put_nowait(frame)
                # Stream deltas/tool bodies are consumed without logging or retaining them.
        except (ValueError, TypeError, OSError, asyncio.QueueFull, ConnectionError):
            self.last_error = "app-server protocol failure"
        finally:
            self.closed = True
            self.last_error = self.last_error or "app-server disconnected"
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(Uncertain(self.last_error))

    async def _respond_loop(self) -> None:
        try:
            while True:
                frame = await self._requests.get()
                method, params = frame["method"], frame.get("params", {})
                self.blocked.append({"method": method, "thread_id": params.get("threadId"),
                                     "turn_id": params.get("turnId")})
                self.blocked = self.blocked[-100:]
                self.last_error = f"headless request blocked: {method}" if method in {
                    "item/commandExecution/requestApproval", "item/fileChange/requestApproval",
                    "execCommandApproval", "applyPatchApproval", "item/permissions/requestApproval",
                    "mcpServer/elicitation/request", "item/tool/requestUserInput",
                } else "headless request blocked: unsupported server request"
                if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
                    response = {"decision": "cancel"}
                elif method in {"execCommandApproval", "applyPatchApproval"}:
                    response = {"decision": "abort"}
                elif method == "item/permissions/requestApproval":
                    response = {"permissions": {}, "scope": "turn"}
                elif method == "mcpServer/elicitation/request":
                    response = {"action": "cancel"}
                else:
                    # User input has no fabricated answer. Explicit RPC cancellation followed by interrupt.
                    await self._send({"id": frame["id"], "error": {
                        "code": -32000, "message": "headless connector cannot resolve this request"}})
                    response = None
                if response is not None:
                    await self._send({"id": frame["id"], "result": response})
                if params.get("threadId") and params.get("turnId"):
                    with contextlib.suppress(NotSubmitted, Uncertain, RPCError):
                        await self.request("turn/interrupt", {
                            "threadId": params["threadId"], "turnId": params["turnId"]})
        except (NotSubmitted, Uncertain, RPCError, ValueError, TypeError):
            self.last_error = "headless request resolution failed"

    async def stop(self) -> None:
        self.closed = True
        process = self.process
        if process:
            if process.stdin:
                process.stdin.close()
            # Kill the launcher session too: direct launchers must exec; container launchers
            # additionally own cleanup of their remote app-server children.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), 5)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
        for task in (self._reader, self._responder):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task


class AppServerDelivery:
    """One concrete adapter; acceptance and execution are separate evidence."""
    def __init__(self, client: AppServerClient, *, model: str | None, cwd: str, sandbox: str,
                 approval_policy: str, instructions: str, mode: str = "tool_output", trusted_config=None):
        self.client = client
        self.settings = {"cwd": cwd, "sandbox": sandbox,
                         "approvalPolicy": approval_policy, "developerInstructions": instructions}
        if model is not None:
            self.settings["model"] = model
        self.mode = mode
        self.trusted_config = trusted_config
        if trusted_config is not None:
            self.settings['config'] = trusted_config
        self.thread_id: str | None = None
        # The agent's own MCP servers the bound thread runs with switched off.
        self.disabled_servers: list[str] = []

    async def start_or_resume(self, thread_id: str | None = None) -> str:
        await self.client.start()
        method = "thread/resume" if thread_id else "thread/start"
        params = dict(self.settings)
        if thread_id:
            params["threadId"] = thread_id
        else:
            params["ephemeral"] = False
        disabled = set()
        if self.trusted_config is not None:
            params["config"], disabled = await self._thread_config()
        self.disabled_servers = sorted(disabled)
        result = await self.client.request(method, params)
        actual = result.get("thread", {}).get("id")
        if not isinstance(actual, str) or not actual or (thread_id and actual != thread_id):
            raise RuntimeError("app-server returned invalid target binding")
        self.thread_id = actual
        if self.trusted_config is not None:
            statuses=[]
            cursor=None
            for _ in range(10):
                params={'threadId':self.thread_id, 'detail':'toolsAndAuthOnly'}
                if cursor is not None: params['cursor']=cursor
                page=await self.client.request('mcpServerStatus/list', params)
                statuses.extend(page.get('data', []))
                cursor=page.get('nextCursor')
                if cursor is None: break
            else: raise RuntimeError('MCP registry exceeded expected bound')
            names={s.get('name') for s in statuses}
            if names != TRUSTED_SERVERS | disabled:
                raise RuntimeError('effective MCP registry differs from trusted configuration: '
                                   f'unexpected {sorted(map(str, names - TRUSTED_SERVERS - disabled))}, '
                                   f'missing {sorted((TRUSTED_SERVERS | disabled) - names)}')
            for server in statuses:
                if server['name'] in disabled:
                    if server.get('tools'):
                        raise RuntimeError(f"the agent's own MCP server {server['name']!r} still exposes tools")
                    continue
                expected=set(self.trusted_config['mcp_servers'][server['name']]['enabled_tools'])
                if set(server.get('tools',{})) != expected or server.get('toolsError'):
                    raise RuntimeError('effective MCP tools differ from trusted allowlist')
        return actual

    async def _thread_config(self):
        """The thread's MCP configuration: the trusted servers, and every
        server the agent's own Codex configuration registers switched off.
        Codex merges a thread's MCP override into the registrations it
        already has, so those would otherwise join the thread."""
        read = await self.client.request("config/read", {"cwd": self.settings["cwd"]})
        servers = (read.get("config") or {}).get("mcp_servers") or {}
        if not isinstance(servers, dict):
            raise RuntimeError("config/read returned an MCP server table that is not a table")
        clash = sorted(set(servers) & TRUSTED_SERVERS)
        if clash:
            raise RuntimeError(f"the agent's Codex configuration registers MCP server(s) {clash}, "
                               "names the AMAP servers use; rename them there")
        disabled = set(servers) - TRUSTED_SERVERS
        config = copy.deepcopy(self.trusted_config)
        config["mcp_servers"].update({name: {"enabled": False} for name in sorted(disabled)})
        return config, disabled

    def turn_params(self, payload: dict) -> dict:
        output = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        params: dict[str, Any] = {"threadId": self.thread_id, "input": []}
        if self.mode == "tool_output":
            params["toolOutput"] = {"name": "amap_notice", "namespace": "amap_connector", "output": output}
        elif self.mode == "wake_up":
            params["input"] = [{"type": "text", "text": "AMAP notice pointer. Follow the standing workflow: " + output}]
        else:
            raise ValueError("unsupported delivery mode")
        return params

    async def deliver(self, payload: dict, rpc_id: int) -> DeliveryResult:
        try:
            result = await self.client.request("turn/start", self.turn_params(payload), rpc_id=rpc_id)
            turn_id = result.get("turn", {}).get("id")
            if not isinstance(turn_id, str) or not turn_id:
                return DeliveryResult("uncertain", detail="acceptance missing turn association")
            return DeliveryResult("accepted", turn_id)
        except NotSubmitted as exc:
            return DeliveryResult("not_submitted", detail=str(exc))
        except (Uncertain, RPCError) as exc:
            return DeliveryResult("uncertain", detail=str(exc))

    async def observe(self) -> dict:
        return await self.client.request("thread/read", {"threadId": self.thread_id, "includeTurns": True})

    async def interrupt(self, turn_id: str) -> None:
        await self.client.request("turn/interrupt", {"threadId": self.thread_id, "turnId": turn_id})

    async def stop(self) -> None:
        await self.client.stop()


def find_event_in_history(history: dict, event_id: str) -> tuple[str, str] | None:
    """Only original connector input is evidence; never match assistant echoes."""
    for turn in history.get("thread", {}).get("turns", []):
        for item in turn.get("items", []):
            raw = None
            if (item.get("type") == "functionCallOutput" and item.get("name") == "amap_notice"
                    and item.get("namespace") == "amap_connector"):
                raw = item.get("output")
            elif item.get("type") == "userMessage":
                content = item.get("content", [])
                if len(content) == 1 and content[0].get("type") == "text":
                    text = content[0].get("text", "")
                    prefix = "AMAP notice pointer. Follow the standing workflow: "
                    if text.startswith(prefix):
                        raw = text[len(prefix):]
            if isinstance(raw, str):
                try:
                    payload = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(payload, dict) and payload.get("event_id") == event_id:
                    turn_id = turn.get("id")
                    if isinstance(turn_id, str) and turn_id:
                        return turn_id, turn.get("status", "inProgress")
    return None
