"""Shared ready-evidence validation for attachment and public observations."""
from pathlib import Path
import re
import time

from .interactive import SESSION_ID


def validate_ready(config, value, binding):
    """Refuse incomplete, stale or foreign ready claims; never infer defaults."""
    if (not isinstance(value, dict) or type(value.get('schema_version')) is not int
            or value['schema_version'] != 1 or value.get('mode') != 'unified'
            or value.get('instance_id') != config.instance_id or value.get('fingerprint') != config.fingerprint()
            or value.get('codex_version') != config.codex_version):
        raise ValueError('unified instance, mode, schema or configuration does not match')
    required = {'backend_live', 'canonical_binding', 'configuration_matches', 'protected_tools',
                'native_reconciled', 'publication_resolved'}
    readiness = value.get('readiness')
    checks = readiness.get('checks') if isinstance(readiness, dict) else None
    if not isinstance(readiness, dict) or readiness.get('state') != 'ready':
        facts = checks if isinstance(checks, dict) else {}
        detail = ', '.join(k + '=' + (facts[k] if facts.get(k) in ('fail', 'unknown') else 'unknown')
                           for k in sorted(required) if facts.get(k) != 'pass')
        raise ValueError('unified service is holding or stopped' + (': ' + detail if detail else ''))
    if not isinstance(checks, dict) or not required <= checks.keys() or any(v != 'pass' for v in checks.values()):
        raise ValueError('unified readiness has missing, failed or unknown facts')
    effective = value.get('effective_settings')
    if (not isinstance(effective, dict) or not isinstance(effective.get('model'), str) or not effective['model']
            or (config.codex_model is not None and effective['model'] != config.codex_model)
            or effective.get('approval_policy') != config.approval_policy
            or not isinstance(effective.get('sandbox'), dict)
            or effective['sandbox'].get('type') != {
                'read-only': 'readOnly', 'workspace-write': 'workspaceWrite', 'danger-full-access': 'dangerFullAccess'}[config.sandbox]):
        raise ValueError('unified descriptor differs from configured model or permissions')
    stamp = value.get('updated_at')
    if type(stamp) not in {int, float} or not 0 <= time.time() - stamp <= max(10, config.poll_interval_ms / 1000 * 3):
        raise ValueError('unified descriptor has no fresh service heartbeat')
    generation, thread = value.get('backend_generation'), value.get('canonical_thread_id')
    if (not isinstance(generation, str) or not re.fullmatch('[a-f0-9]{32}', generation)
            or not isinstance(thread, str) or not SESSION_ID.fullmatch(thread)):
        raise ValueError('unified canonical generation or thread is invalid')
    validation = value.get('protected_tools_validation')
    if (not isinstance(validation, dict) or validation.get('scope') != 'startup'
            or validation.get('backend_generation') != generation
            or type(validation.get('validated_at')) not in {int, float}
            or not 0 < validation['validated_at'] <= stamp
            or value.get('heartbeat_max_age_seconds') != max(10, config.poll_interval_ms / 1000 * 3)
            or value.get('scheduler_state') not in ('idle', 'active')):
        raise ValueError('unified startup validation, freshness bound or scheduler state is unavailable or mismatched')
    count, depth, sources = value.get('attached_client_count'), value.get('queue_depth'), value.get('queue_sources')
    if (type(count) is not int or count not in (0, 1) or type(depth) is not int or depth < 0
            or not isinstance(sources, dict) or set(sources) != {'operator', 'mail', 'peer'}
            or any(type(n) is not int or n < 0 for n in sources.values()) or sum(sources.values()) != depth):
        raise ValueError('unified client or queue facts are missing or inconsistent')
    if binding.get('thread_id') != thread or binding.get('fingerprint') != value['fingerprint']:
        raise ValueError('unified descriptor differs from the durable journal binding')
    endpoint = config.state_dir / ('u-' + generation) / 't'
    if value.get('endpoint') != 'unix://' + str(endpoint):
        raise ValueError('unified endpoint differs from its registered generation')
    return endpoint
