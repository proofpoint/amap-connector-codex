"""Harness guards, using real journal producers; no live restart is implied."""
import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from amap_codex.journal import Journal
from amap_codex.operator import PREFIX, instruction_hash
from amap_codex.supervisor import atomic_json


@pytest.fixture
def probe_module():
    path = Path(__file__).parent/'integration/restart_probe.py'
    spec = importlib.util.spec_from_file_location('restart_probe', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture(module, tmp_path, monkeypatch, fault=None, process_fault=True):
    import subprocess
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout='codex-cli 0.161.0\n'))
    root = tmp_path/'probe'
    module.prepare_files(root, 'codex', None, 'workspace-write')
    config = module.configuration(root)
    config.poll_interval_ms = 0  # No wall-clock wait in a harness unit test.
    monkeypatch.setattr(module, 'configuration', lambda path: config)
    request = {'run_id': 'restart-probe', 'instructions': 'synthetic operator input'}
    with Journal(config.state_dir, config.instance_id, config.fingerprint(),
                 config.codex_version, config.codex_model) as journal:
        journal.bind_thread('thread-original')
        journal.admit_operator(request['run_id'], instruction_hash(request))
        journal.begin_operator(request['run_id'], 41)
        journal.operator_result(request['run_id'], 'accepted', turn_id='turn-original')
        if fault != 'still_active':
            journal.operator_result(request['run_id'], 'finished', execution_status='interrupted')
    checkpoint = {'thread_id': 'thread-original', 'turn_id': 'turn-original', 'run_id': request['run_id'],
                  'instruction_hash': instruction_hash(request), 'fingerprint': config.fingerprint(),
                  'process_fault': process_fault}
    if fault == 'fingerprint':
        checkpoint['fingerprint'] = 'another-configuration'
    if fault == 'thread':
        checkpoint['thread_id'] = 'another-thread'
    atomic_json(root/'checkpoint.json', checkpoint)
    (root/'executions').write_text('entered\n' * (2 if fault == 'duplicate_command' else 1))
    turn = {'id': 'turn-original', 'status': 'interrupted', 'items': [{'type': 'userMessage',
            'content': [{'type': 'text', 'text': PREFIX + json.dumps(request)}]}]}
    if fault == 'different_input':
        turn['items'][0]['content'][0]['text'] = PREFIX + json.dumps({**request, 'instructions': 'changed'})
    if fault == 'submission':
        turn['items'].append({'type': 'mcpToolCall', 'tool': 'submit', 'status': 'completed'})
    turns = [] if fault == 'missing_history' else [turn]
    if fault == 'duplicate_turn':
        turns.append({**turn, 'id': 'another-turn'})

    class Supervisor:
        def __init__(self, config):
            self.journal = Journal(config.state_dir, config.instance_id, config.fingerprint(),
                                   config.codex_version, config.codex_model)
            self.delivery = SimpleNamespace(client=SimpleNamespace(turn_starts=1 if fault == 'redispatch' else 0),
                                            observe=self.observe)
        async def start(self): pass
        async def tick(self): pass
        async def observe(self): return {'thread': {'turns': turns}}
        async def close(self): self.journal.close()

    monkeypatch.setattr(module, 'Supervisor', Supervisor)
    return root


def test_restart_probe_keeps_process_fault_evidence_scoped(probe_module, tmp_path, monkeypatch, capsys):
    root = fixture(probe_module, tmp_path, monkeypatch)
    output = tmp_path/'evidence.json'
    asyncio.run(probe_module.verify(root, output, None, None))
    evidence = json.loads(output.read_text())
    assert evidence['passed'] and not evidence['runtime_identity_changed']
    assert 'no sandbox restart' in evidence['scope']
    assert evidence['new_turns_dispatched'] == 0
    assert str(root) not in output.read_text() + capsys.readouterr().out


@pytest.mark.parametrize('fault', ['fingerprint', 'thread', 'different_input', 'missing_history',
                                 'duplicate_turn', 'duplicate_command', 'redispatch', 'still_active', 'submission'])
def test_restart_probe_cannot_pass_a_broken_recovery(probe_module, tmp_path, monkeypatch, fault):
    root = fixture(probe_module, tmp_path, monkeypatch, fault)
    output = tmp_path/'evidence.json'
    with pytest.raises(RuntimeError):
        asyncio.run(probe_module.verify(root, output, None, None))
    if output.exists():
        assert not json.loads(output.read_text())['passed']


@pytest.mark.parametrize('before,after', [(None, None), ('same-runtime', 'same-runtime'), (None, 'new-runtime')])
def test_sandbox_restart_evidence_requires_distinct_operator_runtime_identities(probe_module, tmp_path, monkeypatch, before, after):
    root = fixture(probe_module, tmp_path, monkeypatch, process_fault=False)
    with pytest.raises(RuntimeError, match='distinct runtime identities'):
        asyncio.run(probe_module.verify(root, tmp_path/'evidence.json', before, after))
    assert not (root/'release').exists()


def test_restart_probe_refuses_an_existing_namespace(probe_module, tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout='codex-cli 0.161.0\n'))
    marker = tmp_path/'preserved'
    marker.write_text('existing operational state')
    with pytest.raises(FileExistsError):
        probe_module.prepare_files(tmp_path, 'codex', None, 'workspace-write')
    assert marker.read_text() == 'existing operational state'


def test_prepare_fails_when_real_journal_producer_finishes_before_the_command(probe_module, tmp_path, monkeypatch):
    import subprocess
    from amap_codex.operator import read_request
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout='codex-cli 0.161.0\n'))
    times = iter([0, 0, 1000])
    # Keep asyncio's clock independent from the probe's injected deadline.
    monkeypatch.setattr(probe_module, 'time', SimpleNamespace(monotonic=lambda: next(times, 1000)))
    class FinishedSupervisor:
        def __init__(self, config):
            self.config = config
            self.journal = Journal(config.state_dir, config.instance_id, config.fingerprint(),
                                   config.codex_version, config.codex_model)
        async def start(self):
            request = read_request(self.config.state_dir/'operator-queue/restart-probe.json')
            self.journal.bind_thread('thread-fixture')
            self.journal.admit_operator(request['run_id'], instruction_hash(request))
            self.journal.begin_operator(request['run_id'], 41)
            self.journal.operator_result(request['run_id'], 'accepted', turn_id='turn-fixture')
            self.journal.operator_result(request['run_id'], 'finished', execution_status='completed')
        async def tick(self): pass
        async def close(self): self.journal.close()
    monkeypatch.setattr(probe_module, 'Supervisor', FinishedSupervisor)
    with pytest.raises(RuntimeError, match='before the workspace command entered'):
        asyncio.run(probe_module.prepare(tmp_path/'probe', 'codex', None, 'workspace-write', False))
