"""Crash boundaries, shared ownership and privileged kickoff conformance."""
import asyncio
from dataclasses import replace
import json
import os
import sys

import pytest

from amap_codex.claims import ClaimHeld
from amap_codex.lifecycle import ControlUnknown, LauncherControl
from amap_codex.host_guard import HostGuard
from amap_codex.journal import Journal
from amap_codex.operator import enqueue
from amap_codex.supervisor import Supervisor
from test_supervisor import config_for, history, publish, until


def controlled(tmp_path):
    c = config_for(tmp_path)
    helper = tmp_path/'control.py'
    remote = tmp_path/'remote.json'
    remote.write_text(json.dumps({'state':'stopped'}))
    helper.write_text('''import json,sys
from pathlib import Path
r=json.load(sys.stdin)
state=json.loads(Path(sys.argv[1]).read_text())['state']
print(json.dumps({**r,'state':state}))
''')
    c = replace(c, launcher_control_argv=[sys.executable,str(helper),str(remote)],
                deployment_id='fixture',owner_domain='host:test',launcher_control_timeout_seconds=.2)
    return c.validate(), remote


def test_wrong_binding_and_unknown_control_are_not_cleanup(tmp_path):
    c, remote = controlled(tmp_path)
    remote.write_text('{"state":"unknown"}')
    with pytest.raises(ControlUnknown): LauncherControl(c).require_stopped('exec1')
    c.launcher_control_argv = [sys.executable,'-c', 'import sys; print("x"*70000)']
    with pytest.raises(ControlUnknown): LauncherControl(c).call('inspect','exec1')


def test_foreign_and_legacy_pid_domains_never_reclaimed(tmp_path):
    c, remote = controlled(tmp_path)
    lane = c.lanes[0]
    lane.claim_path.write_text(json.dumps({'pid':99999999,'consumer':'claude'}))
    guard=HostGuard(lane.claim_path,lane.notice_dir,c.owner_domain,c.deployment_id,'new',LauncherControl(c))
    with pytest.raises(ClaimHeld): guard.acquire()
    assert json.loads(lane.claim_path.read_text())['consumer']=='claude'


def test_surviving_execution_blocks_stale_host_guard(tmp_path):
    c, remote = controlled(tmp_path)
    lane=c.lanes[0]
    guard=HostGuard(lane.claim_path,lane.notice_dir,c.owner_domain,c.deployment_id,'old',LauncherControl(c)).acquire()
    guard.release(clear=False)
    old=json.loads(lane.claim_path.read_text()); old['pid']=99999999
    lane.claim_path.write_text(json.dumps(old))
    remote.write_text('{"state":"running"}')
    new=HostGuard(lane.claim_path,lane.notice_dir,c.owner_domain,c.deployment_id,'new',LauncherControl(c))
    with pytest.raises(ControlUnknown): new.acquire()
    remote.write_text('{"state":"stopped"}')
    new.acquire().release()


def test_unknown_stop_preserves_binding_and_claim(tmp_path):
    c,remote=controlled(tmp_path)
    async def check():
        s=Supervisor(c); await s.start()
        remote.write_text('{"state":"unknown"}')
        with pytest.raises(ControlUnknown): await s.close()
        assert (c.state_dir/'process.json').exists()
        assert c.lanes[0].claim_path.exists()
    asyncio.run(check())


def test_kickoff_serial_durable_and_deduplicated_on_restart(tmp_path):
    c=replace(config_for(tmp_path),operator_kickoff_enabled=True)
    enqueue(c,'R1','Perform this synthetic operator test.')
    publish(c)
    async def check():
        s=Supervisor(c)
        try:
            await s.start()
            await until(s,lambda:len(history(c))==2 and s.journal.status()['counts'].get('finished')==1)
            assert s.journal.operator_runs()[0]['state']=='finished'
            assert len(s.journal.db.execute('SELECT * FROM outcome_transitions').fetchall())==0
        finally: await s.close()
        s=Supervisor(c)
        try:
            await s.start()
            for _ in range(4): await s.tick()
            assert len(history(c))==2
        finally: await s.close()
    asyncio.run(check())


