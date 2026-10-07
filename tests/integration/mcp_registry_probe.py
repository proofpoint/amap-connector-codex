"""Opt-in pinned three-server MCP startup check. No inference or submissions."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import tempfile

from amap_codex.app_server import AppServerClient, AppServerDelivery
import subprocess


async def probe(binary, model, output):
    build = subprocess.run([binary, '--version'], capture_output=True, text=True, check=True).stdout.split('\n')[0].strip()
    repo = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix='amap-mcp-registry-') as directory:
        root = Path(directory)
        for name in ('inbox/messages', 'peer/messages', 'outbox', 'roster', 'codex-home'):
            (root / name).mkdir(parents=True)
        # Empty home keeps ambient user registrations out of this host probe.
        (root / 'codex-home/config.toml').write_text('')
        servers = {}
        for name, tree, lane in [('inbox', 'inbox', 'mail'), ('delegation', 'peer', 'peer')]:
            servers[name] = {'command': str(repo / 'bin/inbox-mcp-vol'), 'required': True,
                'enabled_tools': ['list_messages', 'read_message', 'read_attachment'],
                'default_tools_approval_mode': 'approve',
                'env': {'INBOX_MESSAGE_DIR': str(root / tree / 'messages'), 'INBOX_LANE': lane}}
        servers['inbox_submit'] = {'command': str(repo / 'bin/inbox-submit'), 'args': ['mcp'],
            'required': True, 'enabled_tools': ['submit', 'submit_result', 'peers'],
            'default_tools_approval_mode': 'approve', 'env': {
                'OUTBOX_DIR': str(root / 'outbox'), 'AMAP_ROSTER_DIR': str(root / 'roster'),
                'AMAP_SELF': 'synthetic@example.invalid'}}
        config = {'mcp_servers': servers, 'model_reasoning_effort': 'low'}
        delivery = AppServerDelivery(AppServerClient([binary, 'app-server', '--listen', 'stdio://'],
            expected_version=build, env={**os.environ, 'CODEX_HOME': str(root / 'codex-home')}),
            model=model, cwd=str(root), sandbox='read-only', approval_policy='never',
            instructions='Synthetic startup only. No model turn or submission is authorized.', trusted_config=config)
        try:
            thread = await delivery.start_or_resume()
            assert thread
            evidence = {'scope': 'pinned host MCP startup only; no isolation/router attestation',
                'timestamp_utc': datetime.now(timezone.utc).isoformat(), 'os': platform.system(),
                'architecture': platform.machine(), 'expected_version': build,
                'user_agent': delivery.client.initialize_result.get('userAgent'),
                'thread_created': True, 'exact_server_and_tool_registry': True,
                'inference': False, 'submissions': 0}
            Path(output).write_text(json.dumps(evidence, indent=2) + '\n')
            print(json.dumps(evidence))
        finally:
            await delivery.stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', default='codex')
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    asyncio.run(probe(args.binary, args.model, args.output))
