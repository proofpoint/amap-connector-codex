import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from amap_codex.unified_policy import workspace_matches
from amap_codex import unified
from test_supervisor import config_for


@pytest.mark.parametrize('cwd', [None, '', '.', 'relative', '   ', 7, {}, '/foreign'])
def test_native_workspace_cannot_default_to_the_local_process_directory(tmp_path, monkeypatch, cwd):
    monkeypatch.chdir(tmp_path)
    assert not workspace_matches(cwd, str(tmp_path))
    assert workspace_matches(str(tmp_path), str(tmp_path))


@pytest.mark.parametrize('cwd', ['present', None, '', '.', 'relative', '/foreign'])
def test_service_validates_explicit_workspace_before_binding(tmp_path, monkeypatch, cwd):
    monkeypatch.chdir(tmp_path)
    config = replace(config_for(tmp_path), cwd=str(tmp_path), conversation_mode='unified', unified_argv=['codex'])
    thread = '11111111-1111-4111-8111-111111111111'
    calls = []
    class Client:
        closed = False
        blocked = []
        async def request(self, method, params):
            calls.append(method)
            if method == 'config/read': return {'config': {'model_reasoning_effort': 'high'}}
            if method == 'thread/start':
                result = {'thread': {'id': thread}, 'model': 'fixture', 'approvalPolicy': config.approval_policy,
                          'sandbox': {'type': 'workspaceWrite'}}
                if cwd is not None: result['cwd'] = config.cwd if cwd == 'present' else cwd
                return result
            if method == 'thread/name/set': return {}
            if method == 'thread/read': return {'thread': {'id': thread, 'status': {'type': 'idle'}}}
            if method in {'thread/turns/list', 'thread/items/list', 'thread/queue/list'}: return {'data': []}
            raise AssertionError(method)
    class Backend:
        def __init__(self, *args, **kwargs):
            self.client, self.generation = Client(), 'a'*32
            self.front_path = config.state_dir / 'fake-front'
            self.process = SimpleNamespace(returncode=None, pid=123)
        async def start(self): return self.client
        async def stop(self): self.process.returncode = 0
    class Gate:
        def __init__(self, backend, policy): self.client_count, self.policy = 0, policy
        async def start(self): pass
        async def close(self): pass
    async def resolve(*args): return {}, {}, 'policy'
    async def tools(*args): pass
    monkeypatch.setattr(unified, 'ManagedBackend', Backend)
    monkeypatch.setattr(unified, 'AttachGate', Gate)
    monkeypatch.setattr(unified, 'resolve', resolve)
    monkeypatch.setattr(unified, 'validate_tools', tools)
    async def check():
        service = unified.UnifiedService(config, tmp_path / 'config.toml')
        if cwd != 'present':
            with pytest.raises(ValueError): await service.start()
            from amap_codex.cli import read_status
            assert read_status(config)['thread_id'] is None
        else:
            try:
                await service.start()
                assert service.journal.thread_id == thread
                assert service.effective['reasoning_effort'] == 'high'
                assert service.gate.policy.reasoning_effort == 'high'
            finally:
                await service.close()
        assert 'turn/start' not in calls and 'thread/queue/add' not in calls
    asyncio.run(check())
