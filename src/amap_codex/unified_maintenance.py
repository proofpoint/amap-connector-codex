"""Explicit stopped-service adoption and queue disposition, without model input."""
import hashlib
import json
from pathlib import Path

from .interactive import SESSION_ID
from .journal import Journal, IntegrityConflict, StateConflict, DEFAULT_MODEL
from .native_queue import QueueLedger, associated_pending, snapshot
from .unified import UnifiedService
from .unified_policy import resolve, validate_tools, workspace_matches


def history_digest(history):
    thread = history['thread']
    raw = json.dumps({'thread_id': thread['id'], 'turns': thread['turns']},
                     sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def existing_journal(config):
    path = config.state_dir / 'journal.sqlite3'
    if not path.is_file() or path.is_symlink():
        raise ValueError('maintenance requires an existing regular journal')
    return Journal(config.state_dir, config.instance_id, config.fingerprint(),
                   config.codex_version, config.codex_model, maintenance=True)


def note_required(note):
    if not isinstance(note, str) or not note.strip() or len(note) > 500:
        raise ValueError('maintenance requires an audit note of 1 to 500 characters')


def same_namespace(previous, target):
    if (target.conversation_mode != 'unified' or previous.launcher_control_argv is not None
            or previous.instance_id != target.instance_id or previous.self_address != target.self_address
            or previous.state_dir != target.state_dir or previous.lanes != target.lanes
            or previous.outcome_dir != target.outcome_dir
            or Path(previous.cwd).resolve() != Path(target.cwd).resolve()):
        raise ValueError('adoption requires the same in-environment instance, workspace and delivery namespace')


async def adopt(config, config_path, previous, thread_id, note, *, env=None):
    """Refresh protected policy and retain one explicitly selected saved history.

    Does not merge conversations, create a new thread, or submit model input.
    The operator stops all native clients before invoking this command.
    """
    note_required(note)
    same_namespace(previous, config)
    if not isinstance(thread_id, str) or not SESSION_ID.fullmatch(thread_id):
        raise ValueError('adoption requires an explicit canonical thread UUID')
    service = UnifiedService(config, config_path, env=env)
    service.ownership.acquire()
    try:
        journal = service.journal = existing_journal(previous)
        if (journal.recovery_events() or journal.operator_blocked()
                or journal.db.execute("SELECT 1 FROM outcome_transitions WHERE publication_state IN "
                                      "('publishing','ambiguous') LIMIT 1").fetchone()
                or (journal.db.execute("SELECT 1 FROM sqlite_master WHERE name='native_queue_deletions'").fetchone()
                    and journal.db.execute("SELECT 1 FROM native_queue_deletions WHERE state='planned'").fetchone())):
            raise StateConflict('resolve all in-flight work and publication uncertainty before adoption')
        client = await service.backend.start()
        settings, protected, digest = await resolve(client, config, config_path)
        before, pending = await snapshot(client, thread_id)
        if (pending or any(t['status'] == 'inProgress' for t in before['thread']['turns'])
                or not workspace_matches(before['thread'].get('cwd'), config.cwd)):
            raise StateConflict('adoption requires an idle saved history in this workspace with no native queued work')
        original = history_digest(before)
        journal.db.execute('''CREATE TABLE IF NOT EXISTS unified_adoptions (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), thread_id TEXT NOT NULL,
            previous_fingerprint TEXT NOT NULL, fingerprint TEXT NOT NULL,
            policy_hash TEXT NOT NULL, history_sha256 TEXT NOT NULL, state TEXT NOT NULL)''')
        planned = journal.db.execute('SELECT * FROM unified_adoptions WHERE singleton=1').fetchone()
        record = (1, thread_id, previous.fingerprint(), config.fingerprint(), digest, original, 'planned')
        if planned and planned['state'] == 'planned' and tuple(planned) != record:
            raise IntegrityConflict('unfinished adoption differs from the selected history or policy')
        if not planned or planned['state'] != 'planned':
            with journal._transaction():
                journal.db.execute('INSERT OR REPLACE INTO unified_adoptions VALUES(?,?,?,?,?,?,?)', record)
                journal._audit(None, 'unified_adoption_planned', json.dumps({'thread': thread_id,
                    'history_sha256': original, 'policy_hash': digest}, sort_keys=True))
                journal._audit(None, 'unified_adoption_note', note)
        result = await client.request('thread/resume', {**settings, 'threadId': thread_id, 'excludeTurns': True})
        sandbox = {'read-only': 'readOnly', 'workspace-write': 'workspaceWrite',
                   'danger-full-access': 'dangerFullAccess'}[config.sandbox]
        if (result.get('thread', {}).get('id') != thread_id or not isinstance(result.get('model'), str)
                or (config.codex_model is not None and result['model'] != config.codex_model)
                or result.get('approvalPolicy') != config.approval_policy
                or result.get('sandbox', {}).get('type') != sandbox
                or not workspace_matches(result.get('cwd'), config.cwd)):
            raise IntegrityConflict('adoption did not retain the selected thread and configured pane settings')
        await validate_tools(client, thread_id, protected)
        # Resuming a populated history retains its old developer context on
        # the measured build. Refresh it explicitly while idle, under the
        # durable adoption intent, using the same protected role as native
        # interactive adoption. This is context, never a model input/turn.
        await client.request('thread/inject_items', {'threadId': thread_id, 'items': [
            {'type': 'message', 'role': 'developer', 'content': [
                {'type': 'input_text', 'text': settings['developerInstructions']}]}]})
        after, pending = await snapshot(client, thread_id)
        if pending or history_digest(after) != original or after['thread']['status']['type'] != 'idle':
            raise IntegrityConflict('history changed during adoption; no new binding was committed')
        journal.db.execute('''CREATE TABLE IF NOT EXISTS unified_binding (
            instance_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, policy_hash TEXT NOT NULL,
            effective_model TEXT NOT NULL)''')
        with journal._transaction():
            old = journal.thread_id
            journal.db.execute('UPDATE instance SET fingerprint=?,thread_id=?,codex_version=?,codex_model=?,last_error=NULL',
                (config.fingerprint(), thread_id, config.codex_version, config.codex_model or DEFAULT_MODEL))
            journal.db.execute('INSERT OR REPLACE INTO unified_binding VALUES(?,?,?,?)',
                              (config.instance_id, thread_id, digest, result['model']))
            journal.db.execute("UPDATE unified_adoptions SET state='confirmed' WHERE singleton=1")
            journal._audit(None, 'unified_history_adopted', json.dumps({'previous_thread': old,
                'thread': thread_id, 'previous_fingerprint': previous.fingerprint(),
                'fingerprint': config.fingerprint(), 'history_sha256': original}, sort_keys=True))
        return {'thread_id': thread_id, 'previous_thread_id': old, 'history_sha256': original,
                'history_retained': True, 'model_input_submitted': False, 'audited': True}
    finally:
        await service.close()


async def dispose(config, config_path, event_id, action, note, *, cancel_queued=False, env=None):
    """Hold/handle explicitly, deleting an observed pending item before resume.

    Never calls thread/resume or queue/add. A durable delete intent survives a
    lost acknowledgement; a subsequent explicit invocation must re-observe both
    pending queue and unchanged original history before completing disposition.
    """
    note_required(note)
    if action not in {'hold', 'handled'} or type(cancel_queued) is not bool:
        raise ValueError('unified disposition supports only hold or handled; no retry')
    service = UnifiedService(config, config_path, env=env)
    service.ownership.acquire()
    try:
        journal = service.journal = existing_journal(config)
        if (journal.db.execute("SELECT 1 FROM sqlite_master WHERE name='unified_adoptions'").fetchone()
                and journal.db.execute("SELECT 1 FROM unified_adoptions WHERE state='planned'").fetchone()):
            raise StateConflict('complete the outstanding adoption before event disposition')
        event = journal.event(event_id)
        if event['state'] not in {'pending', 'queued', 'uncertain', 'refused', 'accepted'}:
            raise StateConflict('event is not eligible for explicit disposition')
        if not journal.thread_id:
            raise StateConflict('disposition requires a canonical thread')
        client = await service.backend.start()
        ledger = QueueLedger(journal)
        intent = ledger.latest(event_id)
        history, pending = await snapshot(client, journal.thread_id)
        journal.db.execute('''CREATE TABLE IF NOT EXISTS native_queue_deletions (
            event_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, queue_id TEXT NOT NULL,
            client_id TEXT NOT NULL, input_sha256 TEXT NOT NULL, history_sha256 TEXT NOT NULL,
            state TEXT NOT NULL)''')
        deletion = journal.db.execute('SELECT * FROM native_queue_deletions WHERE event_id=?', (event_id,)).fetchone()
        if deletion and intent is None:
            raise IntegrityConflict('recorded native deletion has no durable queue intent')
        queued = associated_pending(intent, pending, queue_id=deletion['queue_id'] if deletion else None) if intent else []
        if len(queued) > 1:
            raise IntegrityConflict('duplicate pending identities require individual native queue inspection')
        if queued:
            q = queued[0]
            if not cancel_queued:
                raise StateConflict('pending native input requires explicit --cancel-queued before disposition')
            if intent['thread_id'] != journal.thread_id:
                raise IntegrityConflict('native queue association differs from the recorded attempt')
            record = (event_id, journal.thread_id, q['id'], intent['client_id'],
                      hashlib.sha256(json.dumps(q['input'], sort_keys=True).encode()).hexdigest(),
                      history_digest(history), 'planned')
            if deletion and tuple(deletion) != record:
                raise IntegrityConflict('pending input or original history changed after delete intent')
            if not deletion:
                with journal._transaction():
                    journal.db.execute('INSERT INTO native_queue_deletions VALUES(?,?,?,?,?,?,?)', record)
                    journal._audit(event_id, 'native_queue_delete_planned', note)
            result = await client.request('thread/queue/delete', {
                'threadId': journal.thread_id, 'queuedSubmissionId': q['id']})
            if result != {'deleted': True}:
                raise IntegrityConflict('native queue deletion was not positively acknowledged')
            history, pending = await snapshot(client, journal.thread_id)
            if (any(item.get('id') == q['id'] or item.get('clientUserMessageId') == intent['client_id'] for item in pending)
                    or history_digest(history) != record[5]):
                raise IntegrityConflict('native queue deletion did not preserve original history and remove the input')
        elif deletion:
            if (not cancel_queued or deletion['thread_id'] != journal.thread_id
                    or intent is None or deletion['client_id'] != intent['client_id']
                    or history_digest(history) != deletion['history_sha256']):
                raise IntegrityConflict('unconfirmed deletion requires explicit cancellation and unchanged original history')
        with journal._transaction():
            if deletion or queued:
                journal.db.execute("UPDATE native_queue_deletions SET state='confirmed' WHERE event_id=?", (event_id,))
            if action == 'handled':
                journal._set(event_id, 'finished', 'operator marked handled')
                journal.db.execute("UPDATE events SET execution_status='operator_handled' WHERE event_id=?", (event_id,))
                journal._attempt_state(event_id, 'finished')
            else:
                journal._set(event_id, 'uncertain', 'operator hold: ' + note)
                journal._attempt_state(event_id, 'uncertain', 'operator hold')
                journal.db.execute('UPDATE native_queue_intents SET blocked=1 WHERE event_id=?', (event_id,))
                journal._outcome(event_id, 'held', 'operator hold: ' + note)
            journal._audit(event_id, 'unified_' + action, note)
        return {'event_id': event_id, 'action': action, 'native_input_cancelled': bool(deletion or queued),
                'model_input_submitted': False, 'audited': True}
    finally:
        await service.close()
