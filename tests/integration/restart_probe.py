"""Opt-in real supervisor recovery probe with a persistent synthetic namespace.

prepare waits for a real accepted turn to enter a bounded workspace command.
Restart the dedicated isolation environment externally, then run verify on the
same root. --fault-app-server provides a smaller process-only crash experiment.
This harness never submits to a router or touches existing fleet lane trees.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import signal
import sys
import time

from amap_codex.config import Config, Lane
from amap_codex.operator import enqueue, find_operator
from amap_codex.supervisor import Supervisor, atomic_json


def configuration(root):
    choices = json.loads((root/'probe-config.json').read_text())
    return Config('synthetic-restart-probe', 'synthetic@example.invalid',
                  [choices['binary'], 'app-server', '--listen', 'stdio://'],
                  [Lane('mail', root/'mail/notices', root/'mail/messages', root/'mail/consumer.claim')],
                  root/'state', choices['model'], str(root), choices['sandbox'], 'never',
                  root/'instructions.md', codex_version=choices['version'],
                  trusted_config_file=root/'trusted.toml', operator_kickoff_enabled=True,
                  rpc_timeout_seconds=60, shutdown_grace_seconds=1).validate()


def prepare_files(root, binary, model, sandbox):
    import subprocess
    root.mkdir(mode=0o700)  # Refuse reuse, including a real fleet namespace.
    for name in ('mail/notices', 'mail/messages', 'peer/messages', 'outbox', 'roster', 'state'):
        (root/name).mkdir(parents=True, mode=0o700)
    version = subprocess.run([binary, '--version'], capture_output=True, text=True,
                             check=True).stdout.splitlines()[0].strip()
    atomic_json(root/'probe-config.json', {'binary': binary, 'model': model,
                                         'version': version, 'sandbox': sandbox})
    (root/'instructions.md').write_text('Perform only the synthetic operator recovery test. '
        'Run its specified workspace command once. Do not read other files, use MCP tools, '
        'send messages or access credentials. Return only AMAP_RESTART_PROBE_DONE after it exits.')
    (root/'instructions.md').chmod(0o600)
    repo = Path(__file__).resolve().parents[2]
    servers = {}
    for name, tree, lane in [('inbox', 'mail', 'mail'), ('delegation', 'peer', 'peer')]:
        servers[name] = {'command': str(repo/'bin/inbox-mcp-vol'), 'required': True,
            'enabled_tools': ['list_messages', 'read_message', 'read_attachment'],
            'default_tools_approval_mode': 'approve',
            'env': {'INBOX_MESSAGE_DIR': str(root/tree/'messages'), 'INBOX_LANE': lane}}
    servers['inbox_submit'] = {'command': str(repo/'bin/inbox-submit'), 'args': ['mcp'],
        'required': True, 'enabled_tools': ['submit', 'submit_result', 'peers'],
        'default_tools_approval_mode': 'approve', 'env': {'OUTBOX_DIR': str(root/'outbox'),
        'AMAP_ROSTER_DIR': str(root/'roster'), 'AMAP_SELF': 'synthetic@example.invalid'}}
    text = 'model_reasoning_effort = "low"\n'
    for name, values in servers.items():
        text += f'\n[mcp_servers.{name}]\n'
        for key, value in values.items():
            encoded = ('{' + ', '.join(f'{json.dumps(k)} = {json.dumps(v)}' for k, v in value.items()) + '}'
                       if isinstance(value, dict) else json.dumps(value))
            text += f'{key} = {encoded}\n'
    (root/'trusted.toml').write_text(text)
    (root/'trusted.toml').chmod(0o600)
    (root/'work.py').write_text('''import os,time
from pathlib import Path
root=Path(__file__).resolve().parent
fd=os.open(root/'executions',os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
os.write(fd,b'entered\\n'); os.fsync(fd); os.close(fd)
(root/'entered').touch()
deadline=time.monotonic()+180
while not (root/'release').exists() and time.monotonic()<deadline:
    time.sleep(0.1)
print('AMAP_RESTART_PROBE_DONE')
''')


async def prepare(root, binary, model, sandbox, fault):
    prepare_files(root, binary, model, sandbox)
    config = configuration(root)
    command = shlex.join([sys.executable, str(root/'work.py')])
    request = enqueue(config, 'restart-probe', 'This is an authorized synthetic recovery test. '
                      f'Run exactly this workspace command once: {command}. '
                      'It waits up to 180 seconds for the test runner. Do not send messages or use other tools.')
    supervisor = Supervisor(config)
    try:
        await supervisor.start()
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            await supervisor.tick()
            row = next((r for r in supervisor.journal.operator_runs() if r['run_id'] == request['run_id']), None)
            if row and row['state'] == 'accepted' and (root/'entered').exists():
                atomic_json(root/'checkpoint.json', {'thread_id': supervisor.journal.thread_id,
                    'turn_id': row['turn_id'], 'run_id': row['run_id'],
                    'instruction_hash': request['instruction_hash'],
                    'fingerprint': config.fingerprint(), 'process_fault': fault})
                print(json.dumps({'phase': 'active', 'turn_accepted': True,
                                  'workspace_command_entered': True, 'checkpoint_saved': True}), flush=True)
                if fault:
                    # This process owns the newly launched app-server group.
                    os.killpg(supervisor.delivery.client.process.pid, signal.SIGKILL)
                    (root/'release').touch()  # Bound any command child outside that group.
                    await asyncio.sleep(0.2)
                    os._exit(77)  # Intentionally retain crash records and stale claims.
                while row['state'] == 'accepted':
                    await supervisor.tick()
                    await asyncio.sleep(0.2)
                    row = supervisor.journal.operator_runs()[0]
                raise RuntimeError('turn ended before external restart; repeat with a fresh probe root')
            if row and row['state'] in {'finished', 'uncertain'}:
                raise RuntimeError('turn ended or became uncertain before the workspace command entered')
            await asyncio.sleep(0.1)
        raise RuntimeError('probe did not reach an active workspace command')
    finally:
        (root/'release').touch()
        await supervisor.close()


async def verify(root, output, before, after):
    checkpoint = json.loads((root/'checkpoint.json').read_text())
    if not checkpoint['process_fault'] and (not before or not after or before == after):
        raise RuntimeError('provide distinct runtime identities measured by the operator restart runner')
    config = configuration(root)
    if config.fingerprint() != checkpoint['fingerprint']:
        raise RuntimeError('probe configuration changed across restart')
    (root/'release').touch()
    supervisor = Supervisor(config)
    try:
        await supervisor.start()
        # Two idle cycles must not dispatch the retained kickoff again.
        for _ in range(2):
            await supervisor.tick()
            await asyncio.sleep(config.poll_interval_ms / 1000)
        status = supervisor.journal.status()
        row = next(r for r in supervisor.journal.operator_runs() if r['run_id'] == checkpoint['run_id'])
        history = await supervisor.delivery.observe()
        matches = [find_operator({'thread': {'turns': [turn]}}, checkpoint['run_id'])
                   for turn in history.get('thread', {}).get('turns', [])]
        matches = [m for m in matches if m is not None]
        executions = (root/'executions').read_text().splitlines()
        submissions = sum(item.get('type') == 'mcpToolCall' and item.get('tool') == 'submit'
                          for turn in history.get('thread', {}).get('turns', [])
                          for item in turn.get('items', []))
        evidence = {'timestamp_utc': datetime.now(timezone.utc).isoformat(),
            'codex_version': config.codex_version,
            'scope': ('real app-server/controller crash inside isolation; no sandbox restart or router attestation'
                      if checkpoint['process_fault'] else 'operator-attested isolation restart with a synthetic supervisor namespace; no router attestation'),
            'runtime_identity_changed': bool(before and after and before != after),
            'same_thread': status['thread_id'] == checkpoint['thread_id'],
            'original_input_matches': len(matches) == 1 and matches[0][0] == checkpoint['turn_id']
                                      and matches[0][2] == checkpoint['instruction_hash'],
            'operator_state': row['state'], 'execution_status': row['execution_status'],
            'new_turns_dispatched': supervisor.delivery.client.turn_starts,
            'workspace_command_executions': len(executions), 'submissions': submissions}
        evidence['passed'] = (evidence['same_thread'] and evidence['original_input_matches']
            and evidence['operator_state'] in {'finished', 'uncertain'}
            and evidence['new_turns_dispatched'] == 0 and len(executions) == 1 and submissions == 0)
        output.write_text(json.dumps(evidence, indent=2) + '\n')
        print(json.dumps(evidence))
        if not evidence['passed']:
            raise RuntimeError('live recovery gate did not pass; retain checkpoint and journal for diagnosis')
    finally:
        await supervisor.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    start = commands.add_parser('prepare')
    start.add_argument('--root', type=Path, required=True)
    start.add_argument('--binary', default='codex')
    start.add_argument('--model')
    start.add_argument('--sandbox', choices=['read-only', 'workspace-write', 'danger-full-access'],
                       default='workspace-write', help='probe permission profile; use the outer isolation policy')
    start.add_argument('--fault-app-server', action='store_true')
    check = commands.add_parser('verify')
    check.add_argument('--root', type=Path, required=True)
    check.add_argument('--output', type=Path, required=True)
    check.add_argument('--runtime-before')
    check.add_argument('--runtime-after')
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        if args.command == 'prepare':
            asyncio.run(prepare(root, args.binary, args.model, args.sandbox, args.fault_app_server))
        else:
            asyncio.run(verify(root, args.output, args.runtime_before, args.runtime_after))
    except Exception:
        raise SystemExit('restart probe failed; retain the private probe root for local diagnosis')
