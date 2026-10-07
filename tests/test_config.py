from pathlib import Path
import pytest
from amap_codex.config import Config, Lane

def configured(tmp_path):
    notices, messages = tmp_path/'notices', tmp_path/'messages'
    notices.mkdir(); messages.mkdir()
    instructions = tmp_path/'instructions.md'
    instructions.write_text('Trusted workflow')
    return Config('instance-a','agent@example.test',['/usr/bin/codex','app-server'],[Lane('mail',notices,messages,tmp_path/'mail.claim')],tmp_path/'state','gpt-test','/work','workspace-write','never',instructions)

def test_explicit_paths_and_private_state(tmp_path):
    config = configured(tmp_path).validate()
    assert config.state_dir.stat().st_mode & 0o777 == 0o700
    original = config.fingerprint()
    config.poll_interval_ms = 2000
    assert config.fingerprint() == original
    config.codex_version = 'codex-cli 0.161.0'
    assert config.fingerprint() == original, 'the journal audits a build change; it is not a new instance'
    config.codex_version = 'codex-cli 0.160.1'
    config.codex_model = 'gpt-other'
    assert config.fingerprint() == original, 'the journal audits a model change; it is not a new instance'
    config.codex_model = 'gpt-test'
    config.self_address = 'other@example.test'
    assert config.fingerprint() != original

def test_load_requires_paired_lane(tmp_path):
    path = tmp_path/'connector.toml'
    path.write_text('mail_notice_dir="/tmp/notices"\n')
    with pytest.raises(ValueError,match='requires'):
        Config.load(path)

def test_explicit_empty_lane_is_not_silently_disabled(tmp_path):
    path = tmp_path/'connector.toml'
    path.write_text('[lanes.peer]\n')
    with pytest.raises(ValueError,match='requires'):
        Config.load(path)

def test_load_flat_paths(tmp_path):
    config = configured(tmp_path)
    path = tmp_path/'connector.toml'
    path.write_text(f'''instance_id = "instance-a"
self_address = "agent@example.test"
launch_argv = ["/usr/bin/codex", "app-server"]
mail_notice_dir = "{config.lanes[0].notice_dir}"
mail_message_dir = "{config.lanes[0].message_dir}"
mail_claim_path = "{config.lanes[0].claim_path}"
state_dir = "{config.state_dir}"
codex_model = "gpt-test"
cwd = "/work"
sandbox = "workspace-write"
approval_policy = "never"
operator_instructions = "{config.operator_instructions}"
''')
    assert Config.load(path).lanes == config.lanes
    path.write_text(path.read_text().replace('codex_model = "gpt-test"\n', ''))
    assert Config.load(path).codex_model is None, "no model is Codex's own default"
    path.write_text(path.read_text() + 'codex_model = ""\n')
    with pytest.raises(ValueError, match='codex_model'):
        Config.load(path)

def test_peer_self_and_state_permissions(tmp_path):
    config = configured(tmp_path)
    lane = config.lanes[0]
    config.lanes = [Lane('peer',lane.notice_dir,lane.message_dir,lane.claim_path)]
    config.self_address = None
    with pytest.raises(ValueError,match='self_address'): config.validate()
    config.self_address = 'agent@example.test'
    config.state_dir.mkdir(mode=0o755)
    with pytest.raises(ValueError,match='private'): config.validate()

def test_symlinks_and_instruction_fingerprint(tmp_path):
    config = configured(tmp_path).validate()
    old = config.fingerprint()
    config.operator_instructions.write_text('Changed trusted workflow')
    assert config.fingerprint() != old
    link = tmp_path/'state-link'
    link.symlink_to(config.state_dir,target_is_directory=True)
    config.state_dir = link
    with pytest.raises(ValueError,match='symlink'): config.validate()

@pytest.mark.parametrize('field,value',[('rpc_timeout_seconds',float('nan')),('poll_interval_ms',True),('codex_version','codex-cli 0.1'),('cwd','relative')])
def test_invalid_settings(tmp_path,field,value):
    config = configured(tmp_path)
    setattr(config,field,value)
    with pytest.raises(ValueError): config.validate()
