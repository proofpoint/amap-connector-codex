import copy

import pytest

from amap_codex.attach_gate import AttachPolicy
from amap_codex.local_socket import ProtocolError


def policy():
    return AttachPolicy('canonical', cwd='/workspace', model='selected', sandbox_name='read-only',
                        sandbox={'type': 'readOnly'}, approval_policy='on-request', roots=['/workspace'])


@pytest.mark.parametrize('method,params', [
    ('turn/start', {'threadId': 'canonical', 'input': [{'type': 'text', 'text': 'human'}]}),
    ('thread/queue/add', {'threadId': 'canonical', 'clientUserMessageId': 'human', 'input': []}),
    ('turn/interrupt', {'threadId': 'canonical', 'turnId': 'turn1'}),
    ('thread/resume', {'threadId': 'canonical', 'cwd': '/workspace', 'model': 'selected',
        'sandbox': 'read-only', 'approvalPolicy': 'on-request', 'config': None}),
    ('mcpServerStatus/list', {'threadId': 'canonical'}), ('model/list', {}),
])
def test_native_controls_preserve_canonical_thread_and_pane_settings(method, params):
    policy().validate(method, params)


@pytest.mark.parametrize('field,value', [
    ('threadId', 'foreign'), ('cwd', '/foreign'), ('model', 'another'), ('sandbox', 'danger-full-access'),
    ('sandboxPolicy', {'type': 'dangerFullAccess'}), ('approvalPolicy', 'never'),
    ('runtimeWorkspaceRoots', ['/workspace', '/foreign']), ('config', {}), ('baseInstructions', 'override'),
    ('developerInstructions', 'override'), ('history', []), ('path', '/foreign'), ('permissions', 'full'),
    ('approvalsReviewer', 'automatic'), ('environments', []), ('disabledPluginIds', []), ('toolOutput', {}),
    ('collaborationMode', {'mode': 'default', 'settings': {'model': 'foreign'}}),
    ('collaborationMode', {'mode': 'foreign', 'settings': {'model': 'selected'}}),
])
def test_native_setting_changes_cannot_bypass_managed_pane_policy(field, value):
    with pytest.raises(ProtocolError): policy().validate('thread/resume', {'threadId': 'canonical', field: value})


@pytest.mark.parametrize('method', ['thread/start', 'thread/fork', 'thread/delete', 'thread/revert',
                                  'thread/inject_items', 'config/value/write',
                                  'turn/settings/update', 'unknown/mutation'])
def test_unsupported_mutations_have_no_implicit_authority(method):
    with pytest.raises(ProtocolError): policy().validate(method, {'threadId': 'canonical'})


def test_thread_read_requires_exact_binding_even_on_a_readonly_surface():
    with pytest.raises(ProtocolError): policy().validate('thread/read', {})
    with pytest.raises(ProtocolError): policy().validate('mcpServerStatus/list', {'threadId': 'foreign'})


def test_native_mode_uses_backend_builtin_instructions_and_keeps_caller_data_unchanged():
    params = {'threadId': 'canonical', 'collaborationMode': {'mode': 'default', 'settings': {
        'model': 'selected', 'reasoning_effort': 'high', 'developer_instructions': 'caller-provided text'}}}
    result = policy().normalized('thread/settings/update', params)
    assert result['collaborationMode']['settings']['developer_instructions'] is None
    assert result['collaborationMode']['settings']['reasoning_effort'] == 'high'
    assert params['collaborationMode']['settings']['developer_instructions'] == 'caller-provided text'
