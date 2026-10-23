"""Interactive inspection uses actual journal producers, never restart recovery."""
import asyncio
from dataclasses import replace
import io
import json
from pathlib import Path
import sys
import time
import tomllib

import pytest

from amap_codex import interactive
from amap_codex.cli import main
from amap_codex.journal import Journal
from amap_codex.spool import Admission
from amap_codex.supervisor import Ownership
from test_cli import configured


def trusted(config, tmp_path):
    servers = {}
    for name in ('inbox', 'delegation', 'inbox_submit'):
        servers[name] = {'command': '/example/reader', 'required': True,
            'enabled_tools': ['submit','submit_result','peers'] if name == 'inbox_submit'
                             else ['list_messages','read_message','read_attachment']}
    path = tmp_path/'trusted.toml'
    path.write_text('mcp_servers='+interactive.toml_value(servers)+'\n')
    path.chmod(0o600)
    config.trusted_config_file = path
    return config.validate(create_state=False)


def produced(config):
    journal = Journal(config.state_dir, config.instance_id, config.fingerprint(),
                      config.codex_version, config.codex_model)
    journal.bind_thread('owned-thread')
    journal.admit(Admission('event-private','mail','notice-a','a'*64,
                           {'notice_id':'notice-a'}))
    journal.begin_attempt('event-private', 8)
    journal.admit_operator('operator-a', 'PRIVATE_INSTRUCTION_HASH')
    return journal


def outbox_at(tmp_path):
    root = tmp_path/'outbox'
    (root/'results').mkdir(parents=True)
    (root/'req-00000007.json').write_text('SECRET_REQUEST_BODY')
    result = {'contract_version':'2','req_id':'00000006','outcome':'accepted',
              'message_id':'peer-original','reason_code':'peer_routed','ts':'test',
              'detail':'SECRET_DETAIL','body_text':'SECRET_BODY'}
    (root/'results/00000006.json').write_text(json.dumps(result))
    return root


def test_observation_with_live_owner_never_reconciles_dispatch_or_exposes_content(tmp_path):
    config,_ = configured(tmp_path)
    with produced(config) as journal, Ownership(config):
        before = journal.db.total_changes
        database_bytes = (config.state_dir/'journal.sqlite3').read_bytes()
        value = interactive.read_activity(config, outbox=outbox_at(tmp_path))
        assert value['status']['current_turn']['state'] == 'dispatching'
        assert value['events'][0]['state'] == 'dispatching'
        assert value['status']['operator_runs'][0]['state'] == 'pending'
        assert value['outbound']['results'][0]['outcome'] == 'accepted'
        assert value['outbound']['queued_request_files'] == ['req-00000007.json']
        assert journal.event('event-private')['state'] == 'dispatching'
        assert journal.operator_runs()[0]['state'] == 'pending'
        assert journal.db.total_changes == before
        assert (config.state_dir/'journal.sqlite3').read_bytes() == database_bytes
        assert config.lanes[0].claim_path.exists()
        assert 'SECRET_' not in json.dumps(value)


def test_operator_dispatch_inspection_does_not_mark_it_uncertain(tmp_path):
    config,_ = configured(tmp_path)
    with Journal(config.state_dir,config.instance_id,config.fingerprint(),config.codex_version,config.codex_model) as journal:
        journal.bind_thread('owned-thread')
        journal.admit_operator('operator-a','hash')
        journal.begin_operator('operator-a',9)
        assert interactive.read_activity(config)['status']['operator_runs'][0]['state'] == 'dispatching'
        assert journal.operator_runs()[0]['state'] == 'dispatching'


def test_activity_not_started_unknown_outbound_and_limit_validation(tmp_path):
    config,_ = configured(tmp_path)
    value = interactive.read_activity(config)
    assert value['status']['state'] == 'not_started'
    assert value['events'] == [] and value['heartbeat'] == 'unknown'
    assert value['outbound']['state'] == 'not_configured'
    assert not (config.state_dir/'journal.sqlite3').exists()
    for invalid in (0, 101, True, '10'):
        with pytest.raises(ValueError, match='limit'):
            interactive.read_activity(config, limit=invalid)


def test_wrong_instance_and_symlinked_journal_refused(tmp_path):
    config,_ = configured(tmp_path)
    with produced(config):
        with pytest.raises(ValueError, match='another instance'):
            interactive.read_activity(replace(config, instance_id='another'))
    journal = config.state_dir/'journal.sqlite3'
    journal.rename(config.state_dir/'real.sqlite3')
    journal.symlink_to(config.state_dir/'real.sqlite3')
    with pytest.raises(ValueError, match='symlink'):
        interactive.read_activity(config)


