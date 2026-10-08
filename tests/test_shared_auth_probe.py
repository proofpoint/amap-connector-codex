"""Probe failure reporting and redaction; these fixtures prove no Codex behavior."""
import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def probe_module():
    path = Path(__file__).parent/'integration/shared_auth_probe.py'
    spec = importlib.util.spec_from_file_location('shared_auth_probe', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def setup_probe(module, tmp_path, monkeypatch, fault=None):
    home = tmp_path/'private-home'
    home.mkdir()
    auth = home/'auth.json'
    auth.write_text(json.dumps({'tokens': {'access_token': 'SECRET_TOKEN'}, 'last_refresh': 'before'}))
    account = {'type': 'chatgpt', 'email': 'private@example.invalid', 'planType': 'synthetic'}
    if fault == 'apikey':
        account = {'type': 'apiKey'}
    clients = []

    class Client:
        def __init__(self, argv, **kwargs):
            self.index = len(clients)
            self.stopped = False
            self.reads = 0
            self.refresh_requests = 0
            assert kwargs['env']['CODEX_HOME'] == str(home)
            clients.append(self)

        async def start(self):
            if fault == 'startup' and self.index == 1:
                raise RuntimeError('private server diagnostic')

        async def request(self, method, params):
            if params['refreshToken']:
                self.refresh_requests += 1
                if fault == 'refresh' and self.index == 1:
                    raise RuntimeError('private server diagnostic')
                if fault != 'no_rotation':
                    auth.write_text(json.dumps({'tokens': {'access_token': 'SECRET_ROTATED'},
                                                'last_refresh': f'after-{self.index}'}))
            else:
                self.reads += 1
                if self.index == 1 and fault == 'missing_account':
                    return {'account': None}
                if self.index == 1 and fault == 'different_account':
                    return {'account': {**account, 'email': 'other@example.invalid'}}
                if self.index == 1 and self.reads > 1 and fault == 'reread':
                    return {'account': None}
            return {'account': account}

        async def stop(self):
            self.stopped = True

    monkeypatch.setattr(module, 'AppServerClient', Client)
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout='codex-cli 0.161.0\n'))
    return home, clients


@pytest.mark.parametrize('refresh', [False, True])
def test_shared_auth_probe_redacts_account_home_and_credentials(probe_module, tmp_path, monkeypatch, capsys, refresh):
    home, clients = setup_probe(probe_module, tmp_path, monkeypatch)
    output = tmp_path/'evidence.json'
    asyncio.run(probe_module.probe('codex', home, output, refresh))
    evidence = json.loads(output.read_text())
    assert evidence['refresh_requested'] is refresh
    assert evidence['both_accounts_available_after']
    if refresh:
        assert evidence['refresh_succeeded']
    text = output.read_text() + capsys.readouterr().out
    for private in ('SECRET_TOKEN', 'SECRET_ROTATED', 'private@example.invalid', str(home)):
        assert private not in text
    assert all(c.stopped for c in clients)


@pytest.mark.parametrize('fault', ['startup', 'apikey', 'missing_account', 'different_account', 'refresh', 'no_rotation', 'reread'])
def test_shared_auth_probe_never_passes_a_failed_check(probe_module, tmp_path, monkeypatch, capsys, fault):
    home, clients = setup_probe(probe_module, tmp_path, monkeypatch, fault)
    output = tmp_path/'evidence.json'
    with pytest.raises(RuntimeError):
        asyncio.run(probe_module.probe('codex', home, output, True))
    assert all(c.stopped for c in clients)
    if fault in {'startup', 'apikey', 'missing_account', 'different_account'}:
        assert not any(c.refresh_requests for c in clients)
    text = (output.read_text() if output.exists() else '') + capsys.readouterr().out
    assert 'private server diagnostic' not in text
    assert 'private@example.invalid' not in text
    if output.exists():
        evidence = json.loads(output.read_text())
        assert not evidence.get('refresh_succeeded') or not evidence['both_accounts_available_after']


def test_shared_auth_probe_refuses_a_symlinked_auth_file(probe_module, tmp_path):
    auth = tmp_path/'real.json'
    auth.write_text('{"tokens": {"access_token": "synthetic"}}')
    home = tmp_path/'home'
    home.mkdir()
    (home/'auth.json').symlink_to(auth)
    with pytest.raises(RuntimeError, match='regular existing'):
        probe_module.auth_metadata(home)


def test_shared_auth_probe_refuses_a_non_managed_auth_file(probe_module, tmp_path):
    (tmp_path/'auth.json').write_text('{"OPENAI_API_KEY": "synthetic"}')
    with pytest.raises(RuntimeError, match='managed ChatGPT'):
        probe_module.auth_metadata(tmp_path)
