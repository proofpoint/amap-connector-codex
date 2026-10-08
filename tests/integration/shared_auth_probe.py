"""Opt-in real-binary probe for two app-servers sharing managed authentication.

No inference, login, credential copy or logout. --refresh explicitly requests
real token rotation in an existing test Codex home. This does not establish
interactive-pane behavior or sandbox-restart recovery.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

from amap_codex.app_server import AppServerClient


def auth_metadata(home):
    """Inspect only readiness/refresh metadata; never return credential bytes."""
    path = home / 'auth.json'
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('test Codex home needs a regular existing auth.json')
    value = json.loads(path.read_text())
    if not isinstance(value.get('tokens'), dict):
        raise RuntimeError('test Codex home needs managed ChatGPT authentication')
    return {'last_refresh': value.get('last_refresh'), 'mtime_ns': path.stat().st_mtime_ns}


async def probe(binary, home, output, refresh):
    home = home.resolve(strict=True)
    auth_metadata(home)
    build = subprocess.run([binary, '--version'], capture_output=True, text=True,
                           check=True).stdout.splitlines()[0].strip()
    env = {**os.environ, 'CODEX_HOME': str(home)}
    clients = [AppServerClient([binary, 'app-server', '--listen', 'stdio://'],
                              expected_version=build, timeout=60, env=env) for _ in range(2)]
    evidence = {'scope': 'two real app-server processes sharing one existing Codex home; '
                        'no interactive-pane, inference or sandbox-restart attestation',
                'timestamp_utc': datetime.now(timezone.utc).isoformat(),
                'codex_version': build, 'shared_home': True,
                'refresh_requested': refresh, 'inference': False, 'submissions': 0}
    try:
        starts = await asyncio.gather(*(c.start() for c in clients), return_exceptions=True)
        if any(isinstance(r, BaseException) for r in starts):
            raise RuntimeError('one or both test app-servers failed initialization')
        accounts = await asyncio.gather(*(c.request('account/read', {'refreshToken': False})
                                          for c in clients))
        if any((r.get('account') or {}).get('type') != 'chatgpt' for r in accounts):
            raise RuntimeError('both processes must report managed ChatGPT authentication')
        if accounts[0]['account'] != accounts[1]['account']:
            raise RuntimeError('the two processes disagree about the shared account')
        evidence['both_accounts_available'] = True
        if refresh:
            before = auth_metadata(home)
            # Both initialized processes hold their own auth manager; issue the
            # two refresh requests together rather than starting a fresh reader.
            results = await asyncio.gather(*(c.request('account/read', {'refreshToken': True})
                                             for c in clients), return_exceptions=True)
            evidence['refresh_results'] = [
                'chatgpt' if isinstance(r, dict) and r.get('account') == accounts[0]['account']
                else 'failed' for r in results]
            after = auth_metadata(home)
            evidence['refresh_metadata_changed'] = after != before
            evidence['refresh_succeeded'] = (evidence['refresh_results'] == ['chatgpt', 'chatgpt']
                                             and evidence['refresh_metadata_changed'])
        reread = await asyncio.gather(*(c.request('account/read', {'refreshToken': False})
                                       for c in clients), return_exceptions=True)
        evidence['both_accounts_available_after'] = all(
            isinstance(r, dict) and r.get('account') == accounts[0]['account']
            for r in reread)
    finally:
        await asyncio.gather(*(c.stop() for c in clients))
    # Evidence contains no home path, account identity, tokens or server errors.
    output.write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps(evidence))
    if not evidence['both_accounts_available_after'] or (refresh and not evidence['refresh_succeeded']):
        raise RuntimeError('shared authentication probe did not pass; inspect test-instance diagnostics')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', default='codex')
    parser.add_argument('--codex-home', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--refresh', action='store_true',
                        help='force concurrent real refreshes in the provided test Codex home')
    args = parser.parse_args()
    try:
        asyncio.run(probe(args.binary, args.codex_home, args.output, args.refresh))
    except Exception:
        # RPC exceptions can contain account details. Keep console failures
        # generic; runtime stderr remains with the local operator.
        raise SystemExit('shared-auth probe failed; inspect the local test instance')
