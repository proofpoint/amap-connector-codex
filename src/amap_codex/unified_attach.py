"""Read-only bounded attachment preparation; never starts or recovers a backend."""
import json
from pathlib import Path
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
    from .unified_readiness import validate_ready
    endpoint = validate_ready(config, value, read_status(config))
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
