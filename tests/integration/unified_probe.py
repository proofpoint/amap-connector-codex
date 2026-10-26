"""Feasibility probe: one live thread, native TUI and controller submissions.

Disposable Codex home and deterministic localhost provider. No credentials,
external inference, router, production journal or running workspace is used.
This deliberately does not expose a production unified delivery mode.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import fcntl
import http.server
import json
import os
from pathlib import Path
import platform
import pty
import re
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time

from amap_codex.app_server import AppServerDelivery
from _unified_evidence import assert_original_pointer, assert_retained_history, history_snapshot
from _unified_ws import UnixRPC


def response_stream(number, model, text):
    item = {'id': f'message_{number}', 'type': 'message', 'role': 'assistant',
            'status': 'completed', 'content': [{'type': 'output_text', 'text': text, 'annotations': []}]}
    response = {'id': f'resp_{number}', 'object': 'response', 'created_at': 1,
                'status': 'completed', 'model': model, 'output': [item],
                'usage': {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}}
    events = [
        {'type': 'response.created', 'response': {**response, 'status': 'in_progress', 'output': []}},
        {'type': 'response.output_item.added', 'output_index': 0,
         'item': {**item, 'status': 'in_progress', 'content': []}},
        {'type': 'response.content_part.added', 'item_id': item['id'], 'output_index': 0,
         'content_index': 0, 'part': {'type': 'output_text', 'text': '', 'annotations': []}},
        {'type': 'response.output_text.delta', 'item_id': item['id'], 'output_index': 0,
         'content_index': 0, 'delta': text},
        {'type': 'response.output_text.done', 'item_id': item['id'], 'output_index': 0,
         'content_index': 0, 'text': text},
        {'type': 'response.content_part.done', 'item_id': item['id'], 'output_index': 0,
         'content_index': 0, 'part': item['content'][0]},
        {'type': 'response.output_item.done', 'output_index': 0, 'item': item},
        {'type': 'response.completed', 'response': response},
    ]
    return ''.join('data: ' + json.dumps(event) + '\n\n' for event in events).encode()


class Terminal:
    def __init__(self, argv, env, cwd):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 140, 0, 0))
        # A PTY fd alone does not establish a controlling terminal after setsid.
        # Native keyboard events use /dev/tty; claim it before exec, without a
        # preexec_fn in this multithreaded synthetic-provider process.
        attach_tty = ('import fcntl,os,sys,termios; '
                      'fcntl.ioctl(0,termios.TIOCSCTTY,0); '
                      'os.execvpe(sys.argv[1],sys.argv[1:],os.environ)')
        self.process = subprocess.Popen([sys.executable, '-c', attach_tty, *argv],
                                        stdin=slave, stdout=slave, stderr=slave,
                                        cwd=cwd, env=env, start_new_session=True)
        os.close(slave)
        os.set_blocking(self.master, False)
        self.output = bytearray()

    def poll(self):
        try:
            while data := os.read(self.master, 65536):
                self.output.extend(data)
                if b'\x1b[6n' in data:
                    os.write(self.master, b'\x1b[1;1R')
        except (BlockingIOError, OSError):
            pass
        if len(self.output) > 2 * 1024 * 1024:
            raise RuntimeError('synthetic terminal output exceeded probe bound')

    def contains(self, marker):
        self.poll()
        text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', self.output.decode(errors='replace'))
        return marker in text

    def close(self):
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait()
        os.close(self.master)


async def until(predicate, terminal=None, timeout=20):
    deadline = time.monotonic() + timeout
    while not predicate():
        if terminal is not None:
            terminal.poll()
            if terminal.process.poll() is not None:
                raise RuntimeError('native TUI exited before the expected shared-thread event')
        if time.monotonic() >= deadline:
            raise TimeoutError('shared-thread probe condition did not arrive')
        await asyncio.sleep(.02)


async def completed(client, terminal=None):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if terminal is not None:
            terminal.poll()
        try:
            event = await asyncio.wait_for(client.notifications.get(), .1)
        except asyncio.TimeoutError:
            continue
        if event['method'] == 'turn/completed':
            assert event['params']['turn']['status'] == 'completed'
            return event['params']['turn']['id']
    raise TimeoutError('controller did not receive the shared-thread completion')


async def probe(output):
    build = subprocess.check_output(['codex', '--version'], text=True).splitlines()[0]
    with tempfile.TemporaryDirectory(prefix='amap-unified-') as directory:
        root = Path(directory)
        home = root / 'home'
        home.mkdir()
        sock = root / 'backend.sock'
        arrived, release = threading.Event(), threading.Event()
        requests = []
        class Provider(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(request)
                number = len(requests)
                if number == 1:
                    arrived.set()
                    if not release.wait(30):
                        self.send_error(504)
                        return
                marker = f'UNIFIED_RESPONSE_{number}'
                wire = response_stream(number, request['model'], marker)
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(wire)))
                self.end_headers()
                self.wfile.write(wire)
                self.wfile.flush()
            def log_message(self, *args):
                pass
        provider = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Provider)
        threading.Thread(target=provider.serve_forever, daemon=True).start()
        (home / 'config.toml').write_text(
            'model_provider="offline"\nmodel="synthetic-model"\ncheck_for_update_on_startup=false\n'
            '[features]\napps=false\n[model_providers.offline]\nname="Synthetic local provider"\n'
            f'base_url="http://127.0.0.1:{provider.server_port}/v1"\nwire_api="responses"\n'
            'supports_websockets=false\nrequires_openai_auth=false\nrequest_max_retries=0\n'
            f'[projects.{json.dumps(str(root))}]\ntrust_level="trusted"\n')
        env = {'PATH': os.environ['PATH'], 'CODEX_HOME': str(home), 'TERM': 'xterm-256color',
               'LANG': 'C.UTF-8', 'NO_PROXY': '127.0.0.1,localhost', 'no_proxy': '127.0.0.1,localhost'}
        for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
            env[key] = env[key.lower()] = 'http://127.0.0.1:9'
        log = (root / 'backend.log').open('wb')
        backend = subprocess.Popen(['codex', 'app-server', '--listen', 'unix://' + str(sock)],
                                   stdout=subprocess.DEVNULL, stderr=log, env=env,
                                   cwd=root, start_new_session=True)
        delivery = None
        terminal = None
        try:
            await until(lambda: sock.exists() or backend.poll() is not None)
            assert backend.poll() is None, 'synthetic shared backend exited'
            sock.chmod(0o600)
            client = UnixRPC(sock, build)
            delivery = AppServerDelivery(client, model=None, cwd=str(root), sandbox='read-only',
                approval_policy='never', instructions='Single synthetic conversation. No external actions or sends.')
            thread = await delivery.start_or_resume()
            await client.request('thread/name/set', {'threadId': thread, 'name': 'Synthetic unified conversation'})
            ui = ['codex', '--remote', 'unix://' + str(sock), '--no-alt-screen', 'resume', thread]
            terminal = Terminal([*ui, '--', 'UNIFIED_HUMAN_INPUT'], env, root)
            await until(arrived.is_set, terminal)
            active = (await client.request('thread/read', {'threadId': thread, 'includeTurns': False}))['thread']
            assert active['status']['type'] == 'active'
            assert client.turn_starts == 0, 'human turn must come from the native client'
            release.set()
            human_turn = await completed(client, terminal)
            await until(lambda: terminal.contains('UNIFIED_RESPONSE_1'), terminal)
            payloads = []
            for lane, number in [('peer', 2), ('mail', 3)]:
                payload = {'event_id': 'synthetic_event_' + lane, 'lane': lane,
                           'notice_id': 'synthetic_notice_' + lane}
                result = await delivery.deliver(payload, client.reserve_id())
                assert result.state == 'accepted'
                payloads.append((payload, result.turn_id))
                assert await completed(client, terminal) == result.turn_id
                await until(lambda: terminal.contains(f'UNIFIED_RESPONSE_{number}'), terminal)
            history = await client.request('thread/read', {'threadId': thread, 'includeTurns': True})
            turns = history['thread']['turns']
            assert len(turns) == 3 and turns[0]['id'] == human_turn
            for payload, accepted_turn in payloads:
                assert_original_pointer(history, payload, accepted_turn)
            snapshot = history_snapshot(history)
            terminal.close()
            terminal = Terminal(ui, env, root)
            await until(lambda: terminal.contains('UNIFIED_RESPONSE_3'), terminal)
            await asyncio.sleep(.2)
            history = await client.request('thread/read', {'threadId': thread, 'includeTurns': True})
            assert len(history['thread']['turns']) == len(requests) == 3
            assert_retained_history(snapshot, history)
            loaded = await client.request('thread/loaded/list', {})
            assert loaded['data'] == [thread]
            saved = await client.request('thread/list', {'cwd': str(root), 'limit': 10,
                'sourceKinds': ['cli', 'vscode', 'appServer', 'exec', 'unknown']})
            assert [item['id'] for item in saved['data']] == [thread]
            assert backend.poll() is None
            assert not (home / 'auth.json').exists()
            evidence = {'timestamp_utc': datetime.now(timezone.utc).isoformat(),
                'codex_version': build, 'os': platform.system(), 'architecture': platform.machine(),
                'scope': 'protocol feasibility only; scripted serialized inputs, no production arbitration or router',
                'external_inference': False, 'user_auth_used': False, 'fleet_submissions': 0,
                'one_backend': True, 'one_thread': True, 'human_turn_observed_by_controller': True,
                'native_human_busy_state_visible': True, 'peer_pointer_preserved': True,
                'mail_pointer_preserved': True, 'controller_responses_visible_in_native_tui': True,
                'complete_pointers_in_accepted_turns': True, 'exact_ordered_history_retained': True,
                'reattach_same_history_without_replay': True, 'turn_count': 3, 'passed': True}
            output.write_text(json.dumps(evidence, indent=2) + '\n')
            print(json.dumps(evidence))
        finally:
            release.set()
            if terminal is not None:
                terminal.close()
            if delivery is not None:
                await delivery.stop()
            if backend.poll() is None:
                os.killpg(backend.pid, signal.SIGTERM)
            try:
                backend.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(backend.pid, signal.SIGKILL)
                backend.wait()
            log.close()
            provider.shutdown()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(probe(parser.parse_args().output))
