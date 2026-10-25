import asyncio
import copy
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from amap_codex.app_server import Uncertain
from amap_codex.interactive import toml_value
from amap_codex.journal import Journal, IntegrityConflict, StateConflict
from amap_codex.native_queue import NativeQueue
from amap_codex.supervisor import Ownership
from amap_codex import unified_maintenance as maintenance
from test_supervisor import config_for

THREAD = '11111111-1111-4111-8111-111111111111'
OTHER = '22222222-2222-4222-8222-222222222222'


class Native:
    def __init__(self, config):
        self.config, self.calls, self.pending = config, [], []
        self.turns = [{'id': 'turn1', 'status': 'completed', 'items': [
            {'id': 'item1', 'type': 'userMessage', 'clientId': 'human1',
             'content': [{'type': 'text', 'text': 'Original human history'}]}]}]
        self.failure = None
        self.settings = {}
    def reserve_id(self): return len(self.calls) + 1
    async def request(self, method, params, **kwargs):
        self.calls.append((method, copy.deepcopy(params)))
        if method == 'config/read': return {'config': {}}
        if method == 'thread/read': return {'thread': {'id': THREAD, 'cwd': self.config.cwd,
                                                       'status': {'type': 'idle'}}}
        if method == 'thread/turns/list': return {'data': [{**t, 'items': []} for t in self.turns]}
        if method == 'thread/items/list':
            return {'data': [{'turnId': t['id'], 'item': copy.deepcopy(i)} for t in self.turns for i in t['items']]}
        if method == 'thread/queue/list': return {'data': copy.deepcopy(self.pending)}
        if method == 'thread/queue/add':
            item = {'id': 'queue1', 'clientUserMessageId': params['clientUserMessageId'], 'input': params['input']}
            self.pending.append(item)
            return {'queuedSubmission': item}
        if method in {'thread/resume', 'thread/queue/delete'}:
            with sqlite3.connect(self.config.state_dir / 'journal.sqlite3') as db:
                table = 'unified_adoptions' if method == 'thread/resume' else 'native_queue_deletions'
                assert db.execute(f"SELECT state FROM {table}").fetchone()[0] == 'planned'
        if method == 'thread/queue/delete':
            if self.failure == 'false_ack': return {'deleted': False}
            if self.failure != 'retained':
                self.pending = [q for q in self.pending if q['id'] != params['queuedSubmissionId']]
            if self.failure == 'lost_ack': raise Uncertain('synthetic lost delete acknowledgement')
            if self.failure == 'false_ack_removed': return {'deleted': False}
            if self.failure == 'changed_history': self.turns[0]['items'][0]['content'] = []
            return {'deleted': True}
        if method == 'thread/resume':
            if self.failure == 'changed_history': self.turns[0]['items'][0]['content'] = []
            if self.failure == 'resume_ack': raise Uncertain('synthetic lost resume acknowledgement')
            return {'thread': {'id': THREAD}, 'cwd': self.config.cwd, 'model': 'fixture',
                    'approvalPolicy': self.config.approval_policy, 'sandbox': {'type': 'workspaceWrite'}, **self.settings}
        if method == 'mcpServerStatus/list':
            servers = self.config.trusted_overrides()['mcp_servers']
            return {'data': [{'name': n, 'tools': dict.fromkeys(d['enabled_tools'], {})} for n, d in servers.items()]
                    + [{'name': 'amap_fleet', 'tools': {'activity': {}}}]}
        raise AssertionError(method)


@pytest.fixture
def case(tmp_path, monkeypatch):
    previous = config_for(tmp_path)
    trusted = tmp_path / 'trusted.toml'
    servers = {n: {'command': '/unused', 'required': True, 'enabled_tools': tools} for n, tools in [
        ('inbox', ['list_messages', 'read_message', 'read_attachment']),
        ('delegation', ['list_messages', 'read_message', 'read_attachment']),
        ('inbox_submit', ['submit', 'submit_result', 'peers'])]}
    trusted.write_text('mcp_servers=' + toml_value(servers))
    target = replace(previous, conversation_mode='unified', unified_argv=['codex'], trusted_config_file=trusted)
    native = Native(target)
    class Service:
        def __init__(self, config, path, **kwargs):
            self.ownership, self.journal = Ownership(config), None
            async def start(): return native
            self.backend = SimpleNamespace(start=start)
        async def close(self):
            try:
                if self.journal: self.journal.close()
            finally: self.ownership.release()
    monkeypatch.setattr(maintenance, 'UnifiedService', Service)
    return previous, target, native


