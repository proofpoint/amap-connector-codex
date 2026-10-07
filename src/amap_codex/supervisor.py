"""Single-owner, serial delivery. Nothing in this module sends mail."""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import logging
import os
from pathlib import Path
import signal
import time
import uuid

from .app_server import AppServerClient, AppServerDelivery, find_event_in_history
from .claims import acquire_consumer_claim, _holder_is_live
from .journal import Journal, IntegrityConflict
from .outcomes import OutcomePublisher, OutcomeUncertain
from .spool import Scanner
from .host_guard import HostGuard
from .lifecycle import LauncherControl
from .operator import queue_directory, read_request, instruction_hash, find_operator, PREFIX
from .app_server import DeliveryResult, NotSubmitted, Uncertain, RPCError

log = logging.getLogger(__name__)
TERMINAL = {"completed", "failed", "interrupted"}


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


class Ownership:
    """Canonical spool claims and private journal lock must both be held."""
    def __init__(self, config):
        self.config = config
        self.lock_fd = None
        self.claims = []
        self.control = LauncherControl(config) if config.launcher_control_argv else None
        self.execution_id = uuid.uuid4().hex if self.control else None

    def acquire(self):
        try:
            self.lock_fd = os.open(self.config.state_dir / "controller.lock",
                                   os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("another controller owns this state directory") from exc
            process_file = self.config.state_dir / "process.json"
            if process_file.exists():
                try:
                    process = json.loads(process_file.read_text())
                    if self.control:
                        if process.get('deployment_id') != self.config.deployment_id or process.get('owner_domain') != self.config.owner_domain:
                            raise RuntimeError('foreign or legacy execution record; migration required')
                        self.control.require_stopped(process['execution_id'])
                    elif not isinstance(process, dict) or _holder_is_live(process):
                        raise RuntimeError("possible orphan launcher; stop it and verify sandbox cleanup before restart")
                except (ValueError, OSError) as exc:
                    raise RuntimeError("unreadable launcher record; operator inspection required") from exc
            for lane in self.config.lanes:
                if self.control:
                    self.claims.append(HostGuard(lane.claim_path, lane.notice_dir,
                        self.config.owner_domain, self.config.deployment_id,
                        self.execution_id, self.control).acquire())
                else:
                    self.claims.append(acquire_consumer_claim(
                        str(lane.claim_path), str(lane.notice_dir), "amap-codex"))
        except BaseException:
            self.release()
            raise

    def release(self, *, clear=True):
        for claim in reversed(self.claims):
            if isinstance(claim, HostGuard):
                claim.release(clear=clear)
            else:
                claim.release()
        self.claims.clear()
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()


class Supervisor:
    def __init__(self, config):
        self.config = config
        self.ownership = Ownership(config)
        self.journal = None
        self.scanner = Scanner()
        self.stop_requested = asyncio.Event()
        self.active_event = None
        self.active_operator = None
        self.active_since = None
        self.backlog = False
        self.delivery = AppServerDelivery(AppServerClient(
            config.launch_argv, timeout=config.rpc_timeout_seconds,
            frame_limit=config.frame_limit_bytes, on_spawn=self._record_process,
            expected_version=config.codex_version,
            env=None if not self.ownership.control else {**os.environ,
                'AMAP_EXECUTION_ID': self.ownership.execution_id,
                'AMAP_DEPLOYMENT_ID': config.deployment_id}),
            model=config.codex_model, cwd=config.cwd, sandbox=config.sandbox,
            approval_policy=config.approval_policy,
            instructions=config.operator_instructions.read_text(), mode=config.delivery_mode,
            trusted_config=config.trusted_overrides())
        self.publisher = None
        self.execution_recorded = False

    def _record_process(self, pid, proc_start):
        value = {"pid": pid, "proc_start": proc_start}
        if self.ownership.control:
            value.update(deployment_id=self.config.deployment_id,
                owner_domain=self.config.owner_domain, execution_id=self.ownership.execution_id)
        atomic_json(self.config.state_dir / "process.json", value)
        self.execution_recorded = True

    async def start(self):
        self.ownership.acquire()
        try:
            c = self.config
            self.journal = Journal(c.state_dir, c.instance_id, c.fingerprint(), c.codex_version, c.codex_model)
            self.publisher = OutcomePublisher(self.journal, c.outcome_dir,
                                               idempotent_replay=c.outcome_idempotent_replay)
            if self.ownership.control:
                self._record_process(os.getpid(), None)
            thread_id = await self.delivery.start_or_resume(self.journal.thread_id)
            self.journal.bind_thread(thread_id)
            await self.reconcile()
            self.write_status()
        except BaseException:
            await self.close()
            raise

    async def reconcile(self):
        events = self.journal.recovery_events()
        # Even an empty ledger must not dispatch onto a resumed externally active thread.
        try:
            history = await self.delivery.observe()
        except Exception:
            self.journal.operational_error("thread history unavailable; dispatch paused")
            if not events:
                raise RuntimeError("cannot establish bound thread is idle")
            return
        turns = history.get("thread", {}).get("turns", [])
        known_turns = {e["turn_id"] for e in events if e["turn_id"]}
        for event in events:
            match = find_event_in_history(history, event["event_id"])
            if event["state"] == "accepted":
                turn = next((t for t in turns if t.get("id") == event["turn_id"]), None)
                if turn:
                    match = event["turn_id"], turn.get("status", "inProgress")
            if match:
                turn_id, status = match
                self.journal.accept(event["event_id"], turn_id)
                known_turns.add(turn_id)
                if status in TERMINAL:
                    self.journal.finish(event["event_id"], status)
                else:
                    self.active_event = event["event_id"]
                    self.active_since = time.monotonic()
            else:
                self.journal.operational_error("acceptance/execution cannot be reconciled from original input; dispatch paused")
        for operator in self.journal.operator_runs(recovery=True):
            match = find_operator(history, operator['run_id'])
            if match and match[2] == operator['instruction_hash']:
                turn_id, status, _ = match
                self.journal.operator_result(operator['run_id'], 'accepted', turn_id=turn_id)
                known_turns.add(turn_id)
                if status in TERMINAL:
                    self.journal.operator_result(operator['run_id'], 'finished', execution_status=status)
                else:
                    self.active_operator = operator['run_id']
                    self.active_since = time.monotonic()
            else:
                self.journal.operational_error('operator dispatch unresolved; automatic work paused')
        if any(t.get("status") == "inProgress" and t.get("id") not in known_turns for t in turns):
            raise RuntimeError("bound thread has an unowned active turn")

    def scan(self):
        counts = self.journal.status()["counts"]
        queued = sum(counts.get(s, 0) for s in ("pending", "dispatching", "accepted", "uncertain"))
        self.backlog = queued >= self.config.max_pending_events
        if self.backlog:
            return
        for lane in self.config.lanes:
            for admission in self.scanner.scan(lane, self.config.instance_id,
                    self.config.self_address, self.config.publication_grace_seconds):
                if queued >= self.config.max_pending_events:
                    self.backlog = True
                    return
                try:
                    inserted = self.journal.admit(admission)
                except IntegrityConflict:
                    log.error("publication conflict event=%s notice=%s", admission.event_id, admission.notice_id)
                    continue
                if inserted:
                    log.info("event=%s lane=%s notice=%s state=%s", admission.event_id,
                             admission.lane, admission.notice_id, admission.state)
                    if admission.state == "pending":
                        queued += 1

    def observe_notifications(self):
        client = self.delivery.client
        while not client.notifications.empty():
            frame = client.notifications.get_nowait()
            params = frame.get("params", {})
            if params.get("threadId") != self.delivery.thread_id:
                continue
            if frame["method"] == "turn/completed":
                turn = params.get("turn", {})
                if self.active_operator:
                    operator = next(r for r in self.journal.operator_runs() if r['run_id'] == self.active_operator)
                    if turn.get('id') == operator['turn_id'] and turn.get('status') in TERMINAL:
                        self.journal.operator_result(self.active_operator, 'finished', execution_status=turn['status'])
                        self.active_operator = self.active_since = None
                if self.active_event:
                    event = self.journal.event(self.active_event)
                    if turn.get("id") == event["turn_id"] and turn.get("status") in TERMINAL:
                        self.journal.finish(self.active_event, turn["status"])
                        log.info("event=%s turn=%s execution=%s", self.active_event, turn["id"], turn["status"])
                        self.active_event = self.active_since = None
            elif frame["method"] == "error":
                self.journal.operational_error("app-server reported a turn error")
        if client.last_error:
            self.journal.operational_error(client.last_error)

    async def tick(self, *, admit=True):
        self.observe_notifications()
        if self.delivery.client.closed:
            raise RuntimeError("app-server disconnected; restart required for reconciliation")
        if admit:
            await self.dispatch_operator()
            self.scan()
            event = self.journal.next_pending()
            if event and not self.stop_requested.is_set():
                # Recheck retained body and exact hash just before starting work.
                lane = next(l for l in self.config.lanes if l.name == event["lane"])
                current = next((a for a in self.scanner.scan(lane, self.config.instance_id,
                    self.config.self_address, self.config.publication_grace_seconds)
                    if a.event_id == event["event_id"]), None)
                if current is None or current.state != "pending" or current.artifact_hash != event["artifact_hash"]:
                    self.journal.hold(event["event_id"], "retained notice/body unavailable or changed before dispatch")
                else:
                    rpc_id = self.delivery.client.reserve_id()
                    self.journal.begin_attempt(event["event_id"], rpc_id)
                    try:
                        result = await self.delivery.deliver(event["payload"], rpc_id)
                    except BaseException:
                        self.journal.uncertain(event["event_id"], "dispatch interrupted before acceptance persisted")
                        raise
                    if result.state == "accepted":
                        self.journal.accept(event["event_id"], result.turn_id)
                        self.active_event = event["event_id"]
                        self.active_since = time.monotonic()
                    elif result.state == "not_submitted":
                        self.journal.unsent(event["event_id"], result.detail)
                    else:
                        self.journal.uncertain(event["event_id"], result.detail)
                    log.info("event=%s notice=%s dispatch=%s", event["event_id"], event["notice_id"], result.state)
                    self.observe_notifications()
        if self.active_since and time.monotonic() - self.active_since > self.config.turn_watchdog_seconds:
            self.journal.operational_error("turn watchdog exceeded; accepted work remains active, never replayed")
        try:
            self.publisher.publish_pending()
        except OutcomeUncertain:
            self.journal.operational_error("outcome publication uncertain; router evidence required")
        self.write_status()

    async def dispatch_operator(self):
        if not self.config.operator_kickoff_enabled or self.stop_requested.is_set():
            return
        requests = {}
        for path in sorted(queue_directory(self.config).glob('*.json')):
            request = read_request(path)
            self.journal.admit_operator(request['run_id'], instruction_hash(request))
            requests[request['run_id']] = request
        for row in self.journal.operator_runs():
            if row['state'] != 'pending' or row['run_id'] not in requests:
                continue
            if self.journal.operator_blocked() or any(self.journal.status()['counts'].get(s) for s in ('accepted','dispatching','uncertain')):
                return
            rpc_id = self.delivery.client.reserve_id()
            self.journal.begin_operator(row['run_id'], rpc_id)
            try:
                result = await self.delivery.client.request('turn/start', {
                    'threadId': self.delivery.thread_id, 'input': [{'type': 'text',
                    'text': PREFIX + json.dumps(requests[row['run_id']], sort_keys=True)}]}, rpc_id=rpc_id)
                turn_id = result.get('turn', {}).get('id')
                if not isinstance(turn_id, str) or not turn_id:
                    self.journal.operator_result(row['run_id'], 'uncertain')
                else:
                    self.journal.operator_result(row['run_id'], 'accepted', turn_id=turn_id)
                    self.active_operator, self.active_since = row['run_id'], time.monotonic()
            except NotSubmitted:
                self.journal.operator_result(row['run_id'], 'unsent')
            except BaseException:
                self.journal.operator_result(row['run_id'], 'uncertain')
                raise
            self.observe_notifications()
            return

    def write_status(self):
        status = self.journal.status()
        status.update(enabled_lanes=[l.name for l in self.config.lanes],
                      operator_runs=self.journal.operator_runs(),
                      claim_state="held" if self.ownership.claims else "released",
                      codex_reviewed=self.config.codex_reviewed,
                      backlog=self.backlog, blocked_requests=self.delivery.client.blocked,
                      supervisor_pid=os.getpid(), updated_at=time.time())
        if self.ownership.control:
            status.update(execution_id=self.ownership.execution_id,
                          deployment_id=self.config.deployment_id, owner_domain=self.config.owner_domain)
        atomic_json(self.config.state_dir / "status.json", status)

    async def run(self):
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.stop_requested.set)
        try:
            await self.start()
            while not self.stop_requested.is_set():
                await self.tick()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self.stop_requested.wait(), self.config.poll_interval_ms / 1000)
        finally:
            await self.close()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(sig)

    async def close(self):
        self.stop_requested.set()
        try:
            if (self.active_event or self.active_operator) and not self.delivery.client.closed:
                deadline = time.monotonic() + self.config.shutdown_grace_seconds
                while (self.active_event or self.active_operator) and time.monotonic() < deadline and not self.delivery.client.closed:
                    self.observe_notifications()
                    if self.active_event or self.active_operator:
                        await asyncio.sleep(min(0.05, max(0, deadline - time.monotonic())))
                if (self.active_event or self.active_operator) and not self.delivery.client.closed:
                    event = self.journal.event(self.active_event) if self.active_event else next(r for r in self.journal.operator_runs() if r['run_id'] == self.active_operator)
                    with contextlib.suppress(Exception):
                        await self.delivery.interrupt(event["turn_id"])
                        self.observe_notifications()
        finally:
            cleanup_verified = not self.ownership.control
            try:
                await self.delivery.stop()
                if self.ownership.control and self.execution_recorded:
                    self.ownership.control.require_stopped(self.ownership.execution_id, stop=True)
                    cleanup_verified = True
                process_file = self.config.state_dir / "process.json"
                if self.execution_recorded and cleanup_verified and (self.ownership.control or (self.delivery.client.process is not None and self.delivery.client.process.returncode is not None)):
                    with contextlib.suppress(FileNotFoundError):
                        process_file.unlink()
            finally:
                self.ownership.release(clear=cleanup_verified)
                if self.journal:
                    try:
                        self.write_status()
                    finally:
                        self.journal.close()
                        self.journal = None
