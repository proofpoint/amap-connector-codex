"""Mutate native queue evidence guards only on independent scratch copies."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    repo = Path(__file__).resolve().parents[2]
    evidence = Path('tests/integration/_native_queue_evidence.py')
    prefix = 'tests/test_native_queue_probe.py::test_queued_original_refuses_inexact_or_ambiguous_evidence'
    cases = [
        (evidence, 'canonical thread', "assert history['thread']['id'] == thread_id,", 'assert True,', prefix + '[thread]'),
        (evidence, 'original input role', "item.get('type') == 'userMessage'", 'True', prefix + '[role]'),
        (evidence, 'client identity', "item.get('clientId') == submission['clientUserMessageId']", 'True', prefix + '[client]'),
        (evidence, 'unique original', 'assert len(matches) == 1,', 'assert True,', prefix + '[duplicate_item]'),
        (evidence, 'turn identity', "assert turn['id'] == turn_id,", 'assert True,', prefix + '[turn]'),
        (evidence, 'complete input', "assert item['content'] == submission['input'],", 'assert True,', prefix + '[content]'),
        (evidence, 'execution status', "assert turn['status'] == status,", 'assert True,', prefix + '[status]'),
        (Path('tests/integration/_unified_ws.py'), 'passive approval observer',
         'if self.request_handler is not None:', 'if False:',
         'tests/test_unified_probe.py::test_probe_passive_observer_does_not_answer_server_approval'),
    ]
    for path, name, old, new, test in cases:
        source = (repo / path).read_text()
        assert source.count(old) == 1, name
        mutated = source.replace(old, new, 1)
        compile(mutated, str(path), 'exec')
        with tempfile.TemporaryDirectory(prefix='amap-native-queue-guard-') as directory:
            root = Path(directory)
            for tree in ('src', 'tests'):
                shutil.copytree(repo / tree, root / tree, ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copy(repo / 'pyproject.toml', root / 'pyproject.toml')
            (root / path).write_text(mutated)
            result = subprocess.run([sys.executable, '-m', 'pytest', '-q', test], cwd=root,
                capture_output=True, text=True, timeout=30,
                env={**os.environ, 'PYTHONPATH': str(root / 'src'), 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'})
            assert result.returncode == 1 and '1 failed' in result.stdout, (name, result.stdout, result.stderr)
            print('detected:', name)
    print(f'{len(cases)} native queue evidence/observer mutations detected on scratch copies')


if __name__ == '__main__':
    main()
