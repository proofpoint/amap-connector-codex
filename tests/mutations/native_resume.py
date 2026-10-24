"""Prove native-resume guards on scratch copies, without native CLI or network.

Run: python tests/mutations/native_resume.py
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    repo = Path(__file__).resolve().parents[2]
    path = Path('src/amap_codex/interactive.py')
    source = (repo / path).read_text()
    cases = [
        ('single selector', 'if selector is not None:', 'if False:',
         'test_native_resume_parser_refuses_multiple_selectors'),
        ('one CLI result', 'not isinstance(data, list) or len(data) != 1', 'not isinstance(data, list) or len(data) == 0',
         'test_native_last_resolution_refuses_multiple_results_before_loading'),
        ('UUID metadata', 'not isinstance(target, str) or SESSION_ID.fullmatch(target) is None', 'not isinstance(target, str)',
         'test_cli_resume_metadata_restrictions'),
        ('explicit ID matches', "selector != '--last' and target.lower() != selector.lower()", 'False',
         'test_cli_resume_metadata_restrictions'),
        ('supervisor excluded', 'isinstance(bound, str) and target.lower() == bound.lower()', 'False',
         'test_cli_resume_metadata_restrictions'),
        ('CLI source only', "thread.get('source') != 'cli'", 'False',
         'test_cli_resume_metadata_restrictions'),
        ('same workspace', "not isinstance(thread.get('cwd'), str) or Path(thread['cwd']).resolve() != Path(cwd).resolve()", 'False',
         'test_cli_resume_metadata_restrictions'),
        ('read-only preview', 'if resume is not None and not prepare_resume:', 'if False:',
         'test_native_resume_preview_is_refused_without_loading_or_changing_history'),
        ('loaded target revalidated', "validate_native_session(resumed, cwd, target, status.get('thread_id'))", 'pass',
         'test_native_resume_revalidates_loaded_target_before_policy_update'),
        ('idle before policy', "resumed.get('status') != {'type': 'idle'}", 'False',
         'test_native_resume_revalidates_loaded_target_before_policy_update[not_idle]'),
        ('one protected registration', 'len(matches) != 1', 'not matches',
         'test_native_resume_requires_protected_tools_before_policy_update[duplicate]'),
        ('no tool startup error', "matches[0].get('toolsError')", 'False',
         'test_native_resume_requires_protected_tools_before_policy_update[error]'),
        ('tools shape', "not isinstance(matches[0].get('tools'), dict)", 'False',
         'test_native_resume_requires_protected_tools_before_policy_update[wrong_shape]'),
        ('exact protected tools', "set(matches[0].get('tools', {})) != set(definition['enabled_tools'])", 'False',
         'test_native_resume_requires_protected_tools_before_policy_update[extra_tool]'),
        ('policy role', "'type': 'message', 'role': 'developer'", "'type': 'message', 'role': 'user'",
         'test_native_resume_resolves_cli_target_preserves_policy_and_never_dispatches_turn'),
        ('journal ownership at both boundaries', "status.get('instance_id') != config.instance_id", 'False',
         'test_native_resume_refuses_foreign_journal_before_loading_or_updating_policy'),
        ('bounded registry', "raise ValueError('native resume MCP registry exceeded expected bound')", 'pass',
         'test_native_resume_requires_protected_tools_before_policy_update[cursor_loop]'),
    ]
    for name, old, new, test in cases:
        if name == 'journal ownership at both boundaries':
            split = source.index('async def native_resume_target(')
            assert source[split:].count(old) == 2, name
            mutated = source[:split] + source[split:].replace(old, new)
        else:
            assert source.count(old) == 1, name
            mutated = source.replace(old, new, 1)
        compile(mutated, str(path), 'exec')
        with tempfile.TemporaryDirectory(prefix='amap-resume-guard-') as directory:
            root = Path(directory)
            for tree in ('src', 'tests'):
                shutil.copytree(repo / tree, root / tree, ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copy(repo / 'pyproject.toml', root / 'pyproject.toml')
            (root / path).write_text(mutated)
            result = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests/test_interactive.py::' + test],
                cwd=root, text=True, capture_output=True,
                env={**os.environ, 'PYTHONPATH': os.pathsep.join([str(root / 'src'), os.environ.get('PYTHONPATH', '')]),
                     'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'})
            assert result.returncode == 1 and any(marker in result.stdout for marker in (
                'AssertionError', 'DID NOT RAISE')), (name, result.stdout, result.stderr)
            print('detected:', name)
    print(f'{len(cases)} native resume guards detected on scratch copies')


if __name__ == '__main__':
    main()
