"""Read-only status, audited recovery, and bounded diagnostic probes."""
import asyncio
import json
from pathlib import Path
import sqlite3
import sys

import pytest

from amap_codex.app_server import AppServerClient, AppServerDelivery
from amap_codex.cli import doctor_probe, main, read_status
from amap_codex.config import Config, Lane
from amap_codex.journal import Journal
from amap_codex.spool import Admission, event_id
from amap_codex.supervisor import Ownership

FAKE=str(Path(__file__).with_name('fake_app_server.py'))

def configured(tmp_path):
    notices,messages=tmp_path/'notices',tmp_path/'messages'
    notices.mkdir(); messages.mkdir()
    instructions=tmp_path/'instructions.md'
    instructions.write_text('Trusted operator workflow')
    config=Config('test-cli','agent@example.test',[sys.executable,FAKE,'normal',str(tmp_path/'history.json')],
        [Lane('mail',notices,messages,tmp_path/'consumer.claim')],tmp_path/'state',
        'fixture','/workspace','workspace-write','never',instructions,rpc_timeout_seconds=0.4)
    config.validate()
    document={
        'instance_id':config.instance_id,'self_address':config.self_address,
        'launch_argv':config.launch_argv,'mail_notice_dir':str(notices),
        'mail_message_dir':str(messages),'mail_claim_path':str(config.lanes[0].claim_path),
        'state_dir':str(config.state_dir),'codex_model':config.codex_model,'cwd':config.cwd,
        'sandbox':config.sandbox,'approval_policy':config.approval_policy,
        'operator_instructions':str(instructions),'rpc_timeout_seconds':config.rpc_timeout_seconds}
    path=tmp_path/'connector.toml'
    path.write_text('\n'.join(f'{key} = {json.dumps(value)}' for key,value in document.items())+'\n')
    return config,path

def unresolved(config):
    eid=event_id(config.instance_id,'mail','receiver_notice_a')
    record=Admission(eid,'mail','receiver_notice_a','a'*64,
        {'event_id':eid,'lane':'mail','notice_id':'receiver_notice_a'})
    with Journal(config.state_dir,config.instance_id,config.fingerprint(),config.codex_version) as journal:
        journal.admit(record); journal.bind_thread('thread-fixture'); journal.begin_attempt(eid,41)
    return eid

def raw_event(config,eid):
    db=sqlite3.connect(config.state_dir/'journal.sqlite3')
    try:
        return db.execute('SELECT state FROM events WHERE event_id=?',(eid,)).fetchone()[0]
    finally:
        db.close()

def test_status_is_read_only_even_with_committed_dispatch(tmp_path,capsys):
    config,path=configured(tmp_path)
    eid=unresolved(config)
    before=(config.state_dir/'journal.sqlite3').read_bytes()
    assert main(['--config',str(path),'status'])==0
    value=json.loads(capsys.readouterr().out)
    assert value['counts']=={'dispatching':1}
    assert value['current_turn']['event_id']==eid
    assert raw_event(config,eid)=='dispatching'
    assert (config.state_dir/'journal.sqlite3').read_bytes()==before
    assert not config.lanes[0].claim_path.exists()
    assert not (config.state_dir/'process.json').exists()


def test_status_not_started_and_doctor_do_not_launch_or_create_journal(tmp_path,capsys):
    config,path=configured(tmp_path)
    assert main(['--config',str(path),'status'])==0
    assert json.loads(capsys.readouterr().out)['state']=='not_started'
    assert main(['--config',str(path),'doctor'])==0
    assert json.loads(capsys.readouterr().out)['configuration']=='valid'
    assert not (config.state_dir/'journal.sqlite3').exists()
    assert not Path(config.launch_argv[-1]).exists()
    assert not config.lanes[0].claim_path.exists()


def test_status_missing_state_fails_without_creating_directory(tmp_path,capsys):
    config,path=configured(tmp_path)
    config.state_dir.rmdir()
    assert main(['--config',str(path),'status'])==1
    assert not config.state_dir.exists()
    assert 'directory' in capsys.readouterr().err


def test_recovery_requires_evidence_and_writes_audit(tmp_path,capsys):
    config,path=configured(tmp_path)
    eid=unresolved(config)
    base=['--config',str(path),'recover',eid]
    assert main(base+['--action','retry','--note','Prior input not submitted'])==1
    assert 'positive' in capsys.readouterr().err
    assert raw_event(config,eid)=='uncertain'
    assert main(base+['--action','hold','--note','Need complete transport evidence'])==0
    assert json.loads(capsys.readouterr().out)['audited']
    assert main(base+['--action','retry','--note','Request never written',
        '--evidence-reference','transport capture proves no turn/start write'])==0
    assert json.loads(capsys.readouterr().out)['action']=='retry'
    assert raw_event(config,eid)=='pending'
    db=sqlite3.connect(config.state_dir/'journal.sqlite3')
    try:
        actions=db.execute('SELECT action,note FROM audit ORDER BY id').fetchall()
        assert [row[0] for row in actions]==['hold','retry']
        assert 'transport capture' in actions[-1][1]
    finally:
        db.close()


def test_recovery_cannot_run_while_controller_owns_claim(tmp_path,capsys):
    config,path=configured(tmp_path)
    eid=unresolved(config)
    with Ownership(config):
        assert main(['--config',str(path),'recover',eid,'--action','handled',
            '--note','Operator completed task'])==1
        assert 'another controller' in capsys.readouterr().err
        assert raw_event(config,eid)=='dispatching'
    assert not config.lanes[0].claim_path.exists()


def test_probe_pins_thread_records_launcher_and_never_delivers(tmp_path,monkeypatch):
    config,path=configured(tmp_path)
    original=AppServerDelivery.start_or_resume
    observed=[]
    async def inspect_record(self,thread_id=None):
        result=await original(self,thread_id)
        record=json.loads((config.state_dir/'process.json').read_text())
        observed.append(record)
        assert record['pid']==self.client.process.pid
        assert config.lanes[0].claim_path.exists()
        return result
    monkeypatch.setattr(AppServerDelivery,'start_or_resume',inspect_record)
    result=asyncio.run(doctor_probe(config))
    assert result['thread_id']=='thread-fixture'
    assert result['notice_delivered'] is False
    assert len(observed)==1
    assert read_status(config)['thread_id']=='thread-fixture'
    assert not (config.state_dir/'process.json').exists()
    assert not config.lanes[0].claim_path.exists()
    assert not Path(config.launch_argv[-1]).exists()


def test_probe_refuses_active_or_uncertain_journal_without_launch(tmp_path):
    config,path=configured(tmp_path)
    eid=unresolved(config)
    with pytest.raises(RuntimeError,match='active or unresolved'):
        asyncio.run(doctor_probe(config))
    assert raw_event(config,eid)=='dispatching'
    assert not config.lanes[0].claim_path.exists()
    assert not (config.state_dir/'process.json').exists()


def test_probe_version_mismatch_does_not_bind_thread_and_reaps(tmp_path,monkeypatch):
    config,path=configured(tmp_path)
    original=AppServerClient.request
    async def wrong_version(self,method,params,**kwargs):
        result=await original(self,method,params,**kwargs)
        if method=='initialize': result['userAgent']='codex-fixture/0.160.10'
        return result
    monkeypatch.setattr(AppServerClient,'request',wrong_version)
    with pytest.raises(RuntimeError,match='version'):
        asyncio.run(doctor_probe(config))
    assert not (config.state_dir/'journal.sqlite3').exists()
    assert not (config.state_dir/'process.json').exists()
    assert not config.lanes[0].claim_path.exists()
