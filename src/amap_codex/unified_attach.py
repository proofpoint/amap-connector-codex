"""Read-only bounded attachment preparation; never starts or recovers a backend."""
import json
from pathlib import Path
import re
import time

from .interactive import SESSION_ID, native_interactive_args
from .managed_backend import private_directory, private_socket
from .spool import _read


def descriptor(config):
    from .cli import read_status
    private_directory(config.state_dir)
    raw, _ = _read(config.state_dir, 'unified.json')
    if len(raw) > 65536:
        raise ValueError('unified descriptor exceeded bound')
    value = json.loads(raw)
    if (not isinstance(value, dict) or type(value.get('schema_version')) is not int
            or value['schema_version'] != 1 or value.get('mode') != 'unified'
            or value.get('instance_id') != config.instance_id or value.get('fingerprint') != config.fingerprint()
            or value.get('codex_version') != config.codex_version):
        raise ValueError('unified instance, mode, schema or configuration does not match')
    required = {'backend_live', 'canonical_binding', 'configuration_matches', 'protected_tools',
                'native_reconciled', 'publication_resolved'}
    readiness = value.get('readiness')
    checks = readiness.get('checks') if isinstance(readiness, dict) else None
    if not isinstance(readiness, dict) or readiness.get('state') != 'ready':
        facts = checks if isinstance(checks, dict) else {}
        detail = ', '.join(k + '=' + (facts[k] if facts.get(k) in {'fail', 'unknown'} else 'unknown')
                           for k in sorted(required) if facts.get(k) != 'pass')
        raise ValueError('unified service is holding or stopped' + (': ' + detail if detail else ''))
    if not isinstance(checks, dict) or not required <= checks.keys() or any(v != 'pass' for v in checks.values()):
        raise ValueError('unified readiness has missing, failed or unknown facts')
    effective = value.get('effective_settings')
    if (not isinstance(effective, dict) or not isinstance(effective.get('model'), str) or not effective['model']
            or (config.codex_model is not None and effective['model'] != config.codex_model)
            or effective.get('approval_policy') != config.approval_policy
            or effective.get('sandbox', {}).get('type') != {
                'read-only': 'readOnly', 'workspace-write': 'workspaceWrite', 'danger-full-access': 'dangerFullAccess'}[config.sandbox]):
        raise ValueError('unified descriptor differs from configured model or permissions')
    stamp = value.get('updated_at')
    if type(stamp) not in {int, float} or not 0 <= time.time() - stamp <= max(10, config.poll_interval_ms / 1000 * 3):
        raise ValueError('unified descriptor has no fresh service heartbeat')
    generation, thread = value.get('backend_generation'), value.get('canonical_thread_id')
    if (not isinstance(generation, str) or not re.fullmatch('[a-f0-9]{32}', generation)
            or not isinstance(thread, str) or not SESSION_ID.fullmatch(thread)):
        raise ValueError('unified canonical generation or thread is invalid')
    validation = value.get('protected_tools_validation')
    if (not isinstance(validation, dict) or validation.get('scope') != 'startup'
            or validation.get('backend_generation') != generation
            or type(validation.get('validated_at')) not in {int, float}
            or not 0 < validation['validated_at'] <= stamp
            or value.get('heartbeat_max_age_seconds') != max(10, config.poll_interval_ms / 1000 * 3)
            or value.get('scheduler_state') not in {'idle', 'active'}):
        raise ValueError('unified startup validation, freshness bound or scheduler state is unavailable or mismatched')
    status = read_status(config)
    if status.get('thread_id') != thread or status.get('fingerprint') != value['fingerprint']:
        raise ValueError('unified descriptor differs from the durable journal binding')
    endpoint = config.state_dir / ('u-' + generation) / 't'
    if value.get('endpoint') != 'unix://' + str(endpoint):
        raise ValueError('unified endpoint differs from its registered generation')
    private_socket(endpoint)
    return value


def attachment_argv(config, *, native_argv=None, binary=None, wait=30):
    if type(wait) not in {int, float} or not 0 <= wait <= 60:
        raise ValueError('attachment wait must be between zero and sixty seconds')
    deadline = time.monotonic() + wait
    while True:
        try:
            value = descriptor(config)
            break
        except (OSError, ValueError) as exc:
            if time.monotonic() >= deadline:
                raise ValueError('unified connector is not ready: ' + str(exc)[:256] + '; no fallback was started') from None
            time.sleep(min(.1, max(0, deadline - time.monotonic())))
    if type(value.get('attached_client_count')) is not int or value['attached_client_count'] != 0:
        raise ValueError('the canonical conversation already has a writable terminal client')
    if binary is not None and native_argv is not None:
        raise ValueError('use either binary or native argv')
    supplied = native_argv or [binary or config.unified_argv[0]]
    executable, options, prompt, preflight, cwd, resume = native_interactive_args(supplied, config.cwd)
    if executable != config.unified_argv[0] or preflight:
        raise ValueError('native executable/configuration must match protected unified_argv')
    if Path(cwd).resolve() != Path(config.cwd).resolve():
        raise ValueError('attachment workspace differs from the canonical workspace')
    if resume and resume[0] != '--last' and resume[0].lower() != value['canonical_thread_id'].lower():
        raise ValueError('unified resume must name the canonical conversation; adoption is explicit')
    retained = []
    index = 0
    while index < len(options):
        token = options[index]
        if token in {'resume', '--no-daemon'} or (resume and index == resume[1]):
            index += 1
            continue
        if token in {'--no-alt-screen', '--strict-config'}:
            retained.append(token)
            index += 1
            continue
        if token == '--dangerously-bypass-approvals-and-sandbox':
            if (config.sandbox, config.approval_policy) != ('danger-full-access', 'never'):
                raise ValueError('native full-access flag differs from managed pane permissions')
            index += 1
            continue
        name, equals, option_value = token.partition('=')
        if not token.startswith('--') and len(token) > 2 and token[:2] in {'-m', '-s', '-a', '-C', '-i'}:
            name, equals, option_value = token[:2], '=', token[2:].removeprefix('=')
        if name not in {'-m', '--model', '-s', '--sandbox', '-a', '--ask-for-approval', '-C', '--cd', '-i', '--image'}:
            raise ValueError('native control must be configured on the managed backend before attachment')
        start = index
        if not equals:
            index += 1
            option_value = options[index]
        expected = (value['effective_settings']['model'] if name in {'-m', '--model'} else
                    config.sandbox if name in {'-s', '--sandbox'} else
                    config.approval_policy if name in {'-a', '--ask-for-approval'} else None)
        if expected is not None and option_value != expected:
            raise ValueError('native model or permissions differ from the managed pane configuration')
        if expected is None:
            retained.extend(options[start:index+1])
        index += 1
    # Native remote resume rejects CLI permission overrides. Validate supplied
    # flags above, then inherit the verified backend policy; do not forward
    # backend-only configuration into the terminal process.
    return [executable, *retained, '--model', value['effective_settings']['model'], '--remote', value['endpoint'],
            'resume', value['canonical_thread_id'], *prompt]
