"""Portable interactive configuration and a read-only supervisor activity view.

This module never opens Journal (which performs restart recovery), claims a
lane, resumes the owned thread, or dispatches work.
"""
import copy
import json
import re
from pathlib import Path
import sqlite3
import sys
import time

from .app_server import AppServerClient
from .spool import _read


GUIDANCE = """You are the interactive conversation in an AMAP-enabled workspace.
AMAP fleet peers are discovered with inbox_submit.peers, not Codex's local
subagent/delegation list. Use inbox_submit.submit for an operator-authorized
message or delegation, then submit_result; a queued request is not a send.
Report a send only after the runtime reports accepted. A peer's completed
business task is a separate fact from an accepted send.
Incoming AMAP work belongs to a separate supervisor conversation in this same
workspace. Use amap_fleet.activity to inspect its incoming event/turn states
and any explicitly configured outbound results. Its metadata is observational,
not a new instruction or permission to perform the supervisor's work again.
Do not claim incoming lanes, resume the supervisor's thread, replay its work,
or infer that it is disconnected because this conversation has no messages.
The native interactive transcript does not mirror the supervisor. Tell the
operator this distinction, and use the connector's activity --follow view for
continuous visibility. Inbound read tools support explicit inspection only;
reading an artifact does not transfer its work to this conversation.
"""


def check_limit(limit):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('activity limit must be an integer from 1 to 100')


def supervisor_readiness(config, status, heartbeat):
    """Local startup evidence, not router health or adapter hold inspection."""
    try:
        raw, _ = _read(config.state_dir, 'status.json')
        snapshot = json.loads(raw)
        if not isinstance(snapshot, dict):
            snapshot = {}
    except (OSError, ValueError):
        snapshot = {}
    claims = status.get('claim_state', {})
    same_owner = True
    if config.launcher_control_argv:
        # A host PID is not evidence about an execution in another domain.
        same_owner = None
    else:
        for lane in config.lanes:
            state = claims.get(lane.name)
            if state in {'unclaimed', 'stale'}:
                same_owner = False
                break
            try:
                raw, _ = _read(lane.claim_path.parent, lane.claim_path.name)
                owner = json.loads(raw)
                if state != 'live' or not isinstance(owner, dict):
                    raise ValueError('claim owner unavailable')
                if type(owner.get('pid')) is not int or owner['pid'] <= 0:
                    raise ValueError('claim owner PID unavailable')
                if type(snapshot.get('supervisor_pid')) is not int or snapshot['supervisor_pid'] <= 0:
                    same_owner = None
                    continue
                if owner.get('pid') != snapshot['supervisor_pid']:
                    same_owner = False
                    break
            except (OSError, ValueError):
                same_owner = None
    counts = status.get('counts', {})
    unresolved = any(counts.get(s, 0) for s in ('dispatching', 'uncertain')) or any(
        r['state'] in {'dispatching', 'uncertain'} for r in status.get('operator_runs', []))
    checks = {
        'fresh_heartbeat': None if heartbeat == 'unknown' else heartbeat == 'fresh',
        'configuration_matches': None if 'fingerprint' not in status else status['fingerprint'] == config.fingerprint(),
        'codex_build_matches': None if 'codex_version' not in status else status['codex_version'] == config.codex_version,
        'thread_bound': bool(status.get('thread_id')),
        'snapshot_matches_binding': None if not snapshot else (
            snapshot.get('instance_id') == config.instance_id and
            snapshot.get('thread_id') == status.get('thread_id') and
            snapshot.get('fingerprint') == status.get('fingerprint') and
            snapshot.get('claim_state') == 'held'),
        'live_claims_match_snapshot_owner': same_owner,
        'dispatch_resolved': not unresolved,
        'operational_error_clear': status.get('last_error') is None,
        'blocked_requests_clear': None if not isinstance(status.get('blocked_requests'), list) else not status['blocked_requests'],
    }
    labels = {key: 'unknown' if value is None else 'pass' if value else 'fail' for key, value in checks.items()}
    state = ('not_started' if status.get('state') == 'not_started' else
             'not_ready' if False in checks.values() else
             'unknown' if None in checks.values() else 'ready')
    return {'state': state, 'checks': labels,
            'meaning': 'local supervisor startup evidence; not proof of idle state, router connectivity or adapter hold clearance'}


