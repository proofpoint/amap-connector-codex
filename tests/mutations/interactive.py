"""Reproduce interactive guard mutations without touching the working tree.

Run with the existing test interpreter:
    python tests/mutations/interactive.py

Requires pytest and the unmodified matching tests/src tree. Every case gets
its own scratch copy and runs only its named negative control. No native
Codex, inference, network, authentication, Docker or fleet state is used.
"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import os,re,shutil,subprocess,sys,tempfile

repo=Path(__file__).resolve().parents[2]
python=sys.executable
paths=['src/amap_codex/interactive.py','src/amap_codex/cli.py']
sources={p:(repo/p).read_text() for p in paths}
cases=[]
def case(name,old,new,test,path=paths[0],marker=None):
    assert old in sources[path],name
    cases.append((name,[(path,old,new)],test,marker))

for key in ['fresh_heartbeat','configuration_matches','codex_build_matches','thread_bound',
            'live_claims_match_snapshot_owner','dispatch_resolved','operational_error_clear','blocked_requests_clear']:
    old=re.search(r"^        '"+key+r"':.*\n",sources[paths[0]],re.M).group()
    case('readiness '+key,old,"        '"+key+"': True,\n",'test_readiness_without_binding_or_snapshot_stays_explicit' if key=='thread_bound' else 'test_ready_requires_real_producer_binding_owner_and_reconciliation')
old=re.search(r"        'snapshot_matches_binding':.*?        'live_claims_match_snapshot_owner':",sources[paths[0]],re.S).group()
case('snapshot binding and held evidence',old,"        'snapshot_matches_binding': True,\n        'live_claims_match_snapshot_owner':",'test_ready_requires_real_producer_binding_owner_and_reconciliation')
case('remote owner is unknown','if config.launcher_control_argv:','if False:', 'test_readiness_never_infers_remote_ownership_from_local_pid')
case('malformed owner PID is unknown',"if type(owner.get('pid')) is not int or owner['pid'] <= 0:",'if False:','test_readiness_without_binding_or_snapshot_stays_explicit')
case('missing snapshot PID is unknown',"if type(snapshot.get('supervisor_pid')) is not int or snapshot['supervisor_pid'] <= 0:","if type(snapshot.get('supervisor_pid')) is not int:",'test_readiness_without_binding_or_snapshot_stays_explicit')
case('fresh result absence is not success',"'observed' if has_results else 'no_results_yet'","'observed'",'test_fresh_outbox_is_explicitly_no_results_yet_and_not_created_by_observation')
case('dangling result link refused',"if results.is_symlink() or (results.exists() and not results.is_dir()):","if results.exists() and not results.is_dir():",'test_fresh_outbox_is_explicitly_no_results_yet_and_not_created_by_observation')
case('non-directory results refused',"if results.is_symlink() or (results.exists() and not results.is_dir()):","if results.is_symlink():",'test_fresh_outbox_is_explicitly_no_results_yet_and_not_created_by_observation')
case('subcommands and unknown flags refused','if name not in values:','if False:','test_native_wrapper_subcommands_are_refused_but_literal_prompt_is_not_a_subcommand')
case('native config reaches preflight',"preflight += ['-c', value]",'pass','test_native_wrapper_preflight_refuses_reserved_name_from_native_overrides')
case('native features reach preflight',"preflight += [name, value]",'pass','test_native_wrapper_effective_cwd_and_attached_options')
case('native cwd reaches preflight',"{'cwd': cwd}","{'cwd': config.cwd}",'test_native_wrapper_effective_cwd_and_attached_options')
case('required config assignment',"if '=' not in value:",'if False:','test_native_wrapper_unsupported_or_incomplete_arguments_fail_before_launch')
case('mutually exclusive binary/native argv','if native_argv is not None and binary is not None:','if False:','test_native_wrapper_cli_exec_and_json_keep_argv_and_reject_ambiguous_inputs')
case('one default cwd only','if not explicit_cwd:','if True:','test_native_wrapper_does_not_duplicate_native_cwd_or_terminal_options')
case('one terminal flag only',"defaults = [] if explicit_no_alt_screen else ['--no-alt-screen']","defaults = ['--no-alt-screen']",'test_native_wrapper_does_not_duplicate_native_cwd_or_terminal_options')
case('native options retained','argv = [binary, *options]','argv = [binary]','test_native_wrapper_keeps_policy_model_and_literal_prompt_and_preflights_overrides')
case('literal prompt retained',"toml_value(instructions), *prompt]","toml_value(instructions)]",'test_native_wrapper_cli_exec_and_json_keep_argv_and_reject_ambiguous_inputs')
case('outer separator includes binary',"if native[0] != '--' or len(native) < 2:",'if False:','test_native_wrapper_cli_exec_and_json_keep_argv_and_reject_ambiguous_inputs',paths[1])
case('no two prompt sources',"if getattr(args, 'prompt', None) is not None:",'if False:','test_native_wrapper_cli_exec_and_json_keep_argv_and_reject_ambiguous_inputs',paths[1])
case('malformed claim shape unknown',"if not isinstance(owner, dict):",'if False:','test_readiness_without_binding_or_snapshot_stays_explicit',paths[1],"AttributeError: 'list' object has no attribute 'get'")
case('malformed snapshot shape unknown',"if not isinstance(recorded, dict):",'if False:','test_readiness_without_binding_or_snapshot_stays_explicit',paths[1],"TypeError: list indices must be integers or slices, not str")

# Profile refusal is deliberately redundant with the closed option vocabulary.
# This mutant models erroneously admitting profiles without effective preflight.
name='profiles require effective preflight'
replacements=[(paths[0],"if name in {'-p', '--profile'}:",'if False:'),
              (paths[0],"values = {'-c', '--config',","values = {'-p', '--profile', '-c', '--config',")]
cases.append((name,replacements,'test_native_wrapper_unsupported_or_incomplete_arguments_fail_before_launch',None))

case('null blocked evidence is not clear',
     "None if not isinstance(status.get('blocked_requests'), list) else not status['blocked_requests']",
     "None if 'blocked_requests' not in status else not status['blocked_requests']",
     'test_malformed_blocked_request_evidence_cannot_make_supervisor_ready')

def run(item):
    name,replacements,test,marker=item
    with tempfile.TemporaryDirectory(prefix='amap-wrapper-guard-') as temp:
        root=Path(temp)
        for tree in ['src','tests']:
            shutil.copytree(repo/tree,root/tree,ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copy(repo/'pyproject.toml',root/'pyproject.toml')
        mutated=dict(sources)
        for path,old,new in replacements:
            assert old in mutated[path],name
            mutated[path]=mutated[path].replace(old,new,1)
        for path,value in mutated.items():(root/path).write_text(value)
        test_path=os.pathsep.join([str(root/'src'),os.environ.get('PYTHONPATH','')])
        r=subprocess.run([python,'-m','pytest','-q','tests/test_interactive.py::'+test],cwd=root,
                         env={**os.environ,'PYTHONPATH':test_path,'PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1'},capture_output=True,text=True)
        assert r.returncode==1 and any(x in r.stdout for x in ['AssertionError','DID NOT RAISE']+([marker] if marker else [])), name+'\n'+r.stdout+r.stderr
        return name

if __name__ == '__main__':
    with ThreadPoolExecutor(max_workers=4) as pool:
        for name in pool.map(run,cases):print('Detected:',name,flush=True)
    assert all(value==(repo/path).read_text() for path,value in sources.items())
    print(f'{len(cases)} targeted mutations detected on scratch copies; working files unchanged.')