def prepare(config, event=False, *, maintenance=False):
    j = Journal(config.state_dir, config.instance_id, config.fingerprint(), config.codex_version, config.codex_model,
                maintenance=maintenance)
    j.bind_thread(THREAD)
    if event:
        j.admit(SimpleNamespace(state='pending', lane='mail', notice_id='notice1', artifact_hash='hash',
            event_id='event1', payload={'event_id': 'event1', 'lane': 'mail', 'notice_id': 'notice1'}, detail=''))
    return j


def test_adoption_retains_original_history_and_commits_policy_without_model_input(case):
    old, target, native = case
    with prepare(old): pass
    result = asyncio.run(maintenance.adopt(target, Path('/unused'), old, THREAD, 'Operator selected history'))
    assert result['history_retained'] and not result['model_input_submitted']
    assert [m for m, _ in native.calls].count('thread/resume') == 1
    assert not any(m in {'thread/start', 'turn/start', 'thread/queue/add'} for m, _ in native.calls)
    with prepare(target) as j:
        assert j.thread_id == THREAD
        assert j.db.execute('SELECT policy_hash FROM unified_binding').fetchone()[0]
        assert j.db.execute("SELECT 1 FROM audit WHERE action='unified_history_adopted'").fetchone()


@pytest.mark.parametrize('field,value', [('instance_id', 'foreign'), ('self_address', 'foreign@example.invalid'),
    ('cwd', '/foreign'), ('state_dir', Path('/foreign')), ('lanes', []), ('outcome_dir', Path('/foreign')),
    ('conversation_mode', 'separate')])
def test_adoption_never_moves_delivery_namespace(case, field, value):
    old, target, _ = case
    with pytest.raises(ValueError): maintenance.same_namespace(old, replace(target, **{field: value}))


@pytest.mark.parametrize('failure', ['queue', 'active', 'changed_history', 'settings', 'event', 'fingerprint', 'missing', 'owner'])
def test_adoption_refuses_unresolved_work_drift_or_unavailable_evidence(case, failure):
    old, target, native = case
    if failure != 'missing':
        with prepare(old, event=failure == 'event') as j:
            if failure == 'event': j.hold('event1', 'unresolved')
    if failure == 'queue': native.pending = [{'id': 'human_queue'}]
    if failure == 'active': native.turns[0]['status'] = 'inProgress'
    if failure == 'changed_history': native.failure = failure
    if failure == 'settings': native.settings = {'approvalPolicy': 'never-different'}
    if failure == 'fingerprint': old = replace(old, sandbox='danger-full-access')
    owner = Ownership(target) if failure == 'owner' else None
    if owner: owner.acquire()
    try:
        with pytest.raises((ValueError, RuntimeError)):
            asyncio.run(maintenance.adopt(target, Path('/unused'), old, THREAD, 'Explicit selection'))
    finally:
        if owner: owner.release()
    if failure in {'queue', 'active', 'event', 'fingerprint', 'missing', 'owner'}:
        assert not any(m == 'thread/resume' for m, _ in native.calls)
    if failure != 'missing':
        from amap_codex.cli import read_status
        assert read_status(target)['fingerprint'] != target.fingerprint()


def queued_case(case):
    _, target, native = case
    with prepare(target, event=True) as j:
        asyncio.run(NativeQueue(j, native, 'generation').dispatch('event1'))
    return target, native


def test_cancel_before_resume_then_audited_handled(case):
    target, native = queued_case(case)
    result = asyncio.run(maintenance.dispose(target, Path('/unused'), 'event1', 'handled', 'Cancelled intentionally', cancel_queued=True))
    assert result['native_input_cancelled'] and not result['model_input_submitted']
    assert native.pending == [] and not any(m == 'thread/resume' for m, _ in native.calls)
    with prepare(target) as j:
        assert j.event('event1')['execution_status'] == 'operator_handled'
        assert j.db.execute('SELECT state FROM native_queue_deletions').fetchone()[0] == 'confirmed'
        assert j.db.execute("SELECT 1 FROM audit WHERE action='unified_handled'").fetchone()


@pytest.mark.parametrize('failure', ['no_cancel', 'duplicate', 'association', 'false_ack', 'false_ack_removed', 'retained', 'changed_history'])
def test_disposition_refuses_ambiguous_native_deletion(case, failure):
    target, native = queued_case(case)
    if failure == 'duplicate': native.pending.append(copy.deepcopy(native.pending[0]))
    elif failure == 'association': native.pending[0]['id'] = 'foreign_queue'
    else: native.failure = failure
    with pytest.raises((IntegrityConflict, StateConflict)):
        asyncio.run(maintenance.dispose(target, Path('/unused'), 'event1', 'handled', 'Operator disposition',
                                       cancel_queued=failure != 'no_cancel'))
    with prepare(target, maintenance=True) as j: assert j.event('event1')['state'] == 'queued'
    assert not any(m == 'thread/resume' for m, _ in native.calls)


