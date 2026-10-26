"""Measure restart of an unused persistent thread, without inference or auth.

Uses a disposable Codex home and closed local endpoints. No fleet or user
configuration, journal, claims or session history is read or changed.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile

from amap_codex.app_server import AppServerClient, AppServerDelivery, RPCError
from amap_codex.journal import Journal


async def probe(binary, output):
    build = subprocess.check_output([binary, '--version'], text=True).splitlines()[0]
    with tempfile.TemporaryDirectory(prefix='amap-empty-resume-') as temporary:
        root = Path(temporary)
        home = root / 'codex-home'
        home.mkdir()
        (home / 'config.toml').write_text(
            'check_for_update_on_startup=false\n'
            'model_provider="synthetic_offline"\nmodel="synthetic-model"\n'
            'chatgpt_base_url="http://127.0.0.1:9"\n'
            'openai_base_url="http://127.0.0.1:9"\n'
            '[model_providers.synthetic_offline]\nname="Synthetic offline provider"\n'
            'base_url="http://127.0.0.1:9/v1"\nsupports_websockets=false\n')
        env = {'PATH': os.environ['PATH'], 'CODEX_HOME': str(home),
               'OPENAI_BASE_URL': 'http://127.0.0.1:9'}
        for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
            env[name] = env[name.lower()] = 'http://127.0.0.1:9'

        def delivery():
            return AppServerDelivery(AppServerClient(
                [binary, 'app-server', '--listen', 'stdio://'],
                expected_version=build, env=env), model=None, cwd=str(root),
                sandbox='read-only', approval_policy='never',
                instructions='Synthetic startup only. No model turn is authorized.')

        first = delivery()
        second = delivery()
        try:
            thread = await first.start_or_resume()
            await asyncio.sleep(2)
            files = len(list(home.glob('sessions/**/*.jsonl')))
        finally:
            await first.stop()
        result = {'timestamp_utc': datetime.now(timezone.utc).isoformat(),
                  'codex_version': build, 'os': platform.system(),
                  'architecture': platform.machine(),
                  'scope': 'unused-thread restart in a disposable unauthenticated home; no fleet or native TUI acceptance',
                  'initial_start_accepted': True, 'rollout_files_before_restart': files,
                  'inference': False, 'submissions': 0}
        try:
            await second.start_or_resume(thread)
            result['resume_accepted'] = True
        except RPCError as error:
            result.update(resume_accepted=False, error_code=error.code,
                          error_category=error.reason,
                          diagnostic=str(error))
        finally:
            await second.stop()
        assert first.client.turn_starts == second.client.turn_starts == 0
        with Journal(root / 'probe.sqlite3', 'synthetic', 'synthetic', build, None) as journal:
            journal.bind_thread(thread)
            replacements = []
            for _ in range(2):
                current = delivery()
                try:
                    before = journal.thread_id
                    replacement = await current.start_or_resume(
                        before, release_unused=journal.release_unused_thread)
                    journal.bind_thread(replacement)
                    assert replacement != before
                    assert current.client.turn_starts == 0
                    replacements.append(replacement)
                finally:
                    await current.stop()
            assert journal.db.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 0
            assert journal.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0] == 0
            assert journal.db.execute('SELECT COUNT(*) FROM operator_runs').fetchone()[0] == 0
            assert journal.db.execute("SELECT COUNT(*) FROM audit WHERE action='unused_thread_released'").fetchone()[0] == 2
            result.update(unused_thread_recovery=True, consecutive_idle_restarts=len(replacements),
                          release_audit_records=2, journal_preserved=True)
        assert not (home / 'auth.json').exists()
        result.update(turn_start_requests=0, user_auth_used=False)
        output.write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', default='codex')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(probe(args.binary, args.output))
