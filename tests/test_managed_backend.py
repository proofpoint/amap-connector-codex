import asyncio
import os
from pathlib import Path
import socket
import sys
import tempfile

import pytest

from amap_codex.managed_backend import ManagedBackend, private_directory, private_socket


@pytest.mark.parametrize('bad', ['directory', 'socket', 'public_alias_target', 'regular_alias_target', 'alias_not_allowed'])
def test_private_runtime_and_native_alias_guards(tmp_path, bad):
    parent = tmp_path.resolve()
    parent.chmod(0o700)
    target = parent / 'target'
    target.mkdir(mode=0o700)
    endpoint = target / 'socket'
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(endpoint))
    endpoint.chmod(0o600)
    alias = parent / 'alias'
    alias.symlink_to(endpoint)
    try:
        private_directory(parent)
        private_socket(alias, allow_owned_alias=True)
        if bad == 'directory':
            parent.chmod(0o755)
            with pytest.raises(ValueError): private_socket(alias, allow_owned_alias=True)
        elif bad == 'socket':
            endpoint.chmod(0o666)
            with pytest.raises(ValueError): private_socket(alias, allow_owned_alias=True)
        elif bad == 'public_alias_target':
            target.chmod(0o755)
            with pytest.raises(ValueError): private_socket(alias, allow_owned_alias=True)
        elif bad == 'regular_alias_target':
            regular = target / 'regular'
            regular.write_text('not a socket')
            regular.chmod(0o600)
            alias.unlink()
            alias.symlink_to(regular)
            with pytest.raises(ValueError): private_socket(alias, allow_owned_alias=True)
        else:
            with pytest.raises(ValueError): private_socket(alias)
    finally:
        listener.close()


@pytest.mark.parametrize('failure', [None, 'build', 'callback'])
def test_owned_child_start_stop_and_failed_initialization_cleanup(failure):
    async def check():
        with tempfile.TemporaryDirectory(prefix='amap-backend-') as directory:
            state = Path(directory).resolve()
            def record(pid, start):
                assert pid > 0
                if failure == 'callback': raise RuntimeError('record failed')
            backend = ManagedBackend(state, [sys.executable, str(Path(__file__).with_name('fake_native_backend.py').resolve())],
                expected_version='codex-cli ' + ('9.0.0' if failure == 'build' else '0.164.0'),
                timeout=1, on_spawn=record,
                env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')})
            if failure:
                with pytest.raises(RuntimeError): await backend.start()
                assert backend.process.returncode is not None and not backend.directory.exists()
            else:
                await backend.start()
                assert backend.process.returncode is None
                assert backend.client.initialize_result['userAgent'] == 'codex-fixture/0.164.0'
                await backend.stop()
                assert backend.process.returncode is not None and not backend.directory.exists()
            await backend.stop()
    asyncio.run(check())


def test_native_alias_target_respects_portable_socket_path_bound():
    with tempfile.TemporaryDirectory(prefix='aq-') as directory:
        root = Path(directory).resolve()
        padding = 98 - len(os.fsencode(root))
        if padding < 1: pytest.skip('system temporary directory already exceeds test socket bound')
        target = root / ('x' * padding)
        target.mkdir(mode=0o700)
        endpoint = target / 's'
        assert len(os.fsencode(endpoint)) == 101
        listener = socket.socket(socket.AF_UNIX)
        try:
            listener.bind(str(endpoint))
            endpoint.chmod(0o600)
            alias = root / 'b'
            alias.symlink_to(endpoint)
            with pytest.raises(ValueError, match='portable Unix socket length'):
                private_socket(alias, allow_owned_alias=True)
        finally:
            listener.close()