@pytest.mark.parametrize('changed', [False, True])
def test_lost_delete_ack_requires_explicit_reobservation_without_resubmission(case, changed):
    target, native = queued_case(case)
    native.failure = 'lost_ack'
    with pytest.raises(Uncertain):
        asyncio.run(maintenance.dispose(target, Path('/unused'), 'event1', 'handled', 'Cancel', cancel_queued=True))
    assert not native.pending
    with pytest.raises(StateConflict): prepare(target)
    with prepare(target, maintenance=True) as j:
        assert j.event('event1')['state'] == 'queued'
        assert j.db.execute('SELECT state FROM native_queue_deletions').fetchone()[0] == 'planned'
    with pytest.raises(IntegrityConflict):
        asyncio.run(maintenance.dispose(target, Path('/unused'), 'event1', 'handled', 'Missing cancellation consent'))
    if changed: native.turns[0]['items'][0]['content'] = []
    if changed:
        with pytest.raises(IntegrityConflict):
            asyncio.run(maintenance.dispose(target, Path('/unused'), 'event1', 'handled', 'Reobserve', cancel_queued=True))
    else:
        result = asyncio.run(maintenance.dispose(target, Path('/unused'), 'event1', 'handled', 'Reobserve', cancel_queued=True))
        assert result['audited']
    assert [m for m, _ in native.calls].count('thread/queue/delete') == 1
    assert [m for m, _ in native.calls].count('thread/queue/add') == 1
    assert not any(m == 'thread/resume' for m, _ in native.calls)


def test_operator_hold_is_sticky_and_never_becomes_native_retry(case):
    target, native = queued_case(case)
    asyncio.run(maintenance.dispose(target, Path('/unused'), 'event1', 'hold', 'Needs investigation', cancel_queued=True))
    with prepare(target) as j:
        assert j.event('event1')['state'] == 'uncertain'
        assert j.db.execute('SELECT blocked FROM native_queue_intents').fetchone()[0] == 1
        clean, _, _ = asyncio.run(NativeQueue(j, native, 'generation2').reconcile())
        assert not clean and [m for m, _ in native.calls].count('thread/queue/add') == 1
    with pytest.raises(ValueError):
        asyncio.run(maintenance.dispose(target, Path('/unused'), 'event1', 'retry', 'No automatic retry'))


@pytest.mark.parametrize('changed', [False, True])
def test_interrupted_adoption_blocks_normal_start_and_migration_until_explicit_resolution(case, changed):
    from amap_codex.journal import migrate
    old, target, native = case
    with prepare(old): pass
    native.failure = 'resume_ack'
    with pytest.raises(Uncertain):
        asyncio.run(maintenance.adopt(target, Path('/unused'), old, THREAD, 'Explicit adoption'))
    with pytest.raises(StateConflict): prepare(old)
    with pytest.raises(StateConflict):
        migrate(old.state_dir, old.instance_id, target.fingerprint(), 'Cannot bypass planned maintenance')
    if changed: native.turns[0]['items'][0]['content'] = []
    native.failure = None
    if changed:
        with pytest.raises(IntegrityConflict):
            asyncio.run(maintenance.adopt(target, Path('/unused'), old, THREAD, 'Reconcile adoption'))
        with pytest.raises(StateConflict): prepare(old)
    else:
        result = asyncio.run(maintenance.adopt(target, Path('/unused'), old, THREAD, 'Reconcile adoption'))
        assert result['history_retained']
        with prepare(target): pass
    assert [m for m, _ in native.calls].count('thread/resume') == (1 if changed else 2)


@pytest.mark.parametrize('note', ['', ' ', None, 'x' * 501])
def test_maintenance_requires_bounded_explicit_operator_note(note):
    with pytest.raises(ValueError): maintenance.note_required(note)


def test_adoption_requires_explicit_uuid_and_same_local_execution_domain(case):
    old, target, native = case
    with pytest.raises(ValueError):
        asyncio.run(maintenance.adopt(target, Path('/unused'), old, '--last', 'Explicit selection'))
    with pytest.raises(ValueError): maintenance.same_namespace(replace(old, launcher_control_argv=['remote']), target)
    assert not native.calls
