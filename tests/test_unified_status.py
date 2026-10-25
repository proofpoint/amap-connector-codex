import asyncio
import copy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import time

import jsonschema
import pytest

from amap_codex.cli import read_status
from amap_codex.journal import Journal
from amap_codex.native_queue import QueueLedger
from amap_codex.unified import UnifiedService
from test_cli import configured

SCHEMA = json.loads((Path(__file__).resolve().parents[1] / 'docs/status.schema.json').read_text())


@pytest.fixture
def runtime(tmp_path):
    config, _ = configured(tmp_path)
    config = replace(config, conversation_mode='unified', unified_argv=['codex'])
    service = object.__new__(UnifiedService)
    service.config = config
    service.journal = Journal(config.state_dir, config.instance_id, config.fingerprint(), config.codex_version, config.codex_model)
    service.journal.bind_thread('11111111-1111-4111-8111-111111111111')
    QueueLedger(service.journal)
    service.backend = SimpleNamespace(generation='a'*32, front_path=config.state_dir / ('u-'+'a'*32) / 't',
        process=SimpleNamespace(returncode=None, pid=123), client=SimpleNamespace(blocked=[]))
    service.gate = SimpleNamespace(client_count=0)
    service.effective = {'model': 'fixture', 'sandbox': {'type': 'workspaceWrite'}, 'approval_policy': 'never'}
    service.tools_validation = {'scope': 'startup', 'backend_generation': 'a'*32, 'validated_at': time.time()}
    history = {'thread': {'id': service.journal.thread_id, 'status': {'type': 'idle'}, 'turns': []}}
    try: yield service, history
    finally: service.journal.close()


def test_public_status_schema_both_modes_and_unified_generation_fact(runtime, tmp_path):
    service, history = runtime
    service.write_status(True, history, [])
    assert service.status['readiness']['state'] == 'ready'
    assert service.status['protected_tools_validation']['backend_generation'] == service.status['backend_generation']
    jsonschema.validate(service.status, SCHEMA)
    published = read_status(service.config)
    assert published['protected_tools_validation'] == service.tools_validation
    jsonschema.validate(published, SCHEMA)
    # A never-started instance still declares the format without a false ready.
    separate = replace(service.config, conversation_mode='separate', unified_argv=None, state_dir=tmp_path / 'new')
    jsonschema.validate(read_status(separate), SCHEMA)


@pytest.mark.parametrize('validation', [None, {'scope': 'startup', 'backend_generation': 'foreign'}])
def test_startup_tool_validation_is_measured_for_this_generation(runtime, validation):
    service, history = runtime
    service.tools_validation = validation
    service.write_status(True, history, [])
    assert service.status['readiness']['state'] == 'holding'
    assert service.status['readiness']['checks']['protected_tools'] == 'fail'


def test_unknown_native_state_holds_new_dispatch_and_never_publishes_open_vocabulary(runtime):
    service, history = runtime
    history['thread']['status'] = {'type': 'futureUnknownState'}
    async def reconcile(): return True, history, []
    service.queue = SimpleNamespace(reconcile=reconcile)
    clean, h, pending = asyncio.run(service.observe())
    assert not clean
    service.write_status(True, h, pending)
    assert service.status['scheduler_state'] == 'unknown'
    assert service.status['readiness']['state'] == 'holding'
    jsonschema.validate(service.status, SCHEMA)


@pytest.mark.parametrize('change', ['missing_check', 'unknown_state', 'missing_validation'])
def test_schema_does_not_accept_incomplete_ready_status(runtime, change):
    service, history = runtime
    service.write_status(True, history, [])
    status = copy.deepcopy(service.status)
    if change == 'missing_check': status['readiness']['checks'].pop('protected_tools')
    elif change == 'unknown_state': status['scheduler_state'] = 'unknown'
    else: status.pop('protected_tools_validation')
    with pytest.raises(jsonschema.ValidationError): jsonschema.validate(status, SCHEMA)