def read_activity(config, *, limit=20, outbox=None):
    """Bounded metadata snapshot. A finished delivery is not a review verdict."""
    from .cli import read_status
    check_limit(limit)
    status = read_status(config)
    if status.get('instance_id') != config.instance_id:
        raise ValueError('activity journal belongs to another instance')
    result = {'conversation': 'separate_supervisor',
              'meaning': 'delivery execution metadata, not proof of business completion or sending',
              'status': status, 'events': [], 'outbound': {'state': 'not_configured'}}
    path = config.state_dir/'journal.sqlite3'
    if path.exists():
        db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA query_only=ON')
            rows = db.execute('SELECT event_id,lane,notice_id,state,turn_id,execution_status,updated_at '
                              'FROM events ORDER BY updated_at DESC,event_id LIMIT ?', (limit,))
            result['events'] = [dict(row) for row in rows]
        finally:
            db.close()
    # read_status includes every operator run for recovery tooling. Keep the
    # interactive surface bounded, with the newest first, and no instructions.
    stamp = status.get('updated_at')
    result['heartbeat'] = ('unknown' if not isinstance(stamp, (int, float)) else
                           'fresh' if 0 <= time.time()-stamp <= max(10, config.poll_interval_ms/1000*3)
                           else 'stale')
    result['readiness'] = supervisor_readiness(config, status, result['heartbeat'])
    status['operator_runs'] = list(reversed(status.get('operator_runs', [])))[:limit]
    if outbox is not None:
        outbox = Path(outbox)
        if not outbox.is_absolute() or outbox.is_symlink() or not outbox.is_dir():
            raise ValueError('outbox must be an explicit existing directory')
        results = outbox/'results'
        if results.is_symlink() or (results.exists() and not results.is_dir()):
            raise ValueError('outbox results must be a directory when present, not a symlink')
        has_results = results.is_dir()
        latest = sorted(results.glob('*.json'), key=lambda p: p.lstat().st_mtime, reverse=True)[:limit] if has_results else []
        records = []
        for path in latest:
            try:
                raw, _ = _read(results, path.name)
                value = json.loads(raw)
                if not isinstance(value, dict) or value.get('contract_version', '').split('.')[0] != '2':
                    raise ValueError('unknown result contract')
                record = {key: value.get(key) for key in ('req_id', 'outcome', 'reason_code', 'message_id', 'ts')}
                # Never print arbitrary detail, attestation, recipients or body.
                if any(v is not None and (not isinstance(v, str) or len(v) > 300) for v in record.values()):
                    raise ValueError('invalid result metadata')
                if not record['req_id'] or not record['outcome']:
                    raise ValueError('incomplete result')
                records.append(record)
            except (OSError, ValueError, AttributeError):
                records.append({'state': 'unreadable_result', 'file': path.name})
        queued = sorted(p.name for p in outbox.glob('req-*.json'))[:limit]
        result['outbound'] = {'state': 'observed' if has_results else 'no_results_yet', 'queued_request_files': queued, 'results': records,
                              'meaning': 'queued is inert; accepted is runtime acceptance, not peer completion'}
    return result


def watch_activity(config, *, limit=20, outbox=None, interval=1, follow=False, output=None):
    if not isinstance(interval, (int, float)) or not 0.1 <= interval <= 60:
        raise ValueError('activity interval must be between 0.1 and 60 seconds')
    previous = None
    output = output or sys.stdout
    while True:
        value = read_activity(config, limit=limit, outbox=outbox)
        comparison = copy.deepcopy(value)
        for key in ('updated_at', 'oldest_pending_age_seconds'):
            comparison['status'].pop(key, None)
        if comparison != previous:
            # JSON escaping also prevents terminal escapes from metadata.
            print(json.dumps(value, ensure_ascii=True, sort_keys=True), file=output, flush=True)
            previous = comparison
        if not follow:
            return
        time.sleep(interval)