def test_result_unknown_malformed_and_symlink_are_never_presented_as_success(tmp_path):
    config,_ = configured(tmp_path)
    root = outbox_at(tmp_path)
    result = root/'results/00000006.json'
    for contents in ('not json', '[]', '{"contract_version":3}', '{"contract_version":"3","req_id":"x","outcome":"accepted"}',
                     '{"contract_version":"2","req_id":"x"}',
                     '{"contract_version":"2","req_id":"x","outcome":true}',
                     json.dumps({'contract_version':'2','req_id':'x','outcome':'x'*301})):
        result.write_text(contents)
        assert interactive.read_activity(config, outbox=root)['outbound']['results'][0].get('state') == 'unreadable_result'
    result.unlink()
    target = tmp_path/'private.json'
    target.write_text('{"contract_version":"2","req_id":"x","outcome":"accepted"}')
    result.symlink_to(target)
    assert interactive.read_activity(config, outbox=root)['outbound']['results'][0].get('state') == 'unreadable_result'
    link=tmp_path/'linked'
    link.symlink_to(root,target_is_directory=True)
    with pytest.raises(ValueError, match='outbox'):
        interactive.read_activity(config, outbox=link)
    result.unlink()
    (root/'results').rmdir()
    (root/'results').symlink_to(tmp_path)
    with pytest.raises(ValueError, match='results'):
        interactive.read_activity(config, outbox=root)
    with pytest.raises(ValueError, match='outbox'):
        interactive.read_activity(config, outbox=Path('relative'))
    with pytest.raises(ValueError, match='outbox'):
        interactive.read_activity(config, outbox=tmp_path/'missing')


def test_watch_reports_actual_state_changes_and_stale_heartbeat(tmp_path, monkeypatch):
    config,_ = configured(tmp_path)
    with produced(config) as journal:
        journal.accept('event-private','turn-a')
        # Producer writes heartbeat; the observer must not rewrite it.
        snapshot = config.state_dir/'status.json'
        snapshot.write_text(json.dumps({'updated_at':time.time()}))
        output = io.StringIO()
        ticks = []
        def step(interval):
            ticks.append(interval)
            if len(ticks) == 1:
                journal.finish('event-private','completed')
            elif len(ticks) == 2:
                snapshot.write_text(json.dumps({'updated_at':time.time()}))
            elif len(ticks) == 3:
                snapshot.write_text(json.dumps({'updated_at':0}))
            else:
                raise KeyboardInterrupt
        monkeypatch.setattr(interactive.time, 'sleep', step)
        with pytest.raises(KeyboardInterrupt):
            interactive.watch_activity(config, follow=True, output=output)
        values = [json.loads(line) for line in output.getvalue().splitlines()]
        assert [v['events'][0]['state'] for v in values] == ['accepted','finished','finished']
        assert values[-1]['heartbeat'] == 'stale'
    for interval in (0,float('nan'),61):
        with pytest.raises(ValueError, match='interval'):
            interactive.watch_activity(config, interval=interval)


def test_activity_mcp_offers_only_bounded_read_only_tool_and_survives_invalid_requests(tmp_path):
    config,_ = configured(tmp_path)
    requests = [[], {'id':1,'method':'initialize'}, {'id':2,'method':'tools/list'},
                {'id':3,'method':'tools/call','params':{'name':'activity','arguments':{'limit':1}}},
                {'id':4,'method':'tools/call','params':{'name':'kickoff'}},
                {'id':5,'method':'tools/call','params':{'name':'activity','arguments':{'limit':101}}},
                {'id':6,'method':'tools/call','params':{'name':'activity','arguments':{'other':1}}}]
    output = io.StringIO()
    interactive.serve_activity(config, source=io.BytesIO(b'bad json\n'+b''.join((json.dumps(v)+'\n').encode() for v in requests)), output=output)
    values = [json.loads(line) for line in output.getvalue().splitlines()]
    by_id = {v['id']:v for v in values}
    assert by_id[2]['result']['tools'][0]['name'] == 'activity'
    assert len(by_id[2]['result']['tools']) == 1
    assert not by_id[3]['result'].get('isError')
    assert 'error' in by_id[4]
    assert all(by_id[i]['result'].get('isError') for i in (5,6))
    assert not (config.state_dir/'journal.sqlite3').exists()
    with pytest.raises(ValueError, match='frame'):
        interactive.serve_activity(config, source=io.BytesIO(b'x'*(1024*1024+1)), output=io.StringIO())


def native_client(monkeypatch, native=None):
    clients = []
    class Client:
        def __init__(self,*a,**k): self.calls=[];self.stopped=False;clients.append(self)
        async def start(self): pass
        async def request(self,method,params):
            self.calls.append(method)
            return {'config':native if native is not None else {'mcp_servers':{'personal':{}},'developer_instructions':'EXISTING_POLICY'}}
        async def stop(self): self.stopped=True
    monkeypatch.setattr(interactive,'AppServerClient',Client)
    return clients


