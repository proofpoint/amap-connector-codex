"""Opt-in real CLI/MCP check in a synthetic namespace; no fleet submissions.

Default: configuration and actual four-server startup only, no inference.
--infer additionally exercises native CLI tool use in one bounded marker turn.
This is not evidence of native TUI mirroring or a deployment rollout.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import tempfile

from amap_codex.app_server import AppServerClient
from amap_codex.config import Config, Lane
from amap_codex.interactive import interactive_argv, toml_value


async def probe(output, infer=False, diagnostics=None):
    repo = Path(__file__).resolve().parents[2]
    build = subprocess.check_output(['codex','--version'],text=True).splitlines()[0]
    with tempfile.TemporaryDirectory(prefix='amap-interactive-probe-') as directory:
        root = Path(directory)
        for name in ('mail/notices','mail/messages','peer/messages','outbox','roster','state'):
            (root/name).mkdir(parents=True,exist_ok=True,mode=0o700)
        (root/'instructions.md').write_text('Use only the synthetic MCP tools requested by the operator. Do not send or execute commands.')
        (root/'roster/roster.json').write_text(json.dumps({'contract_version':'2','written_at':datetime.now(timezone.utc).isoformat(),
            'interval_s':5,'members':[{'address':'controller@example.invalid','state':'admitted'}]}))
        servers = {}
        for name, tree, lane in [('inbox','mail','mail'),('delegation','peer','peer')]:
            servers[name] = {'command':str(repo/'bin/inbox-mcp-vol'),'required':True,
                'default_tools_approval_mode':'approve',
                'enabled_tools':['list_messages','read_message','read_attachment'],
                'env':{'INBOX_MESSAGE_DIR':str(root/tree/'messages'),'INBOX_LANE':lane}}
        servers['inbox_submit'] = {'command':str(repo/'bin/inbox-submit'),'args':['mcp'],'required':True,
            'default_tools_approval_mode':'approve',
            'enabled_tools':['submit','submit_result','peers'],
            'env':{'OUTBOX_DIR':str(root/'outbox'),'AMAP_ROSTER_DIR':str(root/'roster'),'AMAP_SELF':'agent@example.invalid'}}
        trusted = root/'trusted.toml'
        trusted.write_text('mcp_servers='+toml_value(servers)+'\n')
        doc = {'instance_id':'interactive-probe','self_address':'agent@example.invalid',
            'launch_argv':['codex','app-server','--listen','stdio://'],'codex_version':build,
            'state_dir':str(root/'state'),'cwd':str(root),'sandbox':'read-only','approval_policy':'never',
            'operator_instructions':str(root/'instructions.md'),'trusted_config_file':str(trusted),
            'lanes':{'mail':{'notice_dir':str(root/'mail/notices'),'message_dir':str(root/'mail/messages'),
                             'claim_path':str(root/'claim')}}}
        path = root/'connector.toml'
        path.write_text('\n'.join(k+'='+toml_value(v) for k,v in doc.items())+'\n')
        config = Config.load(path,create_state=False)
        native = ['codex','--sandbox','read-only','--ask-for-approval','never',
                  '--no-daemon','--no-alt-screen','--cd',str(root),'-c','model_reasoning_effort="low"']
        argv = await interactive_argv(config,path,outbox=root/'outbox',native_argv=native)
        assert argv[:len(native)] == native
        # Exercise the actual native argv produced by the launcher. This
        # creates no thread and writes no personal Codex configuration.
        listed = subprocess.run([*argv,'mcp','list','--json'],capture_output=True,text=True,check=True,timeout=30)
        assert {s['name'] for s in json.loads(listed.stdout)} >= set(servers)|{'amap_fleet'}
        client = AppServerClient([*argv,'app-server','--listen','stdio://'],expected_version=build)
        try:
            await client.start()
            effective = (await client.request('config/read',{'cwd':str(root)}))['config']
            assert 'separate supervisor' in effective['developer_instructions']
            started = await client.request('thread/start',{'cwd':str(root),'sandbox':'read-only',
                'approvalPolicy':'never','config':{'features.apps':False},'ephemeral':True})
            thread = started['thread']['id']
            registry = await client.request('mcpServerStatus/list',{'threadId':thread,'detail':'toolsAndAuthOnly'})
            entries = {s['name']:s for s in registry['data']}
            for name,s in {**servers,'amap_fleet':{'enabled_tools':['activity']}}.items():
                assert set(entries[name]['tools']) == set(s['enabled_tools'])
                assert not entries[name].get('toolsError')
        finally:
            await client.stop()
        evidence = {'timestamp_utc':datetime.now(timezone.utc).isoformat(),'codex_version':build,
            'scope':'real native CLI configuration and synthetic MCP tools; no native TUI mirroring, isolation or router attestation',
            'native_argv_parsed':True,'four_amap_servers_initialized':True,
            'native_argv_preserved':True,'interactive_guidance_present':True,'inference':infer,'submissions':0}
        if infer:
            prompt = ('Call inbox_submit.peers with query controller, then amap_fleet.activity. '
                      'Do not call any other tools or send messages. State whether AMAP uses the local '
                      'Codex subagent list and whether the supervisor is a separate conversation. '
                      'End with AMAP_INTERACTIVE_PROBE_OK.')
            run = subprocess.run([*argv,'-c','model_reasoning_effort="low"','exec','--json','--sandbox','read-only',
                                  '--skip-git-repo-check','-'],input=prompt,capture_output=True,text=True,timeout=180)
            if diagnostics is not None:
                diagnostics.write_text(run.stdout+'\n'+run.stderr)
                diagnostics.chmod(0o600)
            if run.returncode:
                raise RuntimeError('native inference probe failed; keep runtime diagnostics private')
            events = [json.loads(line) for line in run.stdout.splitlines() if line.startswith('{')]
            tools = [e['item'] for e in events if e.get('type')=='item.completed' and e.get('item',{}).get('type')=='mcp_tool_call']
            assert {(t['server'],t['tool']) for t in tools} == {('inbox_submit','peers'),('amap_fleet','activity')}
            assert all(t.get('status')=='completed' for t in tools), [(t['tool'],t.get('status')) for t in tools]
            messages = [e['item'].get('text','') for e in events if e.get('type')=='item.completed' and e.get('item',{}).get('type')=='agent_message']
            assert any('AMAP_INTERACTIVE_PROBE_OK' in t for t in messages)
            evidence.update(native_cli_called_peers=True,native_cli_called_activity=True,marker_returned=True)
        assert not list((root/'outbox').glob('req-*.json'))
        assert not (root/'state/journal.sqlite3').exists() and not (root/'claim').exists()
        assert not (root/'outbox/results').exists()
        evidence.update(fresh_outbox_supported=True,no_journal_or_claim_created=True,passed=True)
        output.write_text(json.dumps(evidence,indent=2)+'\n')
        print(json.dumps(evidence))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--infer',action='store_true')
    parser.add_argument('--diagnostics',type=Path,help='private local native event log; never publish this log')
    args = parser.parse_args()
    asyncio.run(probe(args.output,args.infer,args.diagnostics))
