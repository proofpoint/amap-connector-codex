"""Real owned service, native TUI and durable AMAP queue, with local inference.

Disposable credential-free homes; no router, external inference, fleet traffic
or deployment changes. Does not establish existing-history adoption or all
native control types. Uses production transport/controller/attachment code.
"""
import argparse
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import tempfile

from amap_codex.config import Config, Lane
from amap_codex.interactive import toml_value
from amap_codex.journal import IntegrityConflict
from amap_codex.local_socket import Socket
from amap_codex.native_queue import snapshot
from amap_codex.spool import event_id
from amap_codex.unified import UnifiedService
from amap_codex.unified_attach import attachment_argv, descriptor
from _unified_evidence import assert_retained_history, history_snapshot
from native_queue_probe import Fixture
from unified_probe import Terminal, until, response_stream


def configuration(root, build, *, approval=False, hold_first=False):
    repo = Path(__file__).resolve().parents[2]
    provider = Fixture(root, build, hold_first=hold_first, approval=approval)
    for name in ('mail/notices', 'mail/messages', 'peer/notices', 'peer/messages', 'outbox', 'roster', 'state'):
        (root / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    workflow = root / 'workflow.md'
    workflow.write_text('Synthetic protocol probe. Do not execute commands or send messages.')
    servers = {}
    for name, lane in [('inbox', 'mail'), ('delegation', 'peer')]:
        servers[name] = {'command': str(repo / 'bin/inbox-mcp-vol'), 'required': True,
            'enabled_tools': ['list_messages', 'read_message', 'read_attachment'],
            'default_tools_approval_mode': 'approve', 'env': {
                'INBOX_MESSAGE_DIR': str(root / lane / 'messages'), 'INBOX_LANE': lane}}
    servers['inbox_submit'] = {'command': str(repo / 'bin/inbox-submit'), 'args': ['mcp'], 'required': True,
        'enabled_tools': ['submit', 'submit_result', 'peers'], 'default_tools_approval_mode': 'approve',
        'env': {'OUTBOX_DIR': str(root / 'outbox'), 'AMAP_ROSTER_DIR': str(root / 'roster'),
                'AMAP_SELF': 'synthetic@example.invalid'}}
    trusted = root / 'trusted.toml'
    trusted.write_text('mcp_servers=' + toml_value(servers) + '\n')
    config = Config('synthetic-runtime', 'synthetic@example.invalid', ['codex', 'app-server', '--stdio'],
        [Lane(n, root / n / 'notices', root / n / 'messages', root / (n + '.claim')) for n in ('mail', 'peer')],
        root / 'state', None, str(root), 'read-only', 'on-request' if approval else 'never', workflow,
        codex_version=build, poll_interval_ms=20, trusted_config_file=trusted,
        conversation_mode='unified', unified_argv=['codex']).validate()
    path = root / 'connector.toml'
    values = {'instance_id': config.instance_id, 'self_address': config.self_address,
        'launch_argv': config.launch_argv, 'state_dir': str(config.state_dir), 'cwd': config.cwd,
        'sandbox': config.sandbox, 'approval_policy': config.approval_policy,
        'operator_instructions': str(workflow), 'codex_version': build, 'poll_interval_ms': 20,
        'trusted_config_file': str(trusted), 'conversation_mode': 'unified', 'unified_argv': ['codex'],
        'lanes': {lane.name: {'notice_dir': str(lane.notice_dir), 'message_dir': str(lane.message_dir),
                             'claim_path': str(lane.claim_path)} for lane in config.lanes}}
    path.write_text('\n'.join(k + '=' + toml_value(v) for k, v in values.items()) + '\n')
    return config, path, provider


def publish(config, lane_name, notice_id):
    lane = next(l for l in config.lanes if l.name == lane_name)
    filename = 'notice-' + notice_id + '.json'
    notice = {'contract_version': '2', 'notice_id': notice_id, 'ts': '2026-10-25T00:00:00Z',
        'kind': 'peer' if lane_name == 'peer' else 'deliver', 'message': {
            'id': 'synthetic_message_' + notice_id, 'from': 'peer@example.invalid',
            'mailbox': 'peer' if lane_name == 'peer' else 'INBOX',
            'subject': 'Synthetic subject', 'preview': 'Synthetic preview'}}
    body = {'contract_version': '2', 'notice_id': notice_id, 'body_text': 'Synthetic content',
            'to': config.self_address, 'from': 'peer@example.invalid', 'subject': 'Synthetic subject',
            'id': notice['message']['id'], 'date': '2026-10-25T00:00:00Z'}
    (lane.message_dir / filename).write_text(json.dumps(body))
    (lane.notice_dir / filename).write_text(json.dumps(notice))
    return event_id(config.instance_id, lane_name, notice_id)


async def settle(service, predicate, terminal=None):
    async with asyncio.timeout(20):
        while not predicate():
            if terminal:
                terminal.poll()
                assert terminal.process.poll() is None, 'native terminal exited before runtime evidence'
            await service.tick()
            await asyncio.sleep(.02)


async def ordinary(root, build):
    config, path, provider = configuration(root, build, hold_first=True)
    service, terminal = UnifiedService(config, path, env=provider.env), None
    try:
        await service.start()
        assert service.status['readiness']['state'] == 'ready'
        thread, generation = service.journal.thread_id, service.backend.generation
        argv = attachment_argv(config, native_argv=['codex', '--no-daemon', '--', 'SYNTHETIC_HUMAN'], wait=0)
        assert '--no-daemon' not in argv
        terminal = Terminal(argv, provider.env, root)
        try:
            await until(provider.arrived.is_set, terminal)
        except RuntimeError:
            terminal.poll()
            print(terminal.output.decode(errors='replace')[-1000:], flush=True)
            raise
        observe = service.queue.reconcile
        async def incomplete():
            raise IntegrityConflict('synthetic read interleaving')
        service.queue.reconcile = incomplete
        await service.tick()
        assert service.backend.process.returncode is None and len(provider.requests) == 1
        assert service.status['readiness']['checks']['native_reconciled'] == 'unknown'
        assert service.status['queue_depth'] is None
        service.queue.reconcile = observe
        await service.tick()
        peer = publish(config, 'peer', 'synthetic_peer')
        await service.tick()
        assert service.journal.event(peer)['state'] == 'queued'
        assert service.journal.pending_outcomes() == [] and len(provider.requests) == 1
        try:
            extra = await Socket.connect(service.backend.front_path, timeout=.5)
        except (OSError, RuntimeError):
            pass
        else:
            await extra.close()
            raise AssertionError('second writable frontend accepted')
        provider.release.set()
        await settle(service, lambda: service.journal.event(peer)['state'] == 'finished', terminal)
        await until(lambda: terminal.contains('NATIVE_QUEUE_RESPONSE_2'), terminal)
        assert service.journal.event(peer)['execution_status'] == 'completed'
        assert len(service.ownership.claims) == 2 and len(provider.requests) == 2
        terminal.close()
        terminal = None
        await settle(service, lambda: service.gate.client_count == 0)
        mail = publish(config, 'mail', 'synthetic_mail')
        await settle(service, lambda: service.journal.db.execute('SELECT state FROM events WHERE event_id=?',
                     (mail,)).fetchone() is not None)
        await settle(service, lambda: service.journal.event(mail)['state'] == 'finished')
        assert service.backend.process.returncode is None and len(provider.requests) == 3
        before = history_snapshot((await snapshot(service.backend.client, thread))[0])
        stale_endpoint = service.backend.front_path
        await service.close()
        try:
            descriptor(config)
        except ValueError:
            pass
        else:
            raise AssertionError('stopped generation reported ready')
        service = UnifiedService(config, path, env=provider.env)
        await service.start()
        await service.tick()
        assert service.journal.thread_id == thread and service.backend.generation != generation
        assert service.status['readiness']['state'] == 'ready' and len(provider.requests) == 3
        assert not stale_endpoint.exists()
        assert_retained_history(before, (await snapshot(service.backend.client, thread))[0])
        argv = attachment_argv(config, native_argv=['codex', 'resume', '--last'], wait=0)
        assert argv[-1] == thread and '--last' not in argv
        terminal = Terminal(argv, provider.env, root)
        await until(lambda: terminal.contains('NATIVE_QUEUE_RESPONSE_3'), terminal)
        assert len(provider.requests) == 3
        return {'human_and_peer_same_pane': True, 'queue_waits_for_human': True, 'claims_owned': True,
            'queue_receipt_not_delivered': True, 'second_frontend_refused': True, 'detached_mail_completes': True,
            'stale_generation_refused': True, 'restart_exact_history_without_replay': True,
            'resume_last_maps_to_canonical': True, 'reattached_final_response_visible': True,
            'incomplete_observation_holds_without_killing_work': True}
    finally:
        provider.release.set()
        if terminal:
            terminal.close()
        await service.close()
        await provider.close()


async def approval(root, build):
    config, path, provider = configuration(root, build, approval=True)
    service, terminal = UnifiedService(config, path, env=provider.env), None
    try:
        await service.start()
        event = publish(config, 'mail', 'synthetic_approval')
        await settle(service, lambda: bool(service.backend.client.blocked))
        # A new turn can interleave history pagination. Let the independent
        # controller publish a complete observation before a synchronous attach.
        await settle(service, lambda: service.status['readiness']['state'] == 'ready')
        assert not (root / 'approval-executed.txt').exists()
        assert service.gate.client_count == 0 and len(provider.requests) == 1
        terminal = Terminal(attachment_argv(config, wait=0), provider.env, root)
        await until(lambda: terminal.contains('Would you like to run the following command?'), terminal)
        await asyncio.sleep(.25)
        terminal.poll()
        os.write(terminal.master, b'\x1b')
        await settle(service, lambda: service.journal.event(event)['state'] == 'finished', terminal)
        assert terminal.contains('You canceled the request')
        assert not (root / 'approval-executed.txt').exists()
        return {'detached_approval_holds': True, 'native_prompt_through_gate': True,
                'native_decline_prevents_command': True, 'passive_controller_never_answers': True}
    finally:
        if terminal:
            terminal.close()
        await service.close()
        await provider.close()


async def model_restart(root, build):
    config, path, provider = configuration(root, build)
    service, terminal = UnifiedService(config, path, env=provider.env), None
    try:
        await service.start()
        original = service.journal.thread_id
        terminal = Terminal(attachment_argv(config, native_argv=['codex', '--', 'SYNTHETIC_BEFORE_MODEL_CHANGE'],
                                           wait=0), provider.env, root)
        await settle(service, lambda: len(provider.requests) == 1, terminal)
        await until(lambda: terminal.contains('NATIVE_QUEUE_RESPONSE_1'), terminal)
        terminal.close()
        terminal = None
        await service.close()
        config = replace(config, codex_model='changed-synthetic-model')
        path.write_text(path.read_text() + '\ncodex_model="changed-synthetic-model"\n')
        service = UnifiedService(config, path, env=provider.env)
        await service.start()
        assert service.journal.thread_id == original
        assert service.effective['model'] == 'changed-synthetic-model'
        assert service.journal.db.execute("SELECT 1 FROM audit WHERE action='unified_model_changed'").fetchone()
        terminal = Terminal(attachment_argv(config, native_argv=['codex', '--', 'SYNTHETIC_AFTER_MODEL_CHANGE'],
                                           wait=0), provider.env, root)
        try:
            await until(lambda: terminal.contains('NATIVE_QUEUE_RESPONSE_2'), terminal)
        except BaseException:
            print('MODEL_GATE_REFUSALS', service.gate.refused_methods, flush=True)
            terminal.poll()
            print(terminal.output.decode(errors='replace')[-1800:], flush=True)
            raise
        event = publish(config, 'peer', 'synthetic_model_peer')
        await settle(service, lambda: service.journal.db.execute('SELECT 1 FROM events WHERE event_id=?',
                     (event,)).fetchone(), terminal)
        await settle(service, lambda: service.journal.event(event)['state'] == 'finished', terminal)
        assert [r['model'] for r in provider.requests] == ['synthetic-model', 'changed-synthetic-model',
                                                        'changed-synthetic-model']
        return {'audited_model_restart_keeps_canonical_history': True, 'pane_and_peer_use_selected_model': True}
    finally:
        if terminal:
            terminal.close()
        await service.close()
        await provider.close()


async def crash_pending(root, build, *, changed=False):
    config, path, provider = configuration(root, build, hold_first=True)
    service, terminal = UnifiedService(config, path, env=provider.env), None
    try:
        await service.start()
        canonical = service.journal.thread_id
        terminal = Terminal(attachment_argv(config, native_argv=['codex', '--', 'SYNTHETIC_CRASHED_HUMAN'], wait=0),
                            provider.env, root)
        await until(provider.arrived.is_set, terminal)
        event = publish(config, 'peer', 'synthetic_pending')
        await service.tick()
        assert service.journal.event(event)['state'] == 'queued'
        client_id = service.queue.ledger.latest(event)['client_id']
        terminal.close()
        terminal = None
        os.killpg(service.backend.process.pid, signal.SIGKILL)
        await service.close()
        provider.release.set()
        if changed:
            body = root / 'peer/messages/notice-synthetic_pending.json'
            document = json.loads(body.read_text())
            document['body_text'] = 'Changed retained body'
            body.write_text(json.dumps(document))
        service = UnifiedService(config, path, env=provider.env)
        if changed:
            try:
                await service.start()
            except IntegrityConflict:
                assert len(provider.requests) == 1
                return {'changed_queued_body_refused_before_resume': True, 'no_queued_model_execution': True}
            raise AssertionError('changed queued body was allowed to execute on resume')
        await service.start()
        await settle(service, lambda: service.journal.event(event)['state'] == 'finished')
        assert len(provider.requests) == 2 and service.journal.thread_id == canonical
        assert service.queue.ledger.latest(event)['client_id'] == client_id
        assert service.journal.db.execute('SELECT COUNT(*) FROM attempts WHERE event_id=?', (event,)).fetchone()[0] == 1
        return {'pending_survives_backend_crash': True, 'one_original_attempt': True,
                'same_client_identity': True, 'interrupted_human_not_replayed': True}
    finally:
        provider.release.set()
        if terminal:
            terminal.close()
        await service.close()
        await provider.close()


def tool_stream(number, model, namespace, name, arguments):
    item = {'id': 'synthetic_tool_' + str(number), 'type': 'function_call', 'call_id': 'synthetic_call_' + str(number),
            'name': name, 'namespace': namespace, 'arguments': json.dumps(arguments), 'status': 'completed'}
    response = {'id': 'synthetic_response_' + str(number), 'object': 'response', 'created_at': 1,
        'status': 'completed', 'model': model, 'output': [item],
        'usage': {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}}
    events = [
        {'type': 'response.created', 'response': {**response, 'status': 'in_progress', 'output': []}},
        {'type': 'response.output_item.added', 'output_index': 0,
         'item': {**item, 'arguments': '', 'status': 'in_progress'}},
        {'type': 'response.function_call_arguments.delta', 'output_index': 0,
         'item_id': item['id'], 'delta': item['arguments']},
        {'type': 'response.function_call_arguments.done', 'output_index': 0,
         'item_id': item['id'], 'arguments': item['arguments']},
        {'type': 'response.output_item.done', 'output_index': 0, 'item': item},
        {'type': 'response.completed', 'response': response},
    ]
    return ''.join('data: ' + json.dumps(e) + '\n\n' for e in events).encode()


async def mcp_workflow(root, build):
    config, path, provider = configuration(root, build)
    service = UnifiedService(config, path, env=provider.env)
    terminal = None
    def response(number, request):
        if number == 1:
            namespaces = {t.get('name'): {f.get('name') for f in t.get('tools', [])}
                          for t in request['tools'] if t.get('type') == 'namespace'}
            assert 'read_message' in namespaces['mcp__delegation']
            return tool_stream(number, request['model'], 'mcp__delegation', 'read_message', {'id': 'synthetic_tools'})
        outputs = [i for i in request['input'] if i.get('type') == 'function_call_output']
        if number == 2:
            output = next(i for i in outputs if i['call_id'] == 'synthetic_call_1')
            assert 'Synthetic content' in json.dumps(output)
            assert 'authenticated peer' in json.dumps(output).lower()
            return tool_stream(number, request['model'], 'mcp__inbox_submit', 'submit', {
                'to': ['peer@example.invalid'], 'subject': 'Synthetic reply', 'body_text': 'SYNTHETIC_INERT_REPLY',
                'in_reply_to': 'synthetic_message_synthetic_tools'})
        requests = sorted((root / 'outbox').glob('req-*.json'))
        assert len(requests) == 1
        if number == 3:
            req_id = requests[0].stem.removeprefix('req-')
            return tool_stream(number, request['model'], 'mcp__inbox_submit', 'submit_result', {'req_id': req_id})
        assert number == 4
        output = next(i for i in outputs if i['call_id'] == 'synthetic_call_3')
        assert 'pending' in json.dumps(output)
        return response_stream(number, request['model'], 'SYNTHETIC_MCP_WORKFLOW_COMPLETE')
    provider.response_builder = response
    try:
        await service.start()
        terminal = Terminal(attachment_argv(config, wait=0), provider.env, root)
        await until(lambda: terminal.contains('synthetic-model'), terminal)
        event = publish(config, 'peer', 'synthetic_tools')
        await settle(service, lambda: service.journal.db.execute('SELECT 1 FROM events WHERE event_id=?', (event,)).fetchone(), terminal)
        await settle(service, lambda: service.journal.event(event)['state'] == 'finished', terminal)
        await until(lambda: terminal.contains('SYNTHETIC_MCP_WORKFLOW_COMPLETE'), terminal)
        assert service.journal.event(event)['execution_status'] == 'completed'
        history = (await snapshot(service.backend.client, service.journal.thread_id))[0]
        tools = [i for t in history['thread']['turns'] for i in t['items'] if i['type'] == 'mcpToolCall']
        assert [(i['server'], i['tool'], i['status']) for i in tools] == [
            ('delegation', 'read_message', 'completed'), ('inbox_submit', 'submit', 'completed'),
            ('inbox_submit', 'submit_result', 'completed')]
        queued = list((root / 'outbox').glob('req-*.json'))
        assert len(queued) == 1 and len(provider.requests) == 4
        document = json.loads(queued[0].read_text())
        assert document['contract_version'] == '2'
        assert document['draft']['to'] == ['peer@example.invalid']
        assert document['draft']['body_text'] == 'SYNTHETIC_INERT_REPLY'
        assert document['draft']['reply_to_message_id'] == 'synthetic_message_synthetic_tools'
        assert not (root / 'outbox/results').exists() or not list((root / 'outbox/results').glob('*.json'))
        return {'authenticated_reader_fetched_body': True, 'inert_reply_queued': True,
                'submit_result_reports_pending': True, 'reply_correlation_preserved': True,
                'workflow_visible_in_same_pane': True, 'runtime_send_acceptance': False}
    finally:
        if terminal:
            terminal.close()
        await service.close()
        await provider.close()


async def probe(output):
    build = subprocess.check_output(['codex', '--version'], text=True).splitlines()[0]
    evidence = {'timestamp_utc': datetime.now(timezone.utc).isoformat(), 'codex_version': build,
        'os': platform.system(), 'architecture': platform.machine(),
        'scope': 'synthetic owned service/native TUI; no existing-history adoption or deployment acceptance',
        'external_inference': False, 'user_auth_used': False, 'fleet_submissions': 0}
    with tempfile.TemporaryDirectory(prefix='amap-runtime-') as directory:
        for name, scenario in [('ordinary', ordinary), ('approval', approval), ('model_restart', model_restart),
                               ('crash_pending', crash_pending),
                               ('changed_pending', lambda root, build: crash_pending(root, build, changed=True)),
                               ('mcp_workflow', mcp_workflow)]:
            root = Path(directory) / name
            root.mkdir(mode=0o700)
            evidence[name] = await scenario(root, build)
    evidence['probe_assertions_passed'] = True
    evidence['fleet_ready'] = False
    output.write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps(evidence))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(probe(parser.parse_args().output))