def serve_activity(config, *, outbox=None, source=None, output=None):
    """Small stdio MCP server: one read-only tool, no dispatch/recovery API."""
    source, output = source or sys.stdin.buffer, output or sys.stdout
    while True:
        raw = source.readline(1024*1024+1)
        if not raw:
            return
        if len(raw) > 1024*1024:
            raise ValueError('activity MCP frame too large')
        try:
            request = json.loads(raw)
        except ValueError:
            print(json.dumps({'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Invalid JSON'}}), file=output, flush=True)
            continue
        if not isinstance(request, dict):
            request = {'id': None}
        if 'id' not in request:
            continue
        response = {'jsonrpc': '2.0', 'id': request['id']}
        method, params = request.get('method'), request.get('params', {})
        if method == 'initialize':
            result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                      'serverInfo': {'name': 'amap-fleet-activity', 'version': '0.1.0'}}
        elif method == 'ping':
            result = {}
        elif method == 'tools/list':
            result = {'tools': [{'name': 'activity', 'description': GUIDANCE,
                'annotations': {'readOnlyHint': True, 'destructiveHint': False},
                'inputSchema': {'type': 'object', 'properties': {'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}},
                                'additionalProperties': False}}]}
        elif method == 'tools/call':
            if not isinstance(params, dict) or params.get('name') != 'activity':
                response['error'] = {'code': -32602, 'message': 'Only activity is supported'}
            else:
                arguments = params.get('arguments', {})
                try:
                    if not isinstance(arguments, dict) or set(arguments)-{'limit'}:
                        raise ValueError('invalid activity arguments')
                    value = read_activity(config, limit=arguments.get('limit', 20), outbox=outbox)
                    result = {'content': [{'type': 'text', 'text': json.dumps(value)}]}
                except (OSError, ValueError, sqlite3.Error):
                    result = {'isError': True, 'content': [{'type': 'text', 'text': 'Activity unavailable; inspect connector configuration/state. No recovery was attempted.'}]}
        else:
            response['error'] = {'code': -32601, 'message': 'Unsupported method'}
        if 'error' not in response:
            response['result'] = result
        print(json.dumps(response), file=output, flush=True)


def toml_value(value):
    if isinstance(value, dict):
        return '{' + ','.join(json.dumps(k, ensure_ascii=False)+ '='+toml_value(v) for k,v in value.items()) + '}'
    if isinstance(value, list):
        return '[' + ','.join(toml_value(v) for v in value) + ']'
    if isinstance(value, (str, bool, int, float)):
        # JSON surrogate-pair escapes are invalid TOML Unicode scalars.
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    raise ValueError('unsupported interactive configuration value')


SESSION_ID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.IGNORECASE)


