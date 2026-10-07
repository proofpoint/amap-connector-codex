"""Canonical host guard, including durable binding after a guard process dies."""
import contextlib
import fcntl
import json
import os
from pathlib import Path

from .claims import ClaimHeld, _holder_is_live, _proc_start


class HostGuard:
    def __init__(self, path, notice_dir, owner_domain, deployment_id, execution_id, control):
        self.path = Path(path)
        self.notice_dir = str(Path(notice_dir).resolve())
        self.owner_domain, self.deployment_id = owner_domain, deployment_id
        self.execution_id, self.control = execution_id, control
        self.fd = None
        self.owned = False

    def acquire(self):
        from .supervisor import atomic_json
        self.fd = os.open(str(self.path)+'.lock', os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ClaimHeld('canonical host guard is held', path=str(self.path)) from exc
            if self.path.exists() or self.path.is_symlink():
                if self.path.is_symlink() or self.path.stat().st_size > 65536:
                    raise ClaimHeld('invalid host guard; migration inspection required', path=str(self.path))
                try:
                    prior = json.loads(self.path.read_text())
                    if (prior.get('guard_version') != 1 or prior.get('owner_domain') != self.owner_domain
                        or prior.get('deployment_id') != self.deployment_id
                        or prior.get('notice_dir') != self.notice_dir or _holder_is_live(prior)):
                        raise ClaimHeld('live, foreign or legacy host guard', path=str(self.path))
                    self.control.require_stopped(prior['execution_id'])
                except (ValueError, KeyError, AttributeError) as exc:
                    raise ClaimHeld('unreadable host guard', path=str(self.path)) from exc
            atomic_json(self.path, {'guard_version': 1, 'pid': os.getpid(),
                'proc_start': _proc_start(os.getpid()), 'owner_domain': self.owner_domain,
                'deployment_id': self.deployment_id, 'execution_id': self.execution_id,
                'notice_dir': self.notice_dir})
            self.owned = True
            return self
        except BaseException:
            self.release(clear=False)
            raise

    def release(self, *, clear=True):
        if self.owned and clear:
            with contextlib.suppress(FileNotFoundError):
                self.path.unlink()
        self.owned = False
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
