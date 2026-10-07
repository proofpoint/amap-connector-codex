"""Privileged host-only integration kickoff. Never accepts router artifacts."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat

PREFIX = 'AMAP operator kickoff: '


def queue_directory(config):
    if not config.operator_kickoff_enabled:
        raise ValueError('operator kickoff is disabled')
    path = config.state_dir / 'operator-queue'
    path.mkdir(mode=0o700, exist_ok=True)
    if path.is_symlink() or path.stat().st_uid != os.getuid() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError('operator queue must be private and supervisor-owned')
    return path


def read_request(path):
    fd = os.open(path, os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
            raise ValueError('invalid operator request file')
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError('operator request too large')
    doc = json.loads(raw)
    if (set(doc) != {'run_id', 'instructions'} or not isinstance(doc['run_id'], str)
        or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}', doc['run_id'])
        or not isinstance(doc['instructions'], str) or not doc['instructions'].strip()
        or path.name != doc['run_id'] + '.json'):
        raise ValueError('invalid operator request')
    return doc


def instruction_hash(request):
    return hashlib.sha256(request['instructions'].encode()).hexdigest()


def enqueue(config, run_id, instructions):
    from .supervisor import atomic_json
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}', run_id):
        raise ValueError('invalid run_id')
    if not isinstance(instructions, str) or not instructions.strip() or len(instructions.encode()) > 60000:
        raise ValueError('instructions must be nonempty and bounded')
    path = queue_directory(config)/(run_id+'.json')
    request = {'run_id': run_id, 'instructions': instructions}
    # A separate submit lock makes concurrent duplicate CLI calls deterministic.
    import fcntl
    fd = os.open(path.parent/'submit.lock', os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        if path.exists():
            if read_request(path) != request: raise ValueError('run ID already has different instructions')
        else: atomic_json(path, request)
    finally: os.close(fd)
    return {'run_id': run_id, 'state': 'queued', 'instruction_hash': instruction_hash(request)}


def find_operator(history, run_id):
    for turn in history.get('thread', {}).get('turns', []):
        for item in turn.get('items', []):
            content = item.get('content', [])
            if item.get('type') != 'userMessage' or len(content) != 1 or content[0].get('type') != 'text':
                continue
            text = content[0].get('text', '')
            if text.startswith(PREFIX):
                try: request = json.loads(text[len(PREFIX):])
                except ValueError: continue
                if request.get('run_id') == run_id:
                    return turn.get('id'), turn.get('status', 'inProgress'), instruction_hash(request)
