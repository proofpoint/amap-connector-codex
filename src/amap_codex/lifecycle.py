"""Versioned execution control for any deployment; no isolation-specific code."""
import contextlib
import json
import os
import re
import selectors
import signal
import subprocess
import time

LIMIT = 65536


class ControlUnknown(RuntimeError):
    pass


class LauncherControl:
    def __init__(self, config):
        self.config = config

    def call(self, verb, execution_id):
        if verb not in {'inspect', 'stop'} or not isinstance(execution_id,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}',execution_id):
            raise ValueError('exact execution and fixed control verb required')
        request = {'version': 1, 'deployment_id': self.config.deployment_id,
                   'execution_id': execution_id}
        process = subprocess.Popen([*self.config.launcher_control_argv, verb],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True)
        chunks = {process.stdout: bytearray(), process.stderr: bytearray()}
        deadline = time.monotonic() + self.config.launcher_control_timeout_seconds
        try:
            process.stdin.write(json.dumps(request).encode() + b'\n')
            process.stdin.close()
            with selectors.DefaultSelector() as poll:
                for pipe in chunks:
                    poll.register(pipe, selectors.EVENT_READ)
                while poll.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ControlUnknown('execution control timed out')
                    for key, _ in poll.select(remaining):
                        part = os.read(key.fileobj.fileno(), 4096)
                        if not part:
                            poll.unregister(key.fileobj)
                        else:
                            chunks[key.fileobj].extend(part)
                            if len(chunks[key.fileobj]) > LIMIT:
                                raise ControlUnknown('execution control exceeded output bound')
            process.wait(timeout=max(.001, deadline-time.monotonic()))
            if process.returncode:
                raise ControlUnknown('execution control failed')
            result = json.loads(chunks[process.stdout])
            required = {'version', 'deployment_id', 'execution_id', 'state'}
            if (not isinstance(result, dict) or not required <= result.keys()
                or set(result) - (required | {'detail'}) or type(result['version']) is not int
                or result['version'] != 1 or result['deployment_id'] != request['deployment_id']
                or result['execution_id'] != execution_id
                or result['state'] not in {'running', 'stopped', 'unknown'}
                or not isinstance(result.get('detail', ''), str)
                or len(result.get('detail', '')) > 1000):
                raise ControlUnknown('execution control returned invalid binding')
            return result
        except (OSError, ValueError, TypeError, subprocess.TimeoutExpired) as exc:
            raise ControlUnknown('execution control unavailable or malformed') from exc
        finally:
            # A helper may have children still holding its output pipes.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            for pipe in (process.stdin, process.stdout, process.stderr):
                pipe.close()

    def require_stopped(self, execution_id, *, stop=False):
        result = self.call('stop' if stop else 'inspect', execution_id)
        if result['state'] != 'stopped':
            raise ControlUnknown('isolated execution is running or unknown; cleanup required')
        return result
