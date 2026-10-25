"""One explicitly owned Codex backend per private runtime generation."""
import asyncio
import contextlib
import os
from pathlib import Path
import signal
import stat
import uuid

from .local_rpc import LocalRPC


def private_directory(path):
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError('runtime state must be an explicit absolute directory')
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError('runtime directory must be private and owned by the connector')
    if path.resolve() != path:
        raise ValueError('runtime directory must not traverse symlinks')


def private_socket(path, *, allow_owned_alias=False):
    private_directory(path.parent)
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) and allow_owned_alias:
        if info.st_uid != os.getuid():
            raise ValueError('runtime endpoint alias has another owner')
        target = path.resolve(strict=True)
        private_directory(target.parent)
        info = target.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError('runtime socket must be private and owned by the connector')


class ManagedBackend:
    def __init__(self, state_dir, native_argv, *, expected_version, timeout=30,
                 frame_limit=32 * 1024 * 1024, on_spawn=None, env=None):
        private_directory(state_dir)
        if not isinstance(native_argv, list) or not native_argv or any(
                not isinstance(v, str) or not v or '\x00' in v for v in native_argv):
            raise ValueError('managed backend requires an explicit native argument vector')
        self.generation = uuid.uuid4().hex
        self.directory = state_dir / ('u-' + self.generation)
        self.backend_path = self.directory / 'b'
        self.front_path = self.directory / 't'
        if len(os.fsencode(self.backend_path)) > 100:
            raise ValueError('private runtime path exceeds portable Unix socket length; use a shorter state directory')
        self.native_argv = list(native_argv)
        self.expected_version, self.timeout, self.frame_limit = expected_version, timeout, frame_limit
        self.on_spawn, self.env = on_spawn, env
        self.process = self.client = None

    async def start(self):
        if self.process is not None:
            raise RuntimeError('backend generation is already started')
        self.directory.mkdir(mode=0o700)
        private_directory(self.directory)
        try:
            self.process = await asyncio.create_subprocess_exec(*self.native_argv,
                'app-server', '--listen', 'unix://' + str(self.backend_path),
                stdout=asyncio.subprocess.DEVNULL, stderr=None,
                start_new_session=True, env=self.env)
            if self.on_spawn:
                from .claims import _proc_start
                self.on_spawn(self.process.pid, _proc_start(self.process.pid))
            async with asyncio.timeout(self.timeout):
                while not self.backend_path.exists():
                    if self.process.returncode is not None:
                        raise RuntimeError('owned Codex backend exited before listening')
                    await asyncio.sleep(.02)
            # Codex 0.164 publishes the requested endpoint as an owned alias
            # into its private native socket directory. Follow only this newly
            # spawned endpoint, never discover a global daemon by that directory.
            private_socket(self.backend_path, allow_owned_alias=True)
            self.client = LocalRPC(self.backend_path, expected_version=self.expected_version,
                                   timeout=self.timeout, frame_limit=self.frame_limit)
            await self.client.start()
            return self.client
        except BaseException:
            await self.stop()
            raise

    async def stop(self):
        if self.client:
            await self.client.stop()
        if self.process:
            if self.process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self.process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self.process.pid, signal.SIGKILL)
                await asyncio.wait_for(self.process.wait(), 5)
        # Only unlink known socket paths after this owned process has exited.
        if self.process is None or self.process.returncode is not None:
            for path in (self.backend_path, self.front_path):
                with contextlib.suppress(FileNotFoundError):
                    path.unlink()
            with contextlib.suppress(FileNotFoundError, OSError):
                self.directory.rmdir()
