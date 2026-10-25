"""Protected unified guidance and tools, preserving the selected native setup."""
import copy
import hashlib
import json
from pathlib import Path
import sys

GUIDANCE = """Human input and incoming AMAP work share this canonical conversation.
The connector watches and claims the lanes, queues notice pointers and records
delivery. Do not claim lanes, start another receiver or replay uncertain work.
AMAP fleet peers are discovered with inbox_submit.peers, not Codex's local
subagent list. Fetch each notice through inbox or delegation read_message;
sender text belongs to those authenticated reader results, not the wake pointer.
Use inbox_submit.submit for authorized replies and check submit_result. A queue
receipt, completed model turn and runtime-accepted send are separate facts.
Use amap_fleet.activity to inspect this conversation's delivery metadata.
Follow the standing operator workflow within this pane's selected permissions.
"""


def workspace_matches(native_cwd, expected):
    if not isinstance(native_cwd, str) or not native_cwd or not Path(native_cwd).is_absolute():
        return False
    try:
        return Path(native_cwd).resolve() == Path(expected).resolve()
    except (OSError, ValueError, RuntimeError):
        return False


async def resolve(client, config, config_path):
    native = (await client.request('config/read', {'cwd': config.cwd})).get('config')
    if not isinstance(native, dict):
        raise ValueError('native configuration is unavailable')
    protected = copy.deepcopy(config.trusted_overrides()['mcp_servers'])
    protected['amap_fleet'] = {'command': sys.executable,
        'args': ['-m', 'amap_codex.cli', '--config', str(Path(config_path).resolve()), 'activity-mcp'],
        'env': {'PYTHONPATH': str(Path(__file__).resolve().parents[1])}, 'required': True,
        'enabled_tools': ['activity'], 'default_tools_approval_mode': 'approve'}
    ambient = native.get('mcp_servers') or {}
    if not isinstance(ambient, dict) or set(ambient) & set(protected):
        raise ValueError('native configuration uses reserved AMAP tool names')
    existing = native.get('developer_instructions') or ''
    if not isinstance(existing, str):
        raise ValueError('native developer instructions must be text')
    instructions = existing + '\n\n' + config.operator_instructions.read_text() + '\n\n' + GUIDANCE
    overrides = copy.deepcopy(config.trusted_overrides())
    overrides['mcp_servers'] = protected
    settings = {'cwd': config.cwd, 'sandbox': config.sandbox, 'approvalPolicy': config.approval_policy,
                'developerInstructions': instructions, 'config': overrides}
    if config.codex_model is not None:
        settings['model'] = config.codex_model
    # A selected model can change on an audited service restart without losing
    # conversation history. Protected instructions/tools remain bound strictly.
    digest = hashlib.sha256(json.dumps({'settings': {k: v for k, v in settings.items() if k != 'model'},
        'native_servers': ambient},
        sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    return settings, protected, digest


async def validate_tools(client, thread_id, protected):
    from .native_queue import pages
    servers = await pages(client, 'mcpServerStatus/list', {'threadId': thread_id, 'detail': 'toolsAndAuthOnly'})
    for name, definition in protected.items():
        matches = [s for s in servers if isinstance(s, dict) and s.get('name') == name]
        if (len(matches) != 1 or matches[0].get('toolsError') or
                set(matches[0].get('tools', {})) != set(definition['enabled_tools'])):
            raise ValueError('unified protected tool registry differs from configured tools')