def test_committed_operator_attempt_is_uncertain_and_blocks_mail(tmp_path):
    c=replace(config_for(tmp_path),operator_kickoff_enabled=True)
    with Journal(c.state_dir,c.instance_id,c.fingerprint(),c.codex_version, c.codex_model) as j:
        j.bind_thread('thread-fixture'); j.admit_operator('R1','hash'); j.begin_operator('R1',8)
    with Journal(c.state_dir,c.instance_id,c.fingerprint(),c.codex_version, c.codex_model) as j:
        assert j.operator_runs()[0]['state']=='uncertain'
        assert j.next_pending() is None


def test_operator_disposition_is_audited_and_never_resends(tmp_path):
    c=replace(config_for(tmp_path),operator_kickoff_enabled=True)
    with Journal(c.state_dir,c.instance_id,c.fingerprint(),c.codex_version, c.codex_model) as j:
        j.bind_thread('thread-fixture'); j.admit_operator('R1','hash'); j.begin_operator('R1',8)
    with Journal(c.state_dir,c.instance_id,c.fingerprint(),c.codex_version, c.codex_model) as j:
        j.dispose_operator('R1','hold','Original-input evidence incomplete')
        assert j.operator_blocked()
        j.dispose_operator('R1','handled','Operator reviewed effects and retired this run')
        assert not j.operator_blocked()
        assert j.operator_runs()[0]['execution_status']=='operator_handled'
        assert j.db.execute('SELECT COUNT(*) FROM audit').fetchone()[0]==2
        j.admit_operator('R1','hash')
        assert j.operator_runs()[0]['state']=='finished'


def test_control_timeout_and_wrong_identity_fail_closed(tmp_path):
    c,_=controlled(tmp_path)
    for source in ('import time; time.sleep(10)',
                   'import json,sys; r=json.load(sys.stdin); r["execution_id"]="different"; print(json.dumps({**r,"state":"stopped"}))'):
        c.launcher_control_argv=[sys.executable,'-c',source]
        with pytest.raises(ControlUnknown): LauncherControl(c).require_stopped('exec1')


def test_trusted_configuration_requires_exact_registry_and_hashes_content(tmp_path):
    c=config_for(tmp_path); path=tmp_path/'trusted.toml'
    def text(extra=''):
        readers=['inbox','delegation']
        result=''
        for name in readers:
            result+=f'[mcp_servers.{name}]\nrequired=true\nenabled_tools=["list_messages","read_message","read_attachment"]\n'
        return result+'[mcp_servers.inbox_submit]\nrequired=true\nenabled_tools=["submit","submit_result","peers"]\n'+extra
    path.write_text(text()); protected=replace(c,trusted_config_file=path)
    protected.validate(); before=protected.fingerprint()
    path.write_text(text()+'# changed trusted configuration\n')
    assert protected.fingerprint()!=before
    path.write_text(text('[mcp_servers.ambient]\nrequired=true\n'))
    with pytest.raises(ValueError,match='exactly three'): protected.validate()


def test_accepted_operator_can_be_retired_without_completion_evidence(tmp_path):
    c=replace(config_for(tmp_path),operator_kickoff_enabled=True)
    with Journal(c.state_dir,c.instance_id,c.fingerprint(),c.codex_version, c.codex_model) as j:
        j.bind_thread('thread-fixture'); j.admit_operator('R1','hash'); j.begin_operator('R1',8)
        j.operator_result('R1','accepted',turn_id='accepted-turn')
        j.dispose_operator('R1','handled','Controller stopped; operator inspected effects and retired run')
        run=j.operator_runs()[0]
        assert run['state']=='finished' and run['turn_id']=='accepted-turn'
        assert run['execution_status']=='operator_handled'
        assert not j.operator_blocked()