def test_interactive_args_preserve_policy_use_installed_paths_and_do_not_change_permissions(tmp_path,monkeypatch):
    config,path = configured(tmp_path)
    trusted(config,tmp_path)
    clients = native_client(monkeypatch)
    args = asyncio.run(interactive.interactive_argv(config,path))
    values = [tomllib.loads(args[i+1]) for i,item in enumerate(args) if item == '-c']
    servers = {k:v for doc in values for k,v in doc.get('mcp_servers',{}).items()}
    assert set(servers) == {'inbox','delegation','inbox_submit','amap_fleet'}
    assert servers['amap_fleet']['command'] == sys.executable
    assert str(path) in servers['amap_fleet']['args']
    assert 'EXISTING_POLICY' in values[-1]['developer_instructions']
    assert config.operator_instructions.read_text() in values[-1]['developer_instructions']
    assert 'local\nsubagent' in values[-1]['developer_instructions']
    assert 'separate supervisor' in values[-1]['developer_instructions']
    assert all(not any(k in doc for k in ('sandbox_mode','approval_policy','model','features')) for doc in values)
    assert clients[0].calls == ['config/read'] and clients[0].stopped
    assert not config.lanes[0].claim_path.exists()
    assert not (config.state_dir/'journal.sqlite3').exists()


@pytest.mark.parametrize('native',[[], {'mcp_servers':{'amap_fleet':{}}}, {'mcp_servers':{'inbox':{}}},
                                  {'mcp_servers':['bad']}, {'developer_instructions':42}])
def test_interactive_refuses_reserved_names_and_invalid_native_config(tmp_path,monkeypatch,native):
    config,path = configured(tmp_path)
    trusted(config,tmp_path)
    clients = native_client(monkeypatch,native)
    with pytest.raises(ValueError):
        asyncio.run(interactive.interactive_argv(config,path))
    assert clients[0].stopped


def test_interactive_requires_trusted_config_and_cli_exec_has_no_shell(tmp_path,monkeypatch,capsys):
    config,path = configured(tmp_path)
    native_client(monkeypatch)
    with pytest.raises(ValueError,match='trusted_config'):
        asyncio.run(interactive.interactive_argv(config,path))
    from amap_codex.config import Config
    trusted(config,tmp_path)
    monkeypatch.setattr(Config,'load',lambda *a,**k: config)
    native_client(monkeypatch)
    assert main(['--config',str(path),'interactive-args']) == 0
    assert json.loads(capsys.readouterr().out)[0] == 'codex'
    executed=[]
    monkeypatch.setattr('os.execvp',lambda binary,args: executed.append((binary,args)))
    assert main(['--config',str(path),'interactive','--prompt','Hello; $(do-not-execute)']) == 0
    assert executed[0][1][-2:] == ['--','Hello; $(do-not-execute)']
    assert 'separate supervisor' in capsys.readouterr().err


def test_toml_encoding_round_trips_strings_containing_shell_syntax_and_unicode():
    value={'text':'quote " newline\n dollar $(x) `x` Unicode café 🚀','nested':[True,3,{'a.b':'x'}]}
    assert tomllib.loads('value='+interactive.toml_value(value))['value'] == value
    with pytest.raises(ValueError):
        interactive.toml_value(None)
    with pytest.raises(ValueError):
        interactive.toml_value(float('nan'))


def test_limits_newest_order_and_unknown_send_outcome(tmp_path):
    config,_ = configured(tmp_path)
    with produced(config) as journal:
        journal.accept('event-private','turn-a')
        journal.finish('event-private','completed')
        journal.admit(Admission('new-event','mail','new-notice','b'*64,{'notice_id':'new-notice'}))
        value=interactive.read_activity(config,limit=1)
        assert [e['event_id'] for e in value['events']]==['new-event']
    outbox=outbox_at(tmp_path)
    path=outbox/'results/00000006.json'
    result=json.loads(path.read_text()); result['outcome']='future_runtime_state'; path.write_text(json.dumps(result))
    assert interactive.read_activity(config,outbox=outbox)['outbound']['results'][0]['outcome']=='future_runtime_state'


def test_uncertain_work_is_visible_and_never_replayed_by_inspection(tmp_path):
    config,_=configured(tmp_path)
    with produced(config) as journal:
        journal.uncertain('event-private','original acceptance not known')
        for _ in range(2):
            value=interactive.read_activity(config)
            assert value['status']['uncertain_count']==1
            assert value['events'][0]['state']=='uncertain'
        assert journal.event('event-private')['state']=='uncertain'
        assert journal.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]==1
