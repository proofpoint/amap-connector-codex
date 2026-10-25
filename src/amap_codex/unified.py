"""Owned native conversation and AMAP queue controller, independent of adapters."""
import asyncio
import contextlib
import os
from pathlib import Path
import signal
import time

from .attach_gate import AttachGate, AttachPolicy
from .journal import Journal, IntegrityConflict
from .app_server import RPCError, Uncertain
from .managed_backend import ManagedBackend
from .native_queue import NativeQueue, preflight_pending
from .outcomes import OutcomePublisher, OutcomeUncertain
from .spool import Scanner
from .supervisor import Ownership, Supervisor, atomic_json
from .unified_policy import resolve, validate_tools


class UnifiedService:
    def __init__(self, config, config_path, *, env=None):
        if config.conversation_mode != 'unified':
            raise ValueError('unified service requires explicit unified configuration')
        self.config, self.config_path = config, Path(config_path)
        self.ownership = Ownership(config)
        self.backend = ManagedBackend(config.state_dir, config.unified_argv,
            expected_version=config.codex_version, timeout=config.rpc_timeout_seconds,
            frame_limit=config.frame_limit_bytes, on_spawn=self._record_process, env=env)
        self.journal = self.queue = self.gate = self.publisher = None
        self.scanner = Scanner()
        self.backlog = False
        self.stop_requested = asyncio.Event()
        self.effective = None
        self.status = None

    def _record_process(self, pid, proc_start):
        atomic_json(self.config.state_dir / 'process.json', {
            'pid': pid, 'proc_start': proc_start, 'mode': 'unified', 'generation': self.backend.generation})

    async def start(self):
        self.ownership.acquire()
        try:
            c = self.config
            self.journal = Journal(c.state_dir, c.instance_id, c.fingerprint(), c.codex_version, c.codex_model)
            self.journal.db.execute('''CREATE TABLE IF NOT EXISTS unified_binding (
                instance_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, policy_hash TEXT NOT NULL,
                effective_model TEXT NOT NULL)''')
            client = await self.backend.start()
            settings, protected, digest = await resolve(client, c, self.config_path)
            old = self.journal.db.execute('SELECT * FROM unified_binding WHERE instance_id=?', (c.instance_id,)).fetchone()
            thread_id = self.journal.thread_id
            if thread_id and (old is None or old['thread_id'] != thread_id or old['policy_hash'] != digest):
                raise ValueError('canonical history requires explicit adoption or policy migration; no private fallback is allowed')
            if thread_id:
                await preflight_pending(client, self.journal, self.retained)
            params = dict(settings)
            if thread_id:
                params.update(threadId=thread_id, excludeTurns=True)
            else:
                params['ephemeral'] = False
            result = await client.request('thread/resume' if thread_id else 'thread/start', params)
            actual = result.get('thread', {}).get('id')
            if not isinstance(actual, str) or not actual or (thread_id and actual != thread_id):
                raise ValueError('backend did not return the canonical conversation')
            if not thread_id:
                # A named empty thread has a saved rollout in the measured build.
                await client.request('thread/name/set', {'threadId': actual, 'name': 'AMAP unified conversation'})
            await validate_tools(client, actual, protected)
            sandbox_type = {'read-only': 'readOnly', 'workspace-write': 'workspaceWrite',
                            'danger-full-access': 'dangerFullAccess'}[c.sandbox]
            if (not isinstance(result.get('model'), str) or result.get('approvalPolicy') != c.approval_policy
                    or (c.codex_model is not None and result['model'] != c.codex_model)
                    or result.get('sandbox', {}).get('type') != sandbox_type
                    or Path(result.get('cwd', '')).resolve() != Path(c.cwd).resolve()):
                raise ValueError('resolved native model or permissions are unavailable or mismatched')
            with self.journal._transaction():
                if old and old['effective_model'] != result['model']:
                    self.journal._audit(None, 'unified_model_changed', old['effective_model'] + ' -> ' + result['model'])
                self.journal.db.execute('UPDATE instance SET thread_id=? WHERE instance_id=?', (actual, c.instance_id))
                self.journal.db.execute('INSERT OR REPLACE INTO unified_binding VALUES(?,?,?,?)',
                                        (c.instance_id, actual, digest, result['model']))
            self.effective = {'model': result['model'], 'sandbox': result['sandbox'],
                'approval_policy': result['approvalPolicy'], 'source': 'resolved shared pane settings',
                'model_source': 'connector.codex_model' if c.codex_model is not None else 'native Codex configuration',
                'permission_source': 'connector pane configuration'}
            policy = AttachPolicy(actual, cwd=c.cwd, model=result['model'], sandbox_name=c.sandbox,
                sandbox=result['sandbox'], approval_policy=c.approval_policy, roots=result.get('runtimeWorkspaceRoots'),
                approvals_reviewer=result.get('approvalsReviewer'))
            self.queue = NativeQueue(self.journal, client, self.backend.generation)
            self.publisher = OutcomePublisher(self.journal, c.outcome_dir, idempotent_replay=c.outcome_idempotent_replay)
            self.gate = AttachGate(self.backend, policy)
            clean, history, pending = await self.observe()
            self.gate.accepting = False
            await self.gate.start()
            self.write_status(clean, history, pending)
            self.gate.accepting = self.status['readiness']['state'] == 'ready'
        except BaseException:
            await self.close()
            raise

    async def tick(self):
        if self.backend.process.returncode is not None or self.backend.client.closed:
            raise RuntimeError('owned backend exited; restart requires reconciliation')
        # Stream notifications are observational; durable original inputs decide
        # acceptance. No second scheduler or unknown-human-turn rejection.
        while not self.backend.client.notifications.empty():
            self.backend.client.notifications.get_nowait()
        Supervisor.scan(self)
        clean, history, pending = await self.observe()
        if history is None:
            self.gate.accepting = False
            self.write_status(False, None, None)
            return
        if clean:
            self.journal.clear_operational_error()
            event = self.journal.next_pending()
            if event and not self.stop_requested.is_set():
                if not self.retained(event):
                    self.journal.hold(event['event_id'], 'retained notice/body changed before native queue submission')
                    clean = False
                else:
                    await self.queue.dispatch(event['event_id'])
                    clean, history, pending = await self.observe()
        else:
            self.journal.operational_error('native delivery is held pending original-input reconciliation')
        try:
            self.publisher.publish_pending()
        except OutcomeUncertain:
            clean = False
            self.journal.operational_error('outcome publication requires runtime reconciliation')
        self.write_status(clean, history, pending)
        self.gate.accepting = self.status['readiness']['state'] == 'ready'

    def retained(self, event):
        lane = next(l for l in self.config.lanes if l.name == event['lane'])
        current = next((a for a in self.scanner.scan(lane, self.config.instance_id,
            self.config.self_address, self.config.publication_grace_seconds) if a.event_id == event['event_id']), None)
        return current is not None and current.state == 'pending' and current.artifact_hash == event['artifact_hash']

    async def observe(self):
        try:
            return await self.queue.reconcile()
        except (IntegrityConflict, RPCError, Uncertain):
            # A turn can begin between turn and item pagination. Hold new AMAP
            # dispatch/attachments while observing again; do not kill authorized
            # work merely because a read was incomplete or temporarily failed.
            self.journal.operational_error('native history observation unavailable; no automatic resubmission')
            return False, None, None

    def write_status(self, clean, history, pending):
        runtime = history['thread']['status'] if history else None
        turns = history['thread']['turns'] if history else []
        active = next((t for t in reversed(turns) if t['status'] == 'inProgress'), None) if runtime and runtime['type'] == 'active' else None
        active_source = None
        if active:
            event = self.journal.db.execute('SELECT lane FROM events WHERE turn_id=?', (active['id'],)).fetchone()
            active_source = event[0] if event else ('operator' if any(
                i.get('type') == 'userMessage' and i.get('clientId') for i in active['items']) else None)
        sources = {'operator': 0, 'mail': 0, 'peer': 0} if pending is not None else None
        for item in pending or []:
            lane = self.journal.db.execute('SELECT e.lane FROM events e JOIN native_queue_intents q '
                'ON q.event_id=e.event_id WHERE q.client_id=?', (item.get('clientUserMessageId'),)).fetchone()
            sources[lane[0] if lane else 'operator'] += 1
        checks = {'backend_live': self.backend.process.returncode is None,
                  'canonical_binding': history['thread']['id'] == self.journal.thread_id if history else None,
                  'configuration_matches': self.journal.status()['fingerprint'] == self.config.fingerprint(),
                  'protected_tools': True, 'native_reconciled': clean if history else None,
                  'publication_resolved': self.journal.db.execute("SELECT 1 FROM outcome_transitions "
                      "WHERE publication_state IN ('publishing','ambiguous') LIMIT 1").fetchone() is None}
        self.status = {**self.journal.status(), 'schema_version': 1, 'mode': 'unified',
            'backend_generation': self.backend.generation, 'canonical_thread_id': self.journal.thread_id,
            'endpoint': 'unix://' + str(self.backend.front_path), 'backend_pid': self.backend.process.pid,
            'attached_client_count': self.gate.client_count if self.gate else 0,
            'scheduler_state': 'reconciling' if history is None else 'holding' if not clean else runtime['type'],
            'active_input_source': active_source, 'queue_depth': len(pending) if pending is not None else None, 'queue_sources': sources,
            'effective_settings': self.effective, 'blocked_requests': self.backend.client.blocked,
            'supervisor_pid': os.getpid(), 'updated_at': time.time(),
            'claim_state': 'held', 'enabled_lanes': [l.name for l in self.config.lanes],
            'readiness': {'state': 'ready' if all(v is True for v in checks.values()) else 'holding',
                          'checks': {k: 'unknown' if v is None else 'pass' if v else 'fail' for k, v in checks.items()}}}
        atomic_json(self.config.state_dir / 'unified.json', self.status)
        atomic_json(self.config.state_dir / 'status.json', self.status)

    async def run(self):
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT): loop.add_signal_handler(sig, self.stop_requested.set)
        try:
            await self.start()
            while not self.stop_requested.is_set():
                await self.tick()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self.stop_requested.wait(), self.config.poll_interval_ms / 1000)
        finally:
            await self.close()
            for sig in (signal.SIGTERM, signal.SIGINT): loop.remove_signal_handler(sig)

    async def close(self):
        self.stop_requested.set()
        try:
            if self.gate: await self.gate.close()
            await self.backend.stop()
            if self.status:
                self.status.update(readiness={'state': 'stopped', 'checks': {}}, claim_state='released', updated_at=time.time())
                atomic_json(self.config.state_dir / 'unified.json', self.status)
                atomic_json(self.config.state_dir / 'status.json', self.status)
            if self.backend.process is not None and self.backend.process.returncode is not None:
                with contextlib.suppress(FileNotFoundError): (self.config.state_dir / 'process.json').unlink()
        finally:
            self.ownership.release(clear=self.backend.process is None or self.backend.process.returncode is not None)
            if self.journal:
                self.journal.close()
                self.journal = None
