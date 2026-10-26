"""Critical runtime guards mutated only on private independent scratch copies."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    repo = Path(__file__).resolve().parents[2]
    cases = [
        ('local_socket.py', 'mask and extension flags', 'if first & 112 or bool(second & 128) == self.client:', 'if False:'),
        ('local_socket.py', 'message size', 'if size > self.limit or (opcode < 8 and accumulated + size > self.limit):', 'if False:'),
        ('local_socket.py', 'control frame validity', 'if opcode >= 8 and (not final or size > 125):', 'if False:'),
        ('local_socket.py', 'opcode/fragment sequence', 'if opcode not in {0, 1} or (opcode == 0) != fragmented:', 'if False:'),
        ('local_socket.py', 'minimal short length', "if size < 126:\n                raise ProtocolError('nonminimal WebSocket length')", "if False:\n                raise ProtocolError('nonminimal WebSocket length')"),
        ('local_socket.py', 'minimal long length', 'if size < 65536 or size >= 2**63:', 'if False:'),
        ('local_socket.py', 'object envelope', 'if not isinstance(value, dict):', 'if False:'),
        ('local_socket.py', 'duplicate JSON members', 'if key in result:', 'if False:'),
        ('local_socket.py', 'finite JSON floats', 'if not math.isfinite(result):', 'if False:'),
        ('local_socket.py', 'strict key encoding', 'base64.b64decode(key, validate=True)', 'base64.b64decode(key, validate=False)'),
        ('local_socket.py', 'key byte count', 'if len(raw) != 16:', 'if False:'),
        ('local_rpc.py', 'backend build', "if not version_matches(self.initialize_result.get('userAgent'), self.expected_version):", 'if False:'),
        ('local_rpc.py', 'response envelope', "and 'error' not in frame:", ':'),
        ('local_rpc.py', 'passive approval observer', 'self.blocked.append({', "await self.socket.send({'id': identifier, 'result': {'decision': 'cancel'}})\n                        self.blocked.append({"),
        ('managed_backend.py', 'private directory', 'if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:', 'if False:'),
        ('managed_backend.py', 'private socket', 'if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:', 'if False:'),
        ('managed_backend.py', 'explicit alias authority', 'and allow_owned_alias:', ':'),
        ('managed_backend.py', 'resolved native target path length', 'if len(os.fsencode(target)) > 100:', 'if False:'),
        ('native_queue.py', 'ack identity/content', "or submission.get('clientUserMessageId') != intent['client_id']\n                or submission.get('input') != json.loads(intent['input_json'])", ''),
        ('native_queue.py', 'queue ID nonempty', "or not 0 < len(submission['id']) <= 200", ''),
        ('native_queue.py', 'ack binding', "if current != intent or j.thread_id != intent['thread_id']:", 'if False:'),
        ('native_queue.py', 'ack stage', "if j.event(intent['event_id'])['state'] != 'dispatching':", 'if False:'),
        ('native_queue.py', 'history canonical', "if history.get('thread', {}).get('id') != intent['thread_id']:", 'if False:'),
        ('native_queue.py', 'original uniqueness', 'if len(queued) + len(original) > 1:', 'if False:'),
        ('native_queue.py', 'original role', "item.get('type') == 'userMessage' and item.get('clientId') == intent['client_id']", "item.get('clientId') == intent['client_id']"),
        ('native_queue.py', 'original client', "and item.get('clientId') == intent['client_id']", ''),
        ('native_queue.py', 'original full input', "item.get('content') != expected or ", ''),
        ('native_queue.py', 'original status', "or turn.get('status') not in TERMINAL | {'inProgress'}):\n            raise IntegrityConflict('original native input or execution status differs from durable intent')", "):\n            raise IntegrityConflict('original native input or execution status differs from durable intent')"),
        ('native_queue.py', 'pending full input', "if item.get('input') != expected:", 'if False:'),
        ('native_queue.py', 'pending association', "or (known and item.get('id') not in known)", ''),
        ('native_queue.py', 'cursor shape/cycle', 'if not isinstance(cursor, str) or not cursor or cursor in seen:', 'if False:'),
        ('native_queue.py', 'resume queue duplicates', 'or identifier in seen or identifier in originals', ''),
        ('native_queue.py', 'retained queued artifacts', 'or not retained(event)', ''),
        ('attach_gate.py', 'native method vocabulary', 'if method not in READ_ONLY | THREAD_METHODS:', 'if False:'),
        ('attach_gate.py', 'canonical request', "if method in THREAD_METHODS and params.get('threadId') != self.thread_id:", 'if False:'),
        ('attach_gate.py', 'all explicit thread IDs', "if 'threadId' in params and params['threadId'] != self.thread_id:", 'if False:'),
        ('attach_gate.py', 'workspace', "if params.get('cwd') is not None and str(Path(params['cwd']).resolve()) != self.cwd:", 'if False:'),
        ('attach_gate.py', 'model', "if params.get('model') is not None and params['model'] != self.model:", 'if False:'),
        ('attach_gate.py', 'approval policy', "if params.get('approvalPolicy') is not None and params['approvalPolicy'] != self.approval_policy:", 'if False:'),
        ('attach_gate.py', 'approval reviewer', "if params.get('approvalsReviewer') is not None and params['approvalsReviewer'] != self.approvals_reviewer:", 'if False:'),
        ('attach_gate.py', 'sandbox selection', "if params.get('sandbox') is not None and params['sandbox'] != self.sandbox_name:", 'if False:'),
        ('attach_gate.py', 'sandbox policy', "if params.get('sandboxPolicy') is not None and params['sandboxPolicy'] != self.sandbox:", 'if False:'),
        ('attach_gate.py', 'workspace roots', "if params.get('runtimeWorkspaceRoots') is not None and params['runtimeWorkspaceRoots'] != self.roots:", 'if False:'),
        ('attach_gate.py', 'protected configuration', 'if any(params.get(key) is not None for key in forbidden):', 'if False:'),
        ('attach_gate.py', 'tool output provenance', "if params.get('toolOutput') is not None:", 'if False:'),
        ('attach_gate.py', 'trusted native mode instructions', "'developer_instructions': None}}}", "'developer_instructions': collaboration['settings'].get('developer_instructions')}}}"),
        ('unified_readiness.py', 'readiness evidence', "if not isinstance(checks, dict) or not required <= checks.keys() or any(v != 'pass' for v in checks.values()):", 'if False:'),
        ('unified_readiness.py', 'fresh heartbeat', "if type(stamp) not in {int, float} or not 0 <= time.time() - stamp <= max(10, config.poll_interval_ms / 1000 * 3):", 'if False:'),
        ('unified_readiness.py', 'durable journal binding', "if binding.get('thread_id') != thread or binding.get('fingerprint') != value['fingerprint']:", 'if False:'),
        ('unified_readiness.py', 'endpoint generation', "if value.get('endpoint') != 'unix://' + str(endpoint):", 'if False:'),
        ('unified_attach.py', 'one writer precheck', "if type(value.get('attached_client_count')) is not int or value['attached_client_count'] != 0:", 'if False:'),
        ('unified_attach.py', 'foreign resume', "if resume and resume[0] != '--last' and resume[0].lower() != value['canonical_thread_id'].lower():", 'if False:'),
        ('unified_attach.py', 'native flags match pane', 'if expected is not None and option_value != expected:', 'if False:'),
        ('unified.py', 'startup tool validation bound to generation', "bool(self.tools_validation and\n                      self.tools_validation['backend_generation'] == self.backend.generation)", 'True'),
        ('unified.py', 'unknown native state cannot authorize dispatch', "clean and isinstance(state, dict) and state.get('type') in {'idle', 'active'}", 'clean'),
        ('unified.py', 'unknown native state cannot authorize readiness', 'clean and known_state if history else None', 'clean if history else None'),
        ('unified_readiness.py', 'validation generation', "or validation.get('backend_generation') != generation", ''),
        ('unified_readiness.py', 'validation timestamp', "or not 0 < validation['validated_at'] <= stamp", ''),
        ('unified_readiness.py', 'published heartbeat bound', "or value.get('heartbeat_max_age_seconds') != max(10, config.poll_interval_ms / 1000 * 3)", ''),
        ('unified_readiness.py', 'closed scheduler vocabulary', "or value.get('scheduler_state') not in ('idle', 'active')", ''),
        ('cli.py', 'public observations validate ready evidence', 'validate_ready(config, recorded, status)', 'None'),
        ('native_queue.py', 'pre-resume known queue custody', 'WHERE client_id=? OR queue_id=?', 'WHERE client_id=? AND ? IS NOT NULL'),
        ('native_queue.py', 'queue client association', "item.get('clientUserMessageId') != intent['client_id']", 'False'),
        ('native_queue.py', 'native queue ID uniqueness', 'if not isinstance(queue_id, str) or not queue_id or queue_id in queue_ids:', 'if False:'),
        ('native_queue.py', 'one native queue cannot bind multiple attempts', 'if len(matches) != 1:', 'if False:'),
        ('unified_policy.py', 'explicit absolute native workspace', "if not isinstance(native_cwd, str) or not native_cwd or not Path(native_cwd).is_absolute():", 'if False:'),
        ('unified.py', 'startup workspace evidence required', "or not workspace_matches(result.get('cwd'), c.cwd)", ''),
        ('interactive.py', 'activity revalidates at publication', 'validate_ready(config, status, status)', 'None'),
        ('unified_readiness.py', 'queue counts agree', ' or sum(sources.values()) != depth', ''),
    ]
    tests = ['tests/test_local_socket.py', 'tests/test_local_rpc.py', 'tests/test_native_queue.py',
             'tests/test_attach_gate.py', 'tests/test_managed_backend.py', 'tests/test_unified_attach.py', 'tests/test_unified_status.py', 'tests/test_unified_workspace.py']
    for filename, name, old, new in cases:
        path = Path('src/amap_codex') / filename
        source = (repo / path).read_text()
        assert source.count(old) == 1, (name, source.count(old))
        mutated = source.replace(old, new, 1)
        compile(mutated, str(path), 'exec')
        with tempfile.TemporaryDirectory(prefix='amap-runtime-guard-') as directory:
            root = Path(directory)
            for tree in ('src', 'tests', 'docs'):
                shutil.copytree(repo / tree, root / tree, ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copy(repo / 'pyproject.toml', root / 'pyproject.toml')
            (root / path).write_text(mutated)
            result = subprocess.run([sys.executable, '-m', 'pytest', '-q', *tests], cwd=root,
                capture_output=True, text=True, timeout=30,
                env={**os.environ, 'PYTHONPATH': str(root / 'src'), 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'})
            assert result.returncode == 1 and 'failed' in result.stdout, (name, result.stdout[-4000:], result.stderr[-1000:])
            print('detected:', name, flush=True)
    print(f'{len(cases)} runtime guard mutations detected on independent scratch copies')


if __name__ == '__main__':
    main()
