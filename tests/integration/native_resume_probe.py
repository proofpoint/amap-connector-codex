"""Real native TUI resume against a rejecting local provider, no external inference.

Disposable unauthenticated home, synthetic history/MCP namespace, no fleet sends.
Linux/macOS PTY probe; this is not deployment or isolation acceptance.
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
import select
import signal
import struct
import subprocess
import tempfile
import termios
import threading
import time
from unittest.mock import patch

from amap_codex.app_server import AppServerClient, AppServerDelivery
from amap_codex.config import Config, Lane
from amap_codex.interactive import interactive_argv, toml_value
from amap_codex.journal import Journal


async def probe(output, diagnostics=None):
    repo = Path(__file__).resolve().parents[2]
    build = subprocess.check_output(['codex', '--version'], text=True).splitlines()[0]
    with tempfile.TemporaryDirectory(prefix='amap-native-resume-') as temporary:
        root = Path(temporary)
        captured = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                captured.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"synthetic local refusal"}}')
            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        home = root / 'codex-home'
        home.mkdir()
        (home / 'config.toml').write_text(
            'model_provider="offline"\nmodel="synthetic-model"\ncheck_for_update_on_startup=false\n'
            '[features]\napps=false\n[model_providers.offline]\nname="Synthetic local provider"\n'
            f'wire_api="responses"\nbase_url="http://127.0.0.1:{server.server_port}/v1"\n'
            'supports_websockets=false\nrequires_openai_auth=false\nrequest_max_retries=0\n'
            f'[projects.{json.dumps(str(root))}]\ntrust_level="trusted"\n')
        env = {'PATH': os.environ['PATH'], 'CODEX_HOME': str(home), 'TERM': 'xterm-256color',
               'LANG': 'C.UTF-8', 'NO_PROXY': '127.0.0.1,localhost', 'no_proxy': '127.0.0.1,localhost'}
        for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
            env[key] = env[key.lower()] = 'http://127.0.0.1:9'
        def tui(argv):
            before = len(captured)
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 120, 0, 0))
            process = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave,
                                       cwd=root, env=env, start_new_session=True)
            os.close(slave)
            deadline = time.monotonic() + 30
            try:
                while time.monotonic() < deadline and process.poll() is None and len(captured) == before:
                    if select.select([master], [], [], .1)[0]:
                        try:
                            chunk = os.read(master, 65536)
                        except OSError:
                            break
                        if b'\x1b[6n' in chunk:
                            os.write(master, b'\x1b[1;1R')
                assert len(captured) > before, 'native TUI did not reach the local rejecting provider'
                return captured[-1]
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                os.close(master)
        try:
            with patch.dict(os.environ, env, clear=True):
                native = ['codex', '--no-daemon', '--no-alt-screen', '--cd', str(root),
                          '-a', 'never', '-s', 'read-only']
                tui([*native, '-c', 'developer_instructions="OLD_SYNTHETIC_GUIDANCE"',
                     '--', 'ORIGINAL_SYNTHETIC_USER_HISTORY'])
                # Create a more recent supervisor/editor thread; a blind
                # native --last must not be allowed to select it instead.
                delivery = AppServerDelivery(AppServerClient(
                    ['codex', 'app-server', '--listen', 'stdio://'], expected_version=build),
                    model=None, cwd=str(root), sandbox='read-only', approval_policy='never',
                    instructions='SUPERVISOR_SYNTHETIC_GUIDANCE')
                try:
                    supervisor = await delivery.start_or_resume()
                    await delivery.client.request('thread/name/set', {'threadId': supervisor, 'name': 'Synthetic supervisor'})
                    listed = await delivery.client.request('thread/list', {'sourceKinds': ['cli'], 'cwd': str(root), 'limit': 1})
                    target = listed['data'][0]['id']
                    assert target != supervisor
                finally:
                    await delivery.stop()
                for name in ('mail/notices', 'mail/messages', 'peer/messages', 'outbox', 'roster', 'state'):
                    (root / name).mkdir(parents=True, exist_ok=True, mode=0o700)
                instructions = root / 'instructions.md'
                instructions.write_text('PROTECTED_FLEET_POLICY. Do not send messages in this synthetic probe.')
                servers = {}
                for name, tree, lane in [('inbox', 'mail', 'mail'), ('delegation', 'peer', 'peer')]:
                    servers[name] = {'command': str(repo / 'bin/inbox-mcp-vol'), 'required': True,
                        'enabled_tools': ['list_messages', 'read_message', 'read_attachment'],
                        'default_tools_approval_mode': 'approve', 'env': {
                            'INBOX_MESSAGE_DIR': str(root / tree / 'messages'), 'INBOX_LANE': lane}}
                servers['inbox_submit'] = {'command': str(repo / 'bin/inbox-submit'), 'args': ['mcp'],
                    'required': True, 'enabled_tools': ['submit', 'submit_result', 'peers'],
                    'default_tools_approval_mode': 'approve', 'env': {
                        'OUTBOX_DIR': str(root / 'outbox'), 'AMAP_ROSTER_DIR': str(root / 'roster'),
                        'AMAP_SELF': 'synthetic@example.invalid'}}
                trusted = root / 'trusted.toml'
                trusted.write_text('mcp_servers=' + toml_value(servers) + '\n')
                config = Config('synthetic', 'synthetic@example.invalid',
                    ['codex', 'app-server', '--listen', 'stdio://'],
                    [Lane('mail', root / 'mail/notices', root / 'mail/messages', root / 'claim')],
                    root / 'state', None, str(root), 'read-only', 'never', instructions,
                    codex_version=build, trusted_config_file=trusted).validate(create_state=False)
                with Journal(config.state_dir, config.instance_id, config.fingerprint(), build, None) as journal:
                    journal.bind_thread(supervisor)
                journal_before = (root / 'state/journal.sqlite3').read_bytes()
                # The activity MCP needs an actual protected config to read.
                doc = {'instance_id': config.instance_id, 'self_address': config.self_address,
                    'launch_argv': config.launch_argv, 'codex_version': build,
                    'state_dir': str(config.state_dir), 'cwd': str(root), 'sandbox': 'read-only',
                    'approval_policy': 'never', 'operator_instructions': str(instructions),
                    'trusted_config_file': str(trusted), 'lanes': {'mail': {
                        'notice_dir': str(root / 'mail/notices'), 'message_dir': str(root / 'mail/messages'),
                        'claim_path': str(root / 'claim')}}}
                (root / 'connector.toml').write_text('\n'.join(k + '=' + toml_value(v) for k, v in doc.items()) + '\n')
                argv = await interactive_argv(config, root / 'connector.toml', outbox=root / 'outbox',
                    native_argv=['codex', 'resume', '--last', *native[1:], '--model', 'resumed-synthetic-model',
                                 '--', 'RESUMED_SYNTHETIC_USER_PROMPT'],
                    prepare_resume=True)
                assert argv[1:3] == ['resume', target]
                request = tui(argv)
                assert request['model'] == 'resumed-synthetic-model'
                text = json.dumps(request)
                if diagnostics is not None:
                    diagnostics.write_text(json.dumps(request, indent=2))
                    diagnostics.chmod(0o600)
                assert 'ORIGINAL_SYNTHETIC_USER_HISTORY' in text
                assert 'RESUMED_SYNTHETIC_USER_PROMPT' in text
                assert 'PROTECTED_FLEET_POLICY' in text and 'separate supervisor' in text, {
                    'policy': 'PROTECTED_FLEET_POLICY' in text,
                    'guidance': 'separate supervisor' in text,
                    'old_guidance': 'OLD_SYNTHETIC_GUIDANCE' in text}
                developer_texts = ['\n'.join(c.get('text', '') for c in item.get('content', []))
                                  for item in request['input'] if item.get('role') == 'developer']
                assert max(i for i, value in enumerate(developer_texts) if 'PROTECTED_FLEET_POLICY' in value) > max(
                    i for i, value in enumerate(developer_texts) if 'OLD_SYNTHETIC_GUIDANCE' in value)
                tool_text = json.dumps(request.get('tools'))
                assert all(name in tool_text for name in ('inbox', 'delegation', 'inbox_submit', 'amap_fleet'))
                assert (root / 'state/journal.sqlite3').read_bytes() == journal_before
                assert not (root / 'claim').exists() and not list((root / 'outbox').glob('req-*.json'))
                assert not (home / 'auth.json').exists()
                contexts = [event['payload'] for file in (home / 'sessions').rglob('*' + target + '*.jsonl')
                            for line in file.read_text().splitlines()
                            if (event := json.loads(line)).get('type') == 'turn_context']
                assert contexts[-1]['model'] == 'resumed-synthetic-model'
                assert contexts[-1]['approval_policy'] == 'never'
                assert contexts[-1]['sandbox_policy']['type'] == 'read-only'
                result = {'timestamp_utc': datetime.now(timezone.utc).isoformat(), 'codex_version': build,
                    'os': platform.system(), 'architecture': platform.machine(),
                    'scope': 'actual native TUI resume, disposable home and rejecting local provider; no host deployment attestation',
                    'external_inference': False, 'user_auth_used': False, 'submissions': 0,
                    'cli_history_resumed': True, 'last_resolved_to_explicit_cli_id': True,
                    'newer_supervisor_excluded': True, 'original_history_present': True,
                    'protected_guidance_applied': True, 'four_amap_tool_sets_present': True,
                    'native_model_preserved': True, 'sandbox_read_only': True, 'approval_never': True,
                    'journal_and_claims_unchanged': True, 'passed': True}
                output.write_text(json.dumps(result, indent=2) + '\n')
                print(json.dumps(result))
        finally:
            server.shutdown()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--diagnostics', type=Path)
    args = parser.parse_args()
    asyncio.run(probe(args.output, args.diagnostics))
