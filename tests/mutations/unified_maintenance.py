"""Stopped-service maintenance guards tested only on independent scratch copies."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    repo = Path(__file__).resolve().parents[2]
    cases = [
        ('journal.py', 'ordinary startup blocks unfinished operations', 'if not maintenance:', 'if False:'),
        ('journal.py', 'migration cannot bypass unfinished operations', 'require_maintenance_finished(db)\n', 'pass\n'),
        ('unified_maintenance.py', 'in-flight events block adoption', 'journal.recovery_events() or ', ''),
        ('unified_maintenance.py', 'pending native inputs block adoption', 'if (pending or any(', 'if (False or any('),
        ('unified_maintenance.py', 'active original turn blocks adoption', "any(t['status'] == 'inProgress' for t in before['thread']['turns'])", 'False'),
        ('unified_maintenance.py', 'selected pane permissions', "or result.get('approvalPolicy') != config.approval_policy\n", ''),
        ('unified_maintenance.py', 'adopted history exact retention', ' or history_digest(after) != original', ''),
        ('unified_maintenance.py', 'adoption intent identity', "if planned and planned['state'] == 'planned' and tuple(planned) != record:", 'if False:'),
        ('unified_maintenance.py', 'adoption intent durable before resume', "journal.db.execute('INSERT OR REPLACE INTO unified_adoptions VALUES(?,?,?,?,?,?,?)', record)", 'pass'),
        ('unified_maintenance.py', 'confirmed adoption clears only its hold', 'journal.db.execute("UPDATE unified_adoptions SET state=\'confirmed\' WHERE singleton=1")', 'pass'),
        ('unified_maintenance.py', 'duplicate pending input', 'if len(queued) > 1:', 'if False:'),
        ('unified_maintenance.py', 'explicit cancellation', 'if not cancel_queued:', 'if False:'),
        ('unified_maintenance.py', 'pending attempt association', "or (intent['queue_id'] is not None and q['id'] != intent['queue_id'])", ''),
        ('unified_maintenance.py', 'delete intent durable before write', "journal.db.execute('INSERT INTO native_queue_deletions VALUES(?,?,?,?,?,?,?)', record)", 'pass'),
        ('unified_maintenance.py', 'positive native delete acknowledgement', "if result != {'deleted': True}:", 'if False:'),
        ('unified_maintenance.py', 'deleted input no longer pending', "any(item.get('id') == q['id'] or item.get('clientUserMessageId') == intent['client_id'] for item in pending)", 'False'),
        ('unified_maintenance.py', 'deletion preserves original history', 'or history_digest(history) != record[5]', ''),
        ('unified_maintenance.py', 'lost acknowledgement requires unchanged history', "or history_digest(history) != deletion['history_sha256']", ''),
        ('unified_maintenance.py', 'lost acknowledgement requires explicit cancellation', "if (not cancel_queued or deletion['thread_id']", "if (False or deletion['thread_id']"),
        ('unified_maintenance.py', 'operator hold remains sticky', "journal.db.execute('UPDATE native_queue_intents SET blocked=1 WHERE event_id=?', (event_id,))", 'pass'),
        ('unified_maintenance.py', 'protected policy explicitly refreshed', "await client.request('thread/inject_items', {'threadId': thread_id, 'items': [\n            {'type': 'message', 'role': 'developer', 'content': [\n                {'type': 'input_text', 'text': settings['developerInstructions']}]}]})", 'pass'),
        ('unified_maintenance.py', 'protected policy developer role', "'role': 'developer'", "'role': 'user'"),
    ]
    for filename, name, old, new in cases:
        path = Path('src/amap_codex') / filename
        source = (repo / path).read_text()
        assert source.count(old) == 1, (name, source.count(old))
        mutated = source.replace(old, new, 1)
        compile(mutated, str(path), 'exec')
        with tempfile.TemporaryDirectory(prefix='amap-maint-guard-') as directory:
            root = Path(directory)
            for tree in ('src', 'tests'):
                shutil.copytree(repo / tree, root / tree, ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copy(repo / 'pyproject.toml', root / 'pyproject.toml')
            (root / path).write_text(mutated)
            result = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests/test_unified_maintenance.py'],
                cwd=root, capture_output=True, text=True, timeout=30,
                env={**os.environ, 'PYTHONPATH': str(root / 'src'), 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'})
            assert result.returncode == 1 and 'failed' in result.stdout, (name, result.stdout[-4000:], result.stderr[-1000:])
            print('detected:', name, flush=True)
    print(f'{len(cases)} maintenance guards detected on scratch copies')


if __name__ == '__main__': main()
