"""Check probe-transport guards on scratch copies; no native CLI or sockets."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    repo = Path(__file__).resolve().parents[2]
    path = Path('tests/integration/_unified_ws.py')
    source = (repo / path).read_text()
    cases = [
        ('fragmented frames', 'not first & 0x80 or ', '', 'fragmented'),
        ('masked server frames', 'second & 0x80 or ', '', 'masked'),
        ('unnegotiated extensions', ' or first & 0x70:', ':', 'reserved'),
        ('binary data', 'if opcode != 1:', 'if False:', 'binary'),
        ('frame bound', 'if length > 8 * 1024 * 1024:', 'if False:', 'oversized'),
    ]
    controls = [(name, old, new,
        'test_probe_transport_refuses_unsupported_frames_before_delivering_rpc_result[' + test + ']')
        for name, old, new, test in cases]
    controls += [
        ('upgrade status', "not lines[0].startswith('HTTP/1.1 101 ') or ", '',
         'test_probe_upgrade_and_build_must_match_before_use[status]'),
        ('upgrade accept', " or headers.get('sec-websocket-accept') != accept", '',
         'test_probe_upgrade_and_build_must_match_before_use[accept]'),
        ('build', "assert version_matches(self.initialize_result.get('userAgent'), self.expected_version)", 'pass',
         'test_probe_upgrade_and_build_must_match_before_use[version]'),
    ]
    for name, old, new, test in controls:
        assert source.count(old) == 1, name
        mutated = source.replace(old, new, 1)
        compile(mutated, str(path), 'exec')
        with tempfile.TemporaryDirectory(prefix='amap-unified-guard-') as directory:
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
    print(f'{len(controls)} probe transport guard mutations detected on scratch copies')


if __name__ == '__main__':
    main()
