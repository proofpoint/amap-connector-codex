"""Prove exact pointer/history checks on scratch copies, no CLI or network."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    repo = Path(__file__).resolve().parents[2]
    path = Path('tests/integration/_unified_evidence.py')
    source = (repo / path).read_text()
    controls = [
        ('one original input', 'assert len(matches) == 1,', 'assert True,',
         'test_probe_evidence_rejects_partial_wrong_or_ambiguous_pointers[duplicate]'),
        ('accepted turn identity', 'assert matches[0][0] == accepted_turn,', 'assert True,',
         'test_probe_evidence_rejects_partial_wrong_or_ambiguous_pointers[wrong_turn]'),
        ('complete pointer fields', 'assert matches[0][1] == expected,',
         "assert matches[0][1]['event_id'] == expected['event_id'],",
         'test_probe_evidence_rejects_partial_wrong_or_ambiguous_pointers[missing_lane]'),
        ('original input role', "item.get('type') != 'functionCallOutput' or ", '',
         'test_probe_evidence_rejects_partial_wrong_or_ambiguous_pointers[assistant_echo]'),
        ('original input name', "item.get('name') != 'amap_notice'\n                    or ", '',
         'test_probe_evidence_rejects_partial_wrong_or_ambiguous_pointers[wrong_name]'),
        ('original namespace', "\n                    or item.get('namespace') != 'amap_connector'", '',
         'test_probe_evidence_rejects_partial_wrong_or_ambiguous_pointers[wrong_namespace]'),
        ('exact history, not count', "'turns': thread['turns']", "'turns': len(thread['turns'])",
         'test_probe_evidence_rejects_changed_history_even_with_same_count_and_final_marker[human_content]'),
        ('canonical thread identity', "'thread_id': thread['id'], ", '',
         'test_probe_evidence_rejects_changed_history_even_with_same_count_and_final_marker[thread_id]'),
    ]
    for name, old, new, test in controls:
        assert source.count(old) == 1, name
        mutated = source.replace(old, new, 1)
        compile(mutated, str(path), 'exec')
        with tempfile.TemporaryDirectory(prefix='amap-evidence-guard-') as directory:
            root = Path(directory)
            for tree in ('src', 'tests'):
                shutil.copytree(repo / tree, root / tree, ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copy(repo / 'pyproject.toml', root / 'pyproject.toml')
            (root / path).write_text(mutated)
            result = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests/test_unified_probe.py::' + test],
                cwd=root, text=True, capture_output=True,
                env={**os.environ, 'PYTHONPATH': os.pathsep.join([str(root / 'src'), os.environ.get('PYTHONPATH', '')]),
                     'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'})
            assert result.returncode == 1 and 'DID NOT RAISE' in result.stdout, (name, result.stdout, result.stderr)
            print('detected:', name)
    print(f'{len(controls)} exact-evidence guard mutations detected on scratch copies')


if __name__ == '__main__':
    main()