def native_interactive_args(argv, default_cwd):
    """Keep native tokens intact, separating options from a literal prompt.

    An explicit option vocabulary keeps remote work and unsupported subcommands
    out. Local resume additionally requires session-metadata preflight.
    Configuration overrides also reach the preflight process.
    """
    if not isinstance(argv, (list, tuple)) or not argv or not argv[0] or not all(isinstance(arg, str) and '\0' not in arg for arg in argv):
        raise ValueError('native argv requires a binary and string arguments')
    binary, tail = argv[0], list(argv[1:])
    options, prompt, preflight = [], [], []
    cwd = default_cwd
    explicit_cwd = False
    explicit_no_alt_screen = False
    resume_mode, selector, selector_index = False, None, None
    flags = {'--no-alt-screen', '--no-daemon', '--search', '--oss', '--approve-for-me',
             '--full-auto', '--dangerously-bypass-approvals-and-sandbox',
             '--dangerously-bypass-hook-trust', '--strict-config', '--help', '-h', '--version', '-V'}
    values = {'-c', '--config', '-m', '--model', '-s', '--sandbox', '-a', '--ask-for-approval',
              '-C', '--cd', '-i', '--image', '--local-provider', '--add-dir', '--enable', '--disable'}
    index = 0
    while index < len(tail):
        token = tail[index]
        if token == '--':
            prompt = tail[index:]
            break
        if token == 'resume' and not resume_mode:
            resume_mode = True
            options.append(token)
            index += 1
            continue
        if resume_mode and (token == '--last' or SESSION_ID.fullmatch(token)):
            if selector is not None:
                raise ValueError('native resume requires exactly one selector')
            selector, selector_index = token, len(options)
            options.append(token)
            index += 1
            continue
        if token in flags:
            options.append(token)
            if token == '--no-alt-screen':
                explicit_no_alt_screen = True
            if token == '--strict-config':
                preflight.append(token)
            index += 1
            continue
        name, equal, value = token.partition('=')
        if not token.startswith('--') and len(token) > 2 and token[:2] in {'-c', '-m', '-s', '-a', '-C', '-i', '-p'}:
            name, equal, value = token[:2], '=', token[2:].removeprefix('=')
        if name in {'-p', '--profile'}:
            raise ValueError('native --profile is unsupported: app-server preflight cannot load that profile')
        if name not in values:
            raise ValueError('unsupported native option or subcommand; fork and remote launches are refused; '
                             'put a literal prompt after native --')
        start = index
        if not equal:
            index += 1
            if index >= len(tail):
                raise ValueError('native option requires a value')
            value = tail[index]
        if name in {'-c', '--config'}:
            if '=' not in value:
                raise ValueError('native configuration override requires key=value')
            preflight += ['-c', value]
        elif name in {'--enable', '--disable'}:
            preflight += [name, value]
        elif name in {'-C', '--cd'}:
            cwd = str(Path(value).resolve())
            explicit_cwd = True
        options += tail[start:index+1]
        index += 1
    defaults = [] if explicit_no_alt_screen else ['--no-alt-screen']
    if not explicit_cwd:
        defaults += ['--cd', default_cwd]
    if resume_mode and selector is None:
        raise ValueError('native resume subcommand requires --last or an explicit session UUID; picker is unsupported')
    resume = None if not resume_mode else (selector, len(defaults) + selector_index)
    return binary, defaults + options, prompt, preflight, cwd, resume


async def native_resume_target(client, config, cwd, selector):
    """Resolve CLI history without loading a conversation or claiming work.

    App-server-owned threads can be classified as editor sessions. Resolve
    --last to an explicit CLI ID, so a later native lookup cannot choose the
    supervisor or a different session. Refuse unsupported history sources.
    """
    from .cli import read_status
    status = read_status(config)
    if status.get('instance_id') != config.instance_id:
        raise ValueError('resume journal belongs to another instance')
    if selector == '--last':
        result = await client.request('thread/list', {'cwd': str(Path(cwd).resolve()),
            'sourceKinds': ['cli'], 'archived': False, 'sortKey': 'created_at',
            'sortDirection': 'desc', 'limit': 1})
        data = result.get('data')
        if not isinstance(data, list) or len(data) != 1:
            raise ValueError('no single CLI session available to resume in this workspace; use a new conversation')
        thread = data[0]
    else:
        thread = (await client.request('thread/read', {'threadId': selector, 'includeTurns': False})).get('thread')
    return validate_native_session(thread, cwd, selector, status.get('thread_id'))


def validate_native_session(thread, cwd, selector, bound):
    if not isinstance(thread, dict):
        raise ValueError('native resume session metadata unavailable')
    target = thread.get('id')
    if not isinstance(target, str) or SESSION_ID.fullmatch(target) is None:
        raise ValueError('native resume session ID is invalid')
    if selector != '--last' and target.lower() != selector.lower():
        raise ValueError('native resume metadata names a different session')
    if isinstance(bound, str) and target.lower() == bound.lower():
        raise ValueError('native resume must not target the supervisor-owned thread')
    if thread.get('source') != 'cli':
        raise ValueError('native resume supports CLI conversations only; supervisor and editor sessions are refused')
    if not isinstance(thread.get('cwd'), str) or Path(thread['cwd']).resolve() != Path(cwd).resolve():
        raise ValueError('native resume session belongs to a different workspace')
    return target


