"""Native queue reliability investigation, using only a disposable local backend.

No user authentication, external inference, router or production state. This
is a protocol probe, not a deployed unified connector. Queue persistence and
duplicate suppression are measured properties, never assumed from CLI success.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import http.server
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import tempfile
import threading
import uuid

from amap_codex.app_server import AppServerDelivery
from _native_queue_evidence import assert_queued_original
from _unified_evidence import assert_retained_history, history_snapshot
from _unified_ws import UnixRPC
from unified_probe import Terminal, until, completed, response_stream


def command_stream(number, model):
    """Synthetic request for a harmless command requiring an operator decision."""
    arguments = json.dumps({'cmd': 'printf AMAP_SYNTHETIC_APPROVAL > approval-executed.txt',
        'sandbox_permissions': 'require_escalated', 'justification': 'Synthetic queue approval probe'})
    item = {'id': f'call_item_{number}', 'type': 'function_call', 'name': 'exec_command',
            'call_id': f'call_{number}', 'arguments': arguments, 'status': 'completed'}
    response = {'id': f'resp_{number}', 'object': 'response', 'created_at': 1, 'status': 'completed',
                'model': model, 'output': [item], 'usage': {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}}
    events = [
        {'type': 'response.created', 'response': {**response, 'status': 'in_progress', 'output': []}},
        {'type': 'response.output_item.added', 'output_index': 0,
         'item': {**item, 'arguments': '', 'status': 'in_progress'}},
        {'type': 'response.function_call_arguments.delta', 'output_index': 0,
         'item_id': item['id'], 'delta': arguments},
        {'type': 'response.function_call_arguments.done', 'output_index': 0,
         'item_id': item['id'], 'arguments': arguments},
        {'type': 'response.output_item.done', 'output_index': 0, 'item': item},
        {'type': 'response.completed', 'response': response},
    ]
    return ''.join('data: ' + json.dumps(event) + '\n\n' for event in events).encode()


class Fixture:
    """Private synthetic provider and explicitly selected backend generations."""
    def __init__(self, root, build, *, hold_first=False, approval=False):
        self.root, self.build = root, build
        self.home = root / 'home'
        self.home.mkdir()
        self.sock = root / 'backend.sock'
        self.arrived, self.release = threading.Event(), threading.Event()
        if not hold_first:
            self.release.set()
        self.requests = []
        self.server_requests = []
        self.approval = approval
        self.response_builder = None
        fixture = self

        class Provider(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                fixture.requests.append(request)
                number = len(fixture.requests)
                if number == 1:
                    fixture.arrived.set()
                    if not fixture.release.wait(30):
                        self.send_error(504)
                        return
                wire = (fixture.response_builder(number, request) if fixture.response_builder else
                        command_stream(number, request['model']) if fixture.approval and number == 1 else
                        response_stream(number, request['model'], f'NATIVE_QUEUE_RESPONSE_{number}'))
                try:
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.send_header('Content-Length', str(len(wire)))
                    self.end_headers()
                    self.wfile.write(wire)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass  # A scenario deliberately kills the requesting backend.

            def log_message(self, *args):
                pass

        self.provider = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Provider)
        threading.Thread(target=self.provider.serve_forever, daemon=True).start()
        (self.home / 'config.toml').write_text(
            'model_provider="offline"\nmodel="synthetic-model"\ncheck_for_update_on_startup=false\n'
            '[features]\napps=false\n[model_providers.offline]\nname="Synthetic local provider"\n'
            f'base_url="http://127.0.0.1:{self.provider.server_port}/v1"\nwire_api="responses"\n'
            'supports_websockets=false\nrequires_openai_auth=false\nrequest_max_retries=0\n'
            f'[projects.{json.dumps(str(root))}]\ntrust_level="trusted"\n')
        self.env = {'PATH': os.environ['PATH'], 'CODEX_HOME': str(self.home),
                    'TERM': 'xterm-256color', 'LANG': 'C.UTF-8',
                    'NO_PROXY': '127.0.0.1,localhost', 'no_proxy': '127.0.0.1,localhost'}
        for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
            self.env[key] = self.env[key.lower()] = 'http://127.0.0.1:9'
        self.backend = self.client = self.delivery = self.terminal = self.log = None
        self.thread = None

    async def start(self):
        # Only this fixture owns this path and every backend generation.
        self.sock.unlink(missing_ok=True)
        self.log = (self.root / 'backend.log').open('ab')
        self.backend = subprocess.Popen(['codex', 'app-server', '--listen', 'unix://' + str(self.sock)],
            stdout=subprocess.DEVNULL, stderr=self.log, cwd=self.root, env=self.env, start_new_session=True)
        await until(lambda: self.sock.exists() or self.backend.poll() is not None)
        assert self.backend.poll() is None, 'private backend failed to start'
        self.sock.chmod(0o600)
        async def observe_request(frame):
            # Passive test observer. Never race the native terminal's decision.
            self.server_requests.append(frame)
        self.client = UnixRPC(self.sock, self.build, request_handler=observe_request)
        self.delivery = AppServerDelivery(self.client, model=None, cwd=str(self.root),
            sandbox='read-only', approval_policy='on-request' if self.approval else 'never',
            instructions='Synthetic queue probe. No external actions.')
        self.thread = await self.delivery.start_or_resume(self.thread)
        await self.client.request('thread/name/set', {'threadId': self.thread, 'name': 'Synthetic queue probe'})

    def attach(self, prompt=None):
        argv = ['codex', '--remote', 'unix://' + str(self.sock), '--no-alt-screen', 'resume', self.thread]
        if prompt:
            argv += ['--', prompt]
        self.terminal = Terminal(argv, self.env, self.root)

    def detach(self):
        if self.terminal:
            self.terminal.close()
            self.terminal = None

    async def queue_cli(self, text):
        process = await asyncio.create_subprocess_exec('codex', 'queue', '--remote', 'unix://' + str(self.sock),
            '--thread', self.thread, '--message', text, cwd=self.root, env=self.env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(process.communicate(), 10)
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
        assert process.returncode == 0, (process.returncode, out, err)
        assert b'Queued message ' in out

    async def add(self, text, client_id=None):
        return (await self.client.request('thread/queue/add', {
            'threadId': self.thread, 'clientUserMessageId': client_id or str(uuid.uuid4()),
            'input': [{'type': 'text', 'text': text}]}))['queuedSubmission']

    async def pending(self):
        result = await self.client.request('thread/queue/list', {'threadId': self.thread})
        assert result.get('nextCursor') is None, 'fixture must fit in one queue page'
        return result['data']

    async def history(self):
        return await self.client.request('thread/read', {'threadId': self.thread, 'includeTurns': True})

    async def stop_backend(self, *, crash=False):
        self.detach()
        if crash and self.backend and self.backend.poll() is None:
            os.killpg(self.backend.pid, signal.SIGKILL)
        if self.client:
            await self.client.stop()
            self.client = self.delivery = None
        if self.backend:
            if self.backend.poll() is None:
                os.killpg(self.backend.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(asyncio.to_thread(self.backend.wait), 5)
            except asyncio.TimeoutError:
                os.killpg(self.backend.pid, signal.SIGKILL)
                await asyncio.to_thread(self.backend.wait)
            self.backend = None
        if self.log:
            self.log.close()
            self.log = None

    async def close(self):
        self.release.set()
        await self.stop_backend()
        self.provider.shutdown()


async def ordinary(root, build):
    fixture = Fixture(root, build, hold_first=True)
    try:
        await fixture.start()
        fixture.attach('SYNTHETIC_HUMAN_INPUT')
        await until(fixture.arrived.is_set, fixture.terminal)
        await fixture.queue_cli('SYNTHETIC_BUSY_POINTER')
        pending = await fixture.pending()
        assert len(pending) == 1 and pending[0]['input'][0]['text'] == 'SYNTHETIC_BUSY_POINTER'
        assert len(fixture.requests) == 1
        fixture.release.set()
        human = await completed(fixture.client, fixture.terminal)
        busy = await completed(fixture.client, fixture.terminal)
        await until(lambda: fixture.terminal.contains('NATIVE_QUEUE_RESPONSE_2'), fixture.terminal)
        await fixture.queue_cli('SYNTHETIC_IDLE_POINTER')
        idle = await completed(fixture.client, fixture.terminal)
        await until(lambda: fixture.terminal.contains('NATIVE_QUEUE_RESPONSE_3'), fixture.terminal)
        history = await fixture.history()
        assert [t['id'] for t in history['thread']['turns']] == [human, busy, idle]
        assert_queued_original(history, fixture.thread, pending[0], busy)
        snapshot = history_snapshot(history)
        fixture.detach()
        fixture.attach()
        await until(lambda: fixture.terminal.contains('NATIVE_QUEUE_RESPONSE_3'), fixture.terminal)
        assert_retained_history(snapshot, await fixture.history())
        assert len(fixture.requests) == 3
        fixture.detach()
        await fixture.queue_cli('SYNTHETIC_DETACHED_POINTER')
        await completed(fixture.client)
        fixture.attach()
        await until(lambda: fixture.terminal.contains('NATIVE_QUEUE_RESPONSE_4'), fixture.terminal)
        assert len(fixture.requests) == len((await fixture.history())['thread']['turns']) == 4
        contexts = [event['payload'] for file in (fixture.home / 'sessions').rglob('*.jsonl')
            for line in file.read_text().splitlines() if (event := json.loads(line)).get('type') == 'turn_context']
        assert len(contexts) == 4
        assert all(c['model'] == 'synthetic-model' and c['approval_policy'] == 'never'
                   and c['sandbox_policy']['type'] == 'read-only' for c in contexts)
        return {'busy_waits': True, 'idle_visible': True, 'detached_executes': True,
                'reattach_exact_history': True, 'settings_preserved': True}
    finally:
        await fixture.close()


async def recovery(root, build):
    fixture = Fixture(root, build, hold_first=True)
    try:
        await fixture.start()
        fixture.attach('SYNTHETIC_HUMAN_INPUT')
        await until(fixture.arrived.is_set, fixture.terminal)
        await fixture.add('SYNTHETIC_RETAIN_POINTER_ONE')
        await fixture.add('SYNTHETIC_RETAIN_POINTER_TWO')
        cancelled = await fixture.add('SYNTHETIC_CANCEL_POINTER')
        deletion = await fixture.client.request('thread/queue/delete', {
            'threadId': fixture.thread, 'queuedSubmissionId': cancelled['id']})
        assert deletion == {'deleted': True}
        pending = await fixture.pending()
        assert cancelled['id'] not in [q['id'] for q in pending]
        before = json.loads(json.dumps(pending))
        await fixture.stop_backend(crash=True)
        fixture.release.set()
        await fixture.start()
        after = await fixture.pending()
        # Resume can already consume a durable queued item. Pending count alone
        # cannot distinguish execution from loss; wait for original turn inputs.
        turns = [await completed(fixture.client), await completed(fixture.client)]
        history = await fixture.history()
        for expected in before:
            matches = [(turn['id'], item) for turn in history['thread']['turns'] for item in turn['items']
                       if item['type'] == 'userMessage' and item.get('clientId') == expected['clientUserMessageId']]
            assert len(matches) == 1 and matches[0][0] in turns
            assert_queued_original(history, fixture.thread, expected, matches[0][0])
        assert await fixture.pending() == [] and len(fixture.requests) == 3
        assert 'SYNTHETIC_CANCEL_POINTER' not in json.dumps(history)
        result = {'cancel_removes_pending': True, 'pending_before_crash': len(before),
                  'pending_immediately_after_resume': len(after),
                  'queued_inputs_execute_once_after_crash': True, 'history_client_id_and_content_match': True,
                  'interrupted_human_not_replayed': True, 'provider_requests_after_resume': len(fixture.requests)}
        return result
    finally:
        await fixture.close()


async def duplicates(root, build):
    fixture = Fixture(root, build, hold_first=True)
    try:
        await fixture.start()
        fixture.attach('SYNTHETIC_HUMAN_INPUT')
        await until(fixture.arrived.is_set, fixture.terminal)
        client_id = str(uuid.uuid4())
        first = await fixture.add('SYNTHETIC_DUPLICATE_POINTER', client_id)
        second = await fixture.add('SYNTHETIC_DUPLICATE_POINTER', client_id)
        assert first['id'] != second['id']
        assert len(await fixture.pending()) == 2
        fixture.release.set()
        for _ in range(3):
            await completed(fixture.client, fixture.terminal)
        history = await fixture.history()
        matches = [t for t in history['thread']['turns'] for i in t['items']
                   if i['type'] == 'userMessage' and i['content'][0].get('text') == 'SYNTHETIC_DUPLICATE_POINTER']
        assert len(matches) == 2 and len(fixture.requests) == 3
        return {'same_client_id_creates_distinct_entries': True,
                'same_client_id_executes_twice': True, 'native_idempotence': False}
    finally:
        await fixture.close()


async def lost_ack(root, build):
    fixture = Fixture(root, build, hold_first=True)
    observer = None
    try:
        await fixture.start()
        fixture.attach('SYNTHETIC_HUMAN_INPUT')
        await until(fixture.arrived.is_set, fixture.terminal)
        observer = UnixRPC(fixture.sock, build)
        await observer.start()
        client_id = str(uuid.uuid4())
        # Deliberately do not register a pending Future: consume/discard the ACK.
        await observer.send({'id': observer.reserve_id(), 'method': 'thread/queue/add', 'params': {
            'threadId': fixture.thread, 'clientUserMessageId': client_id,
            'input': [{'type': 'text', 'text': 'SYNTHETIC_LOST_ACK_POINTER'}]}})
        for _ in range(50):
            pending = await fixture.pending()
            if pending:
                break
            await asyncio.sleep(.02)
        assert len(pending) == 1 and pending[0]['clientUserMessageId'] == client_id
        await observer.stop()
        observer = None
        assert await fixture.pending() == pending
        fixture.release.set()
        await completed(fixture.client, fixture.terminal)
        turn = await completed(fixture.client, fixture.terminal)
        history = await fixture.history()
        matches = [t for t in history['thread']['turns'] for i in t['items']
                   if i['type'] == 'userMessage' and i['content'][0].get('text') == 'SYNTHETIC_LOST_ACK_POINTER']
        assert len(matches) == 1 and matches[0]['id'] == turn
        assert_queued_original(history, fixture.thread, pending[0], turn)
        assert await fixture.pending() == [] and len(fixture.requests) == 2
        return {'ack_deliberately_discarded': True, 'pending_recovers_association': True,
                'original_history_recovers_execution': True, 'resubmissions': 0}
    finally:
        if observer:
            await observer.stop()
        await fixture.close()


async def active_cancel(root, build):
    fixture = Fixture(root, build, hold_first=True)
    try:
        await fixture.start()
        await fixture.add('SYNTHETIC_ACTIVE_CANCEL_POINTER')
        await until(fixture.arrived.is_set)
        history = await fixture.history()
        turn = history['thread']['turns'][-1]
        assert turn['status'] == 'inProgress'
        await fixture.client.request('turn/interrupt', {'threadId': fixture.thread, 'turnId': turn['id']})
        while True:
            event = await asyncio.wait_for(fixture.client.notifications.get(), 20)
            if event['method'] == 'turn/completed':
                assert event['params']['turn']['id'] == turn['id']
                assert event['params']['turn']['status'] == 'interrupted'
                break
        fixture.release.set()
        history = await fixture.history()
        assert history['thread']['turns'][-1]['status'] == 'interrupted'
        assert await fixture.pending() == []
        await fixture.stop_backend()
        await fixture.start()
        history = await fixture.history()
        assert len(history['thread']['turns']) == 1 and history['thread']['turns'][0]['status'] == 'interrupted'
        assert await fixture.pending() == [] and len(fixture.requests) == 1
        return {'active_turn_interrupted': True, 'not_requeued_after_restart': True}
    finally:
        await fixture.close()


async def native_controls(root, build):
    fixture = Fixture(root, build, hold_first=True)
    try:
        await fixture.start()
        fixture.attach('SYNTHETIC_HUMAN_INPUT')
        await until(fixture.arrived.is_set, fixture.terminal)
        original = await fixture.add('SYNTHETIC_ORIGINAL_POINTER')
        human = await fixture.add('SYNTHETIC_QUEUED_HUMAN')
        edited = (await fixture.client.request('thread/queue/update', {
            'threadId': fixture.thread, 'queuedSubmissionId': original['id'],
            'input': [{'type': 'text', 'text': 'SYNTHETIC_OPERATOR_EDITED_POINTER'}]}))['queuedSubmission']
        assert edited['id'] == original['id'] and edited['clientUserMessageId'] == original['clientUserMessageId']
        await fixture.client.request('thread/queue/reorder', {
            'threadId': fixture.thread, 'queuedSubmissionIds': [human['id'], edited['id']]})
        assert await fixture.pending() == [human, edited]
        fixture.release.set()
        initial_turn = await completed(fixture.client, fixture.terminal)
        human_turn = await completed(fixture.client, fixture.terminal)
        edited_turn = await completed(fixture.client, fixture.terminal)
        history = await fixture.history()
        assert [t['id'] for t in history['thread']['turns']] == [initial_turn, human_turn, edited_turn]
        assert_queued_original(history, fixture.thread, human, human_turn)
        assert_queued_original(history, fixture.thread, edited, edited_turn)
        try:
            assert_queued_original(history, fixture.thread, original, edited_turn)
        except AssertionError:
            pass
        else:
            raise AssertionError('edited queue input was incorrectly counted as original delivery')
        return {'native_update_and_reorder_supported': True, 'edited_input_retains_client_id': True,
                'exact_original_check_refuses_edited_delivery': True}
    finally:
        await fixture.close()


async def approvals(root, build):
    fixture = Fixture(root, build, approval=True)
    try:
        await fixture.start()
        await fixture.add('SYNTHETIC_APPROVAL_POINTER')
        await until(lambda: bool(fixture.server_requests))
        frame = fixture.server_requests[0]
        assert frame['method'] == 'item/commandExecution/requestApproval'
        assert frame['params']['threadId'] == fixture.thread
        assert len(fixture.requests) == 1
        history = await fixture.history()
        assert 'waitingOnApproval' in history['thread']['status']['activeFlags']
        fixture.attach()
        await until(lambda: fixture.terminal.contains('Would you like to run the following command?'), fixture.terminal)
        fixture.detach()
        history = await fixture.history()
        assert 'waitingOnApproval' in history['thread']['status']['activeFlags']
        assert len(fixture.requests) == 1
        fixture.attach()
        await until(lambda: fixture.terminal.contains('Would you like to run the following command?'), fixture.terminal)
        # Decline only in this disposable native terminal; the observer never replies.
        await asyncio.sleep(.25)
        fixture.terminal.poll()
        os.write(fixture.terminal.master, b'\x1b')
        async with asyncio.timeout(20):
            while True:
                fixture.terminal.poll()
                try:
                    event = await asyncio.wait_for(fixture.client.notifications.get(), .1)
                except asyncio.TimeoutError:
                    continue
                if event['method'] == 'turn/completed':
                    assert event['params']['turn']['id'] == frame['params']['turnId']
                    break
        history = await fixture.history()
        commands = [i for t in history['thread']['turns'] for i in t['items'] if i['type'] == 'commandExecution']
        assert not (root / 'approval-executed.txt').exists()
        assert not commands or (len(commands) == 1 and commands[0]['status'] == 'declined')
        assert fixture.terminal.contains('You canceled the request')
        return {'detached_holds_for_approval': True, 'native_prompt_on_attach_and_reattach': True,
                'observer_never_replies': True, 'native_decline_prevents_execution': True}
    finally:
        await fixture.close()


async def probe(output):
    build = subprocess.check_output(['codex', '--version'], text=True).splitlines()[0]
    evidence = {'timestamp_utc': datetime.now(timezone.utc).isoformat(), 'codex_version': build,
                'os': platform.system(), 'architecture': platform.machine(),
                'scope': 'native queue reliability only; no production connector or isolation acceptance',
                'external_inference': False, 'user_auth_used': False, 'fleet_submissions': 0}
    with tempfile.TemporaryDirectory(prefix='amap-native-queue-') as directory:
        for name, scenario in [('ordinary', ordinary), ('recovery', recovery),
                               ('duplicates', duplicates), ('lost_ack', lost_ack), ('active_cancel', active_cancel),
                               ('native_controls', native_controls),
                               ('approvals', approvals)]:
            root = Path(directory) / name
            root.mkdir(mode=0o700)
            evidence[name] = await scenario(root, build)
    evidence['probe_assertions_passed'] = True
    evidence['production_ready'] = False
    output.write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps(evidence), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(probe(parser.parse_args().output))
