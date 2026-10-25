from dataclasses import replace
import json
from pathlib import Path
import socket
import tempfile
import time

import pytest

from amap_codex.interactive import toml_value
from amap_codex.journal import Journal
from amap_codex.unified_attach import attachment_argv, descriptor
from test_supervisor import config_for

THREAD = '11111111-1111-4111-8111-111111111111'


@pytest.fixture
def attachment():
    with tempfile.TemporaryDirectory(prefix='amap-attach-') as directory:
        root = Path(directory).resolve()
        c = config_for(root)
        servers = {name: {'command': '/unused', 'required': True, 'enabled_tools': tools} for name, tools in [
            ('inbox', ['list_messages', 'read_message', 'read_attachment']),
            ('delegation', ['list_messages', 'read_message', 'read_attachment']),
            ('inbox_submit', ['submit', 'submit_result', 'peers'])]}
        trusted = root / 'trusted.toml'
        trusted.write_text('mcp_servers=' + toml_value(servers))
        c = replace(c, conversation_mode='unified', unified_argv=['codex'], trusted_config_file=trusted)
        with Journal(c.state_dir, c.instance_id, c.fingerprint(), c.codex_version, c.codex_model) as j:
            j.bind_thread(THREAD)
        generation = 'a' * 32
        parent = c.state_dir / ('u-' + generation)
        parent.mkdir(mode=0o700)
        path = parent / 't'
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(path))
        listener.listen()
        path.chmod(0o600)
        value = {'schema_version': 1, 'mode': 'unified', 'instance_id': c.instance_id,
            'fingerprint': c.fingerprint(), 'canonical_thread_id': THREAD, 'backend_generation': generation,
            'endpoint': 'unix://' + str(path), 'updated_at': time.time(), 'attached_client_count': 0,
            'codex_version': c.codex_version,
            'effective_settings': {'model': 'fixture', 'approval_policy': c.approval_policy,
                                   'sandbox': {'type': 'workspaceWrite'}},
            'readiness': {'state': 'ready', 'checks': dict.fromkeys(
                ['backend_live', 'canonical_binding', 'configuration_matches', 'protected_tools',
                 'native_reconciled', 'publication_resolved'], 'pass')}}
        def write(): (c.state_dir / 'unified.json').write_text(json.dumps(value))
        write()
        try: yield c, value, write
        finally: listener.close()


def test_native_fresh_and_last_attach_only_the_registered_generation(attachment):
    c, value, _ = attachment
    for native in (['codex'], ['codex', 'resume', '--last'], ['codex', 'resume', THREAD, '--no-daemon']):
        argv = attachment_argv(c, native_argv=native, wait=0)
        assert argv[-4:] == ['--remote', value['endpoint'], 'resume', THREAD]
        assert '--no-daemon' not in argv and '--last' not in argv


@pytest.mark.parametrize('change', ['schema', 'schema_bool', 'mode', 'instance', 'fingerprint', 'holding',
                                  'missing_check', 'unknown_check', 'stale', 'future', 'generation',
                                  'thread', 'endpoint', 'missing_stamp'])
def test_descriptor_never_defaults_missing_stale_or_foreign_evidence_to_ready(attachment, change):
    c, v, write = attachment
    if change == 'schema': v['schema_version'] = 2
    elif change == 'schema_bool': v['schema_version'] = True
    elif change == 'mode': v['mode'] = 'separate'
    elif change == 'instance': v['instance_id'] = 'foreign'
    elif change == 'fingerprint': v['fingerprint'] = 'foreign'
    elif change == 'holding': v['readiness']['state'] = 'holding'
    elif change == 'missing_check': v['readiness']['checks'].pop('protected_tools')
    elif change == 'unknown_check': v['readiness']['checks']['native_reconciled'] = 'unknown'
    elif change == 'stale': v['updated_at'] -= 60
    elif change == 'future': v['updated_at'] += 60
    elif change == 'generation': v['backend_generation'] = '../foreign'
    elif change == 'thread': v['canonical_thread_id'] = '22222222-2222-4222-8222-222222222222'
    elif change == 'endpoint': v['endpoint'] = 'unix:///foreign'
    else: v.pop('updated_at')
    write()
    with pytest.raises(ValueError): descriptor(c)


@pytest.mark.parametrize('native', [
    ['codex', 'resume', '22222222-2222-4222-8222-222222222222'],
    ['codex', '--model', 'foreign'], ['codex', '-mforeign'],
    ['codex', '--sandbox', 'danger-full-access'], ['codex', '-a', 'on-request'],
    ['codex', '--cd', '/foreign'], ['codex', '-c', 'developer_instructions="override"'],
    ['codex', '--dangerously-bypass-approvals-and-sandbox'], ['codex', '--search'],
    ['foreign-codex'],
])
def test_attachment_cannot_change_backend_model_permissions_workspace_or_history(attachment, native):
    c, _, _ = attachment
    with pytest.raises(ValueError): attachment_argv(c, native_argv=native, wait=0)


@pytest.mark.parametrize('count', [1, None, False])
def test_second_or_unknown_client_is_refused(attachment, count):
    c, v, write = attachment
    v['attached_client_count'] = count
    write()
    with pytest.raises(ValueError): attachment_argv(c, wait=0)
