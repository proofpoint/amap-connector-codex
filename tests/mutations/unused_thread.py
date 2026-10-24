"""Reproduce unused-thread recovery guards on scratch copies only.

Run with the test interpreter: python tests/mutations/unused_thread.py
No native Codex, inference, network or fleet state is used.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    repo = Path(__file__).resolve().parents[2]
    cases = [
        ('existing binding', 'app_server.py', 'thread_id is None', 'False',
         'test_app_server.py::test_missing_rollout_release_requires_existing_binding_and_explicit_callback[None]'),
        ('explicit callback', 'app_server.py', 'release_unused is None', 'False',
         'test_app_server.py::test_missing_rollout_release_requires_existing_binding_and_explicit_callback[00000000-0000-0000-0000-000000000001]'),
        ('matching bound rollout', 'app_server.py', 'error.missing_thread_id != thread_id', 'False',
         'test_supervisor.py::test_missing_resume_rollout_keeps_binding_and_never_dispatches_or_replaces_thread'),
        ('no prior turn request', 'app_server.py', 'self.client.turn_starts != 0', 'False',
         'test_app_server.py::test_missing_rollout_never_releases_after_a_turn_request'),
        ('binding compare', 'journal.py', 'not expected_thread_id or self.thread_id != expected_thread_id', 'False',
         'test_journal.py::test_unused_thread_release_checks_binding_and_audits_atomically'),
        ('event ledger', 'journal.py', "('events', 'attempts', 'operator_runs')", "('attempts', 'operator_runs')",
         'test_journal.py::test_unused_thread_release_refuses_every_ledger_even_orphan_attempt[events]'),
        ('attempt ledger', 'journal.py', "('events', 'attempts', 'operator_runs')", "('events', 'operator_runs')",
         'test_journal.py::test_unused_thread_release_refuses_every_ledger_even_orphan_attempt[attempts]'),
        ('operator ledger', 'journal.py', "('events', 'attempts', 'operator_runs')", "('events', 'attempts')",
         'test_journal.py::test_unused_thread_release_refuses_every_ledger_even_orphan_attempt[operator_runs]'),
        ('release audit', 'journal.py', "self._audit(None, 'unused_thread_released',", "self._audit(None, 'ignored',",
         'test_journal.py::test_unused_thread_release_checks_binding_and_audits_atomically'),
        ('atomic release', 'journal.py', 'with self._transaction():\n            if not expected_thread_id',
         "with __import__('contextlib').nullcontext():\n            if not expected_thread_id",
         'test_journal.py::test_unused_thread_release_rolls_back_binding_when_audit_fails'),
    ]
    for name, filename, old, new, test in cases:
        path = Path('src/amap_codex') / filename
        original = (repo / path).read_text()
        assert original.count(old) == 1, name
        mutated = original.replace(old, new, 1)
        compile(mutated, str(path), 'exec')
        with tempfile.TemporaryDirectory(prefix='amap-unused-guard-') as directory:
            root = Path(directory)
            for tree in ('src', 'tests'):
                shutil.copytree(repo / tree, root / tree, ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copy(repo / 'pyproject.toml', root / 'pyproject.toml')
            (root / path).write_text(mutated)
            result = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests/' + test],
                                    cwd=root, text=True, capture_output=True,
                                    env={**os.environ, 'PYTHONPATH': os.pathsep.join([
                                        str(root / 'src'), os.environ.get('PYTHONPATH', '')]),
                                         'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'})
            assert result.returncode == 1 and any(marker in result.stdout for marker in
                ('AssertionError', 'DID NOT RAISE')), (name, result.stdout, result.stderr)
            print('detected:', name)
    print(f'{len(cases)} recovery guards detected on scratch copies')


if __name__ == '__main__':
    main()
