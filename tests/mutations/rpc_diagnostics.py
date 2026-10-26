"""Prove RPC diagnostic privacy/context checks using scratch copies only.

Run with the existing test interpreter: python tests/mutations/rpc_diagnostics.py
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
    path = Path('src/amap_codex/app_server.py')
    original = (repo / path).read_text()
    cases = [
        ('request context', 'raise exc.at_method(method)', 'raise exc',
         'test_resume_failure_identifies_local_method_without_server_content'),
        ('numeric code only', 'code if type(code) is int else None', 'code',
         'test_rpc_diagnostics_never_copy_arbitrary_server_text'),
        ('category code guard', 'self.code == -32600 and isinstance(message, str)',
         'isinstance(message, str)', 'test_rpc_diagnostics_never_copy_arbitrary_server_text'),
        ('exact category shape', 'and re.fullmatch(', 'and (True or re.fullmatch(',
         'test_rpc_diagnostics_never_copy_arbitrary_server_text'),
        ('local method vocabulary', 'method if method in known else "unrecognized_method"',
         'method', 'test_unknown_local_method_is_not_written_to_diagnostics'),
        ('resume-only hint', 'and name == "thread/resume"', '',
         'test_missing_rollout_hint_is_only_for_resume'),
    ]
    for name, old, new, test in cases:
        assert original.count(old) == 1, name
        mutated = original.replace(old, new, 1)
        if name == 'exact category shape':
            mutated = mutated.replace('message) else None', 'message)) else None', 1)
        with tempfile.TemporaryDirectory(prefix='amap-rpc-guard-') as directory:
            root = Path(directory)
            for tree in ('src', 'tests'):
                shutil.copytree(repo / tree, root / tree, ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copy(repo / 'pyproject.toml', root / 'pyproject.toml')
            (root / path).write_text(mutated)
            # A syntax/import error is not evidence that the named guard fires.
            compile(mutated, str(path), 'exec')
            result = subprocess.run([sys.executable, '-m', 'pytest', '-q',
                                     'tests/test_app_server.py::' + test], cwd=root,
                                    env={**os.environ, 'PYTHONPATH': os.pathsep.join([
                                        str(root / 'src'), os.environ.get('PYTHONPATH', '')]),
                                         'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'},
                                    text=True, capture_output=True)
            assert result.returncode == 1 and 'AssertionError' in result.stdout, (name, result.stdout, result.stderr)
            print('detected:', name)
    print(f'{len(cases)} diagnostic guards detected on scratch copies')


if __name__ == '__main__':
    main()
