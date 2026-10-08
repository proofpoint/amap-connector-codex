"""Opt-in pinned three-server MCP startup check. No inference or submissions."""
import argparse
import asyncio
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import tempfile

from amap_codex.app_server import AppServerClient, AppServerDelivery
import subprocess


DEAD_ENDPOINT = 'http://127.0.0.1:9'


def chatgpt_login(home):
    """A synthetic ChatGPT login, under which Codex adds its `codex_apps`
    server. Every endpoint and proxy points at a closed local port, so
    nothing leaves the machine; returns the environment that does that."""
    def part(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b'=').decode()
    claims = {'https://api.openai.com/auth': {'chatgpt_plan_type': 'pro', 'chatgpt_account_id': 'synthetic',
              'chatgpt_user_id': 'synthetic'}, 'email': 'synthetic@example.invalid', 'exp': 4102444800}
    token = f"{part({'alg': 'none', 'typ': 'JWT'})}.{part(claims)}.synthetic"
    (home / 'auth.json').write_text(json.dumps({'OPENAI_API_KEY': None, 'last_refresh': '2099-01-01T00:00:00Z',
        'tokens': {'id_token': token, 'access_token': token, 'refresh_token': 'synthetic', 'account_id': 'synthetic'}}))
    with (home / 'config.toml').open('a') as handle:
        handle.write(f'chatgpt_base_url = "{DEAD_ENDPOINT}"\nopenai_base_url = "{DEAD_ENDPOINT}"\n')
    env = {key: value for key, value in os.environ.items() if key.lower() != 'no_proxy'}
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
        env[key] = env[key.lower()] = DEAD_ENDPOINT
    return env


async def probe(binary, model, output, chatgpt):
    build = subprocess.run([binary, '--version'], capture_output=True, text=True, check=True).stdout.split('\n')[0].strip()
    repo = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix='amap-mcp-registry-') as directory:
        root = Path(directory)
        for name in ('inbox/messages', 'peer/messages', 'outbox', 'roster', 'codex-home'):
            (root / name).mkdir(parents=True)
        # The agent's own registrations, which the thread must switch off:
        # one that would start, and one already disabled.
        (root / 'codex-home/config.toml').write_text(
            f'[mcp_servers.agent_own]\ncommand = "{repo}/bin/inbox-mcp-vol"\n'
            f'env = {{ INBOX_MESSAGE_DIR = "{root}/inbox/messages", INBOX_LANE = "mail" }}\n'
            '[mcp_servers.agent_off]\ncommand = "/nonexistent"\nenabled = false\n')
        env = {**os.environ}
        if chatgpt:
            env = chatgpt_login(root / 'codex-home')
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
            expected_version=build, env={**env, 'CODEX_HOME': str(root / 'codex-home')}),
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
                'agent_servers_switched_off': delivery.disabled_servers,
                'chatgpt_login': chatgpt,
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
    parser.add_argument('--chatgpt-login', action='store_true',
                        help='a synthetic ChatGPT login, offline: Codex then adds codex_apps')
    args = parser.parse_args()
    asyncio.run(probe(args.binary, args.model, args.output, args.chatgpt_login))