async def prepare_native_resume(client, config, target, cwd, servers, instructions):
    """Append current protected policy to the selected CLI history, no turn.

    Native resume retains old developer context. Command-line configuration
    alone does not refresh that policy after a conversation has real history.
    """
    from .cli import read_status
    resumed = (await client.request('thread/resume', {
        'threadId': target, 'excludeTurns': True, 'config': {'mcp_servers': servers}})).get('thread')
    status = read_status(config)
    if status.get('instance_id') != config.instance_id:
        raise ValueError('resume journal belongs to another instance')
    validate_native_session(resumed, cwd, target, status.get('thread_id'))
    if resumed.get('status') != {'type': 'idle'}:
        raise ValueError('native resume did not establish an idle CLI conversation')
    registered = []
    cursor = None
    for _ in range(10):
        params = {'threadId': target, 'detail': 'toolsAndAuthOnly'}
        if cursor is not None:
            params['cursor'] = cursor
        page = await client.request('mcpServerStatus/list', params)
        registered.extend(page.get('data', []))
        cursor = page.get('nextCursor')
        if cursor is None:
            break
    else:
        raise ValueError('native resume MCP registry exceeded expected bound')
    for name, definition in servers.items():
        matches = [server for server in registered if server.get('name') == name]
        if (len(matches) != 1 or matches[0].get('toolsError') or not isinstance(matches[0].get('tools'), dict) or
                set(matches[0].get('tools', {})) != set(definition['enabled_tools'])):
            raise ValueError('native resume protected MCP registry differs from the trusted configuration')
    await client.request('thread/inject_items', {'threadId': target, 'items': [
        {'type': 'message', 'role': 'developer', 'content': [
            {'type': 'input_text', 'text': instructions}]}]})


async def interactive_argv(config, config_path, *, binary=None, outbox=None, native_argv=None, prepare_resume=False):
    if native_argv is not None and binary is not None:
        raise ValueError('use either --binary or a native argv, not both')
    binary, options, prompt, preflight, cwd, resume = native_interactive_args(
        native_argv if native_argv is not None else [binary or 'codex'], config.cwd)
    if resume is not None and not prepare_resume:
        raise ValueError('native resume requires interactive execution to install current policy; interactive-args is read-only')
    trusted = config.trusted_overrides()
    if trusted is None:
        raise ValueError('interactive participation requires a protected trusted_config_file')
    args = ['--config', str(Path(config_path).resolve()), 'activity-mcp']
    if outbox is not None:
        read_activity(config, outbox=outbox)  # Refuse bad observation paths before any history update.
        args += ['--outbox-dir', str(outbox)]
    protected = copy.deepcopy(trusted['mcp_servers'])
    protected['amap_fleet'] = {'command': sys.executable, 'args': ['-m', 'amap_codex.cli', *args],
        'env': {'PYTHONPATH': str(Path(__file__).resolve().parents[1])},
        'required': True, 'enabled_tools': ['activity'], 'default_tools_approval_mode': 'approve'}
    # Fresh launch reads configuration only. Resume loads only a verified CLI
    # target and appends protected developer policy, never a model turn.
    client = AppServerClient([binary, *preflight, 'app-server', '--listen', 'stdio://'],
                             expected_version=config.codex_version, timeout=config.rpc_timeout_seconds)
    try:
        await client.start()
        native = (await client.request('config/read', {'cwd': cwd})).get('config')
        if not isinstance(native, dict):
            raise ValueError('native Codex configuration unavailable')
        servers = native.get('mcp_servers') or {}
        names = set(trusted['mcp_servers']) | {'amap_fleet'}
        if not isinstance(servers, dict) or names & set(servers):
            raise ValueError('native configuration already uses reserved AMAP MCP names')
        existing = native.get('developer_instructions') or ''
        if not isinstance(existing, str):
            raise ValueError('native developer instructions must be text')
        instructions = existing + '\n\n' + config.operator_instructions.read_text() + '\n\n' + GUIDANCE
        if resume is not None:
            selector, slot = resume
            options[slot] = await native_resume_target(client, config, cwd, selector)
            await prepare_native_resume(client, config, options[slot], cwd, protected, instructions)
    finally:
        await client.stop()
    # Only messaging and guidance are changed. Model, sandbox, approval,
    # network, authentication and ambient tools retain their native settings.
    argv = [binary, *options]
    for name, server in protected.items():
        argv += ['-c', 'mcp_servers.'+name+'='+toml_value(server)]
    return argv + ['-c', 'developer_instructions='+toml_value(instructions), *prompt]
