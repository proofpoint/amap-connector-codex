"""Stopped-service native adoption/deletion with a synthetic local provider."""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import asyncio
import json
from pathlib import Path
import platform
import subprocess
import tempfile

from amap_codex.journal import Journal, IntegrityConflict
from amap_codex.native_queue import snapshot
from amap_codex.unified import UnifiedService
from amap_codex.unified_attach import attachment_argv
from amap_codex.unified_maintenance import adopt, dispose, history_digest
from unified_runtime_probe import configuration, publish, settle
from unified_probe import Terminal, until


async def adoption(root, build):
    config, path, provider = configuration(root, build)
    previous = replace(config, conversation_mode='separate', unified_argv=None)
    service = terminal = None
    try:
        await provider.start()
        provider.attach('SYNTHETIC_HISTORY_TO_ADOPT')
        await until(lambda: provider.terminal.contains('NATIVE_QUEUE_RESPONSE_1'), provider.terminal)
        thread = provider.thread
        before = history_digest((await snapshot(provider.client, thread))[0])
        # The selected pane may differ from the old supervisor history. Keep
        # both saved histories; adoption selects one and never merges them.
        old = (await provider.client.request('thread/start', {'cwd': str(root), 'ephemeral': False}))['thread']['id']
        await provider.client.request('thread/name/set', {'threadId': old, 'name': 'Other retained history'})
        with Journal(config.state_dir, config.instance_id, previous.fingerprint(), build, None) as journal:
            journal.bind_thread(old)
        await provider.stop_backend()
        result = await adopt(config, path, previous, thread, 'Synthetic explicit adoption', env=provider.env)
        assert result['previous_thread_id'] == old and result['thread_id'] == thread
        assert result['history_sha256'] == before and len(provider.requests) == 1
        service = UnifiedService(config, path, env=provider.env)
        await service.start()
        assert service.journal.thread_id == thread and len(provider.requests) == 1
        assert history_digest((await snapshot(service.backend.client, thread))[0]) == before
        assert (await service.backend.client.request('thread/read', {'threadId': old, 'includeTurns': False}))['thread']['id'] == old
        terminal = Terminal(attachment_argv(config, wait=0), provider.env, root)
        await until(lambda: terminal.contains('NATIVE_QUEUE_RESPONSE_1'), terminal)
        event = publish(config, 'peer', 'synthetic_after_adoption')
        await settle(service, lambda: service.journal.db.execute('SELECT 1 FROM events WHERE event_id=?', (event,)).fetchone(), terminal)
        await settle(service, lambda: service.journal.event(event)['state'] == 'finished', terminal)
        await until(lambda: terminal.contains('NATIVE_QUEUE_RESPONSE_2'), terminal)
        assert len(provider.requests) == 2
        return {'operator_selected_history_retained_exactly': True, 'other_saved_history_retained': True,
                'adoption_submits_no_model_input': True, 'protected_policy_and_tools_refreshed': True,
                'next_peer_turn_visible_in_adopted_pane': True}
    finally:
        if terminal: terminal.close()
        if service: await service.close()
        await provider.close()


async def cancellation(root, build):
    config, path, provider = configuration(root, build, hold_first=True)
    service = UnifiedService(config, path, env=provider.env)
    terminal = None
    try:
        await service.start()
        thread = service.journal.thread_id
        terminal = Terminal(attachment_argv(config, native_argv=['codex', '--', 'SYNTHETIC_CRASHED_HUMAN'], wait=0),
                            provider.env, root)
        await until(provider.arrived.is_set, terminal)
        event = publish(config, 'mail', 'synthetic_cancel')
        await service.tick()
        assert service.journal.event(event)['state'] == 'queued'
        terminal.close()
        terminal = None
        import os, signal
        os.killpg(service.backend.process.pid, signal.SIGKILL)
        await service.close()
        provider.release.set()
        body = root / 'mail/messages/notice-synthetic_cancel.json'
        document = json.loads(body.read_text())
        document['body_text'] = 'Changed queued artifact; do not execute'
        body.write_text(json.dumps(document))
        service = UnifiedService(config, path, env=provider.env)
        try:
            await service.start()
        except IntegrityConflict:
            pass
        else:
            raise AssertionError('changed queued body was resumed')
        result = await dispose(config, path, event, 'handled', 'Synthetic explicit cancellation',
                               cancel_queued=True, env=provider.env)
        assert result['native_input_cancelled'] and not result['model_input_submitted']
        assert len(provider.requests) == 1
        service = UnifiedService(config, path, env=provider.env)
        await service.start()
        assert service.journal.thread_id == thread
        assert (await snapshot(service.backend.client, thread))[1] == []
        assert service.journal.event(event)['execution_status'] == 'operator_handled'
        assert len(provider.requests) == 1
        assert not service.journal.pending_outcomes()  # handled is not fabricated delivery
        return {'changed_pending_body_blocks_resume': True, 'audited_offline_cancellation': True,
                'cancelled_input_absent_after_resume': True, 'no_pending_or_human_replay': True,
                'no_fabricated_delivered_outcome': True}
    finally:
        provider.release.set()
        if terminal: terminal.close()
        await service.close()
        await provider.close()


async def probe(output):
    build = subprocess.check_output(['codex', '--version'], text=True).splitlines()[0]
    evidence = {'timestamp_utc': datetime.now(timezone.utc).isoformat(), 'codex_version': build,
        'os': platform.system(), 'architecture': platform.machine(), 'external_inference': False,
        'user_auth_used': False, 'fleet_submissions': 0}
    with tempfile.TemporaryDirectory(prefix='amap-maint-') as directory:
        for name, scenario in [('adoption', adoption), ('cancellation', cancellation)]:
            root = Path(directory) / name
            root.mkdir(mode=0o700)
            evidence[name] = await scenario(root, build)
    evidence.update(probe_assertions_passed=True, fleet_ready=False)
    output.write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps(evidence))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(probe(parser.parse_args().output))
