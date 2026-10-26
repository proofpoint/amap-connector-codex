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
    pre = 'tests/test_native_queue_probe.py::test_recovery_precrash_requires_exact_intended_membership'
    post = 'tests/test_native_queue_probe.py::test_recovery_rejects_substitution_missing_inputs_and_human_replay'
    cases.extend([
        (evidence, 'exact two intended add receipts', 'assert len(retained) == 2,', 'assert True,', pre+'[extra_add_receipt]'),
        (evidence, 'distinct intended clients', "assert len({s['clientUserMessageId'] for s in retained}) == 2,", 'assert True,', pre+'[duplicate_add_client]'),
        (evidence, 'distinct intended complete inputs', "assert len({json.dumps(s['input'], sort_keys=True) for s in retained}) == 2,", 'assert True,', pre+'[duplicate_add_input]'),
        (evidence, 'exact pre-crash membership count', 'assert len(pending) == 2,', 'assert True,', pre+'[extra_pending]'),
        (evidence, 'pre-crash client association', "assert found[0]['clientUserMessageId'] == receipt['clientUserMessageId'],", 'assert True,', pre+'[substitute_client]'),
        (evidence, 'pre-crash full input association', "assert found[0]['input'] == receipt['input'],", 'assert True,', pre+'[substitute_input]'),
        (evidence, 'bind original human text', "assert item['content'][0]['text'] == human_text,", 'assert True,', pre+'[wrong_human]'),
        (evidence, 'no extra original human replay', 'assert len(originals) == 3,', 'assert True,', post+'[human_replay]'),
        (evidence, 'preserve exact original human identity', 'assert original_human == [human],', 'assert True,', post+'[human_replaced]'),
        (evidence, 'distinct observed execution turns', 'assert len(turns) == 2 and len(set(turns)) == 2,', 'assert True,', post+'[same_execution_and_observer]'),
        (evidence, 'bind observed turns to intended inputs', 'assert set(matched_turns) == set(turns),', 'assert True,', post+'[observed_turn_substitution]'),
        (evidence, 'no extra provider dispatch', 'assert len(requests) == 3,', 'assert True,', post+'[extra_provider_human]'),
        (evidence, 'provider active input is intended dispatch', "assert content[0]['text'] == text,", 'assert True,', post+'[provider_human_replay]'),
    ])
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
