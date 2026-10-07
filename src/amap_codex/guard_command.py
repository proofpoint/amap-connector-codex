"""Host guard for another connector, using the same execution contract."""
import asyncio
import contextlib
import os
import signal

from .supervisor import Ownership, atomic_json
from .claims import _proc_start


async def run_guarded(config):
    if not config.launcher_control_argv:
        raise ValueError('guard-command requires controlled execution')
    ownership=Ownership(config)
    ownership.acquire()
    process=None
    recorded=False
    stopped=False
    stop=asyncio.Event()
    loop=asyncio.get_running_loop()
    for sig in (signal.SIGTERM,signal.SIGINT): loop.add_signal_handler(sig,stop.set)
    try:
        atomic_json(config.state_dir/'process.json', {'pid':os.getpid(),
            'proc_start':_proc_start(os.getpid()),'deployment_id':config.deployment_id,
            'owner_domain':config.owner_domain,'execution_id':ownership.execution_id})
        recorded=True
        process=await asyncio.create_subprocess_exec(*config.launch_argv,start_new_session=True,
            env={**os.environ,'AMAP_EXECUTION_ID':ownership.execution_id,
                 'AMAP_DEPLOYMENT_ID':config.deployment_id})
        atomic_json(config.state_dir/'status.json', {'instance_id':config.instance_id,
            'kind':'guarded-command','supervisor_pid':os.getpid(),
            'execution_id':ownership.execution_id,'claim_state':'held'})
        waiting=asyncio.create_task(process.wait()); stopping=asyncio.create_task(stop.wait())
        await asyncio.wait([waiting,stopping],return_when=asyncio.FIRST_COMPLETED)
        stopping.cancel()
        return process.returncode or 0
    finally:
        try:
            if process and process.returncode is None:
                with contextlib.suppress(ProcessLookupError): os.killpg(process.pid,signal.SIGTERM)
            if recorded:
                ownership.control.require_stopped(ownership.execution_id,stop=True)
                stopped=True
                (config.state_dir/'process.json').unlink()
        finally:
            if process and process.returncode is None:
                try: await asyncio.wait_for(process.wait(),2)
                except asyncio.TimeoutError:
                    with contextlib.suppress(ProcessLookupError): os.killpg(process.pid,signal.SIGKILL)
                    await process.wait()
            ownership.release(clear=stopped)
            for sig in (signal.SIGTERM,signal.SIGINT): loop.remove_signal_handler(sig)
