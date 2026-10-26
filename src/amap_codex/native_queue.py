"""Durable AMAP queue intents and conservative original-input reconciliation.

Codex owns scheduling. This layer never infers non-submission from absence,
replays an uncertain add, turns a queue receipt into delivered, or dispatches
human input. The caller owns the normal instance lock and consumer claims.
"""
import json
import uuid

from .app_server import NotSubmitted, RPCError, Uncertain
from .journal import IntegrityConflict, StateConflict

PREFIX = 'AMAP notice pointer. Follow the standing workflow: '
TERMINAL = {'completed', 'failed', 'interrupted'}


def pointer_input(payload):
    return [{'type': 'text', 'text': PREFIX + json.dumps(payload, sort_keys=True, separators=(',', ':')),
             'text_elements': []}]


class QueueLedger:
    def __init__(self, journal):
        self.journal = journal
        journal.db.execute('''CREATE TABLE IF NOT EXISTS native_queue_intents (
            event_id TEXT NOT NULL, number INTEGER NOT NULL,
            thread_id TEXT NOT NULL, generation TEXT NOT NULL,
            client_id TEXT NOT NULL UNIQUE, input_json TEXT NOT NULL,
            queue_id TEXT, blocked INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(event_id,number),
            FOREIGN KEY(event_id,number) REFERENCES attempts(event_id,number))''')

    def latest(self, event_id):
        row = self.journal.db.execute('SELECT * FROM native_queue_intents WHERE event_id=? '
                                     'ORDER BY number DESC LIMIT 1', (event_id,)).fetchone()
        return None if row is None else dict(row)

    def begin(self, event_id, rpc_id, generation):
        if not isinstance(generation, str) or not generation:
            raise ValueError('owned backend generation is required')
        j = self.journal
        with j._transaction():
            number = j._begin_attempt(event_id, rpc_id)
            payload = j.event(event_id)['payload']
            client_id = str(uuid.uuid4())
            j.db.execute('INSERT INTO native_queue_intents '
                         '(event_id,number,thread_id,generation,client_id,input_json) VALUES(?,?,?,?,?,?)',
                         (event_id, number, j.thread_id, generation, client_id, json.dumps(pointer_input(payload))))
        return self.latest(event_id)

    def queued(self, intent, submission):
        j = self.journal
        if (not isinstance(submission, dict) or not isinstance(submission.get('id'), str)
                or not 0 < len(submission['id']) <= 200
                or submission.get('clientUserMessageId') != intent['client_id']
                or submission.get('input') != json.loads(intent['input_json'])):
            raise IntegrityConflict('native queue acknowledgement does not match durable intent')
        with j._transaction():
            current = self.latest(intent['event_id'])
            if current != intent or j.thread_id != intent['thread_id']:
                raise IntegrityConflict('queue intent or canonical binding changed')
            if j.event(intent['event_id'])['state'] != 'dispatching':
                raise StateConflict('queue admission requires current dispatch')
            j.db.execute('UPDATE native_queue_intents SET queue_id=? WHERE event_id=? AND number=?',
                         (submission['id'], intent['event_id'], intent['number']))
            j._set(intent['event_id'], 'queued', 'native queue admitted pointer; original turn input not yet observed')
            j._attempt_state(intent['event_id'], 'queued')

    def hold(self, event_id, detail, *, conflict=False):
        j = self.journal
        with j._transaction():
            row = j.event(event_id)
            if row['state'] == 'finished':
                return
            j._set(event_id, 'uncertain', detail)
            j._attempt_state(event_id, 'uncertain', detail)
            j._outcome(event_id, 'held', detail)
            if conflict:
                j.db.execute('UPDATE native_queue_intents SET blocked=1 WHERE event_id=?', (event_id,))
                j._audit(event_id, 'native_queue_conflict', detail)


async def pages(client, method, params, *, max_pages=100):
    result, seen = [], set()
    cursor = None
    total_bytes = 0
    for _ in range(max_pages):
        query = {**params, 'limit': 100}
        if cursor is not None:
            query['cursor'] = cursor
        page = await client.request(method, query)
        if not isinstance(page, dict) or not isinstance(page.get('data'), list):
            raise IntegrityConflict('native observation page is malformed')
        total_bytes += len(json.dumps(page, ensure_ascii=True))
        if total_bytes > 64 * 1024 * 1024 or len(result) + len(page['data']) > max_pages * 100:
            raise IntegrityConflict('native observation exceeded aggregate bound')
        result.extend(page['data'])
        cursor = page.get('nextCursor')
        if cursor is None:
            return result
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise IntegrityConflict('native observation cursor is invalid or repeated')
        seen.add(cursor)
    raise IntegrityConflict('native observation exceeded page bound; no absence conclusion is possible')


async def snapshot(client, thread_id):
    metadata = (await client.request('thread/read', {'threadId': thread_id, 'includeTurns': False})).get('thread')
    if not isinstance(metadata, dict) or metadata.get('id') != thread_id:
        raise IntegrityConflict('observation names another canonical thread')
    turns = await pages(client, 'thread/turns/list', {
        'threadId': thread_id, 'itemsView': 'notLoaded', 'sortDirection': 'asc'})
    by_id = {}
    for turn in turns:
        if (not isinstance(turn, dict) or not isinstance(turn.get('id'), str) or not turn['id']
                or turn['id'] in by_id or turn.get('status') not in TERMINAL | {'inProgress'}):
            raise IntegrityConflict('native turn observation is incomplete or ambiguous')
        by_id[turn['id']] = {**turn, 'items': []}
    # Full item pagination is independent of the turn-summary page size.
    entries = await pages(client, 'thread/items/list', {'threadId': thread_id, 'sortDirection': 'asc'})
    item_ids = set()
    for entry in entries:
        if not isinstance(entry, dict) or entry.get('turnId') not in by_id or not isinstance(entry.get('item'), dict):
            raise IntegrityConflict('history changed during observation; reconcile again before dispatch')
        item = entry['item']
        if not isinstance(item.get('id'), str) or not item['id'] or item['id'] in item_ids:
            raise IntegrityConflict('native item identity is incomplete or duplicated')
        item_ids.add(item['id'])
        by_id[entry['turnId']]['items'].append(item)
    pending = await pages(client, 'thread/queue/list', {'threadId': thread_id})
    return {'thread': {**metadata, 'turns': list(by_id.values())}}, pending


async def preflight_pending(client, journal, retained):
    """Inspect persisted queued work BEFORE resume can automatically execute it."""
    pending = await pages(client, 'thread/queue/list', {'threadId': journal.thread_id})
    if not pending:
        return
    ledger = QueueLedger(journal)
    history, pending = await snapshot(client, journal.thread_id)
    seen, queue_ids = set(), set()
    originals = {item.get('clientId') for turn in history['thread']['turns'] for item in turn['items']
                 if item.get('type') == 'userMessage' and item.get('clientId')}
    for item in pending:
        identifier = item.get('clientUserMessageId') if isinstance(item, dict) else None
        queue_id = item.get('id') if isinstance(item, dict) else None
        if not isinstance(identifier, str) or not identifier or identifier in seen or identifier in originals:
            raise IntegrityConflict('persisted native queue is duplicated or already represented in original history')
        if not isinstance(queue_id, str) or not queue_id or queue_id in queue_ids:
            raise IntegrityConflict('persisted native queue identity is missing or duplicated')
        seen.add(identifier)
        queue_ids.add(queue_id)
        matches = journal.db.execute('SELECT * FROM native_queue_intents WHERE client_id=? OR queue_id=?',
                                     (identifier, queue_id)).fetchall()
        if not matches:
            continue  # Native human input retains native ownership and scheduling.
        if len(matches) != 1:
            raise IntegrityConflict('persisted queue identity matches conflicting durable attempts')
        intent = dict(matches[0])
        associated_pending(intent, [item])  # Either identity retains AMAP custody.
        event = journal.event(intent['event_id'])
        attempt = journal.db.execute('SELECT state FROM attempts WHERE event_id=? AND number=?',
                                     (intent['event_id'], intent['number'])).fetchone()
        if (intent['blocked'] or event['state'] in {'accepted', 'finished'} or attempt['state'] == 'unsent'
                or event['detail'].startswith('publication integrity conflict')
                or intent != ledger.latest(intent['event_id']) or not retained(event)
                or observe_intent(intent, history, pending)[0] != 'queued'):
            raise IntegrityConflict('persisted AMAP pointer requires explicit queue disposition before resume')


def associated_pending(intent, pending, *, queue_id=None):
    """A known queue ID cannot become human input by changing its client ID."""
    known = {v for v in (intent['queue_id'], queue_id) if v is not None}
    if len(known) > 1:
        raise IntegrityConflict('recorded queue and deletion identities disagree')
    if any(not isinstance(q, dict) or not isinstance(q.get('id'), str) or not q['id']
           or not isinstance(q.get('clientUserMessageId'), str) or not q['clientUserMessageId'] for q in pending):
        raise IntegrityConflict('native pending identity is missing or malformed')
    queued = [q for q in pending if isinstance(q, dict) and (
        q.get('clientUserMessageId') == intent['client_id'] or q.get('id') in known)]
    for item in queued:
        if (item.get('clientUserMessageId') != intent['client_id']
                or (known and item.get('id') not in known)):
            raise IntegrityConflict('known native queue identity changed its durable client association')
    return queued


def observe_intent(intent, history, pending):
    if history.get('thread', {}).get('id') != intent['thread_id']:
        raise IntegrityConflict('history belongs to another canonical thread')
    expected = json.loads(intent['input_json'])
    queued = associated_pending(intent, pending)
    original = [(turn, item) for turn in history['thread']['turns'] for item in turn.get('items', [])
                if item.get('type') == 'userMessage' and item.get('clientId') == intent['client_id']]
    if len(queued) + len(original) > 1:
        raise IntegrityConflict('multiple native submissions use one durable client identity')
    if queued:
        item = queued[0]
        if item.get('input') != expected:
            raise IntegrityConflict('queued pointer content or association changed')
        return 'queued', item['id'], None
    if original:
        turn, item = original[0]
        if (item.get('content') != expected or not isinstance(turn.get('id'), str) or not turn['id']
                or turn.get('status') not in TERMINAL | {'inProgress'}):
            raise IntegrityConflict('original native input or execution status differs from durable intent')
        return 'accepted', turn['id'], turn['status']
    return 'unknown', None, None


class NativeQueue:
    def __init__(self, journal, client, generation):
        if not journal.thread_id:
            raise StateConflict('bind the canonical thread before native queue delivery')
        self.journal, self.client, self.generation = journal, client, generation
        self.ledger = QueueLedger(journal)

    async def dispatch(self, event_id):
        rpc_id = self.client.reserve_id()
        intent = self.ledger.begin(event_id, rpc_id, self.generation)
        try:
            result = await self.client.request('thread/queue/add', {
                'threadId': intent['thread_id'], 'clientUserMessageId': intent['client_id'],
                'input': json.loads(intent['input_json'])}, rpc_id=rpc_id)
            self.ledger.queued(intent, result.get('queuedSubmission'))
        except NotSubmitted:
            self.journal.unsent(event_id, 'native queue transport positively rejected before write')
        except (Uncertain, RPCError):
            self.ledger.hold(event_id, 'native queue admission unconfirmed; inspect original input before any retry')
        except IntegrityConflict:
            self.ledger.hold(event_id, 'native queue acknowledgement conflicts with durable intent', conflict=True)
            raise
        except BaseException:
            self.ledger.hold(event_id, 'native queue dispatch interrupted; never automatically repeat the add')
            raise

    async def reconcile(self):
        history, pending = await snapshot(self.client, self.journal.thread_id)
        clean = True
        for event in self.journal.recovery_events():
            intent = self.ledger.latest(event['event_id'])
            if intent is None or intent['blocked'] or event['detail'].startswith('publication integrity conflict'):
                clean = False
                continue
            try:
                state, association, status = observe_intent(intent, history, pending)
                if state == 'accepted':
                    if event['turn_id'] is not None and event['turn_id'] != association:
                        raise IntegrityConflict('native execution association changed')
                    self.journal.accept(event['event_id'], association)
                    if status in TERMINAL:
                        self.journal.finish(event['event_id'], status)
                    elif history['thread'].get('status', {}).get('type') != 'active':
                        self.journal.operational_error('accepted execution is not active or terminal; explicit resolution may be required')
                        clean = False
                elif state == 'queued':
                    with self.journal._transaction():
                        if event['state'] == 'accepted':
                            raise IntegrityConflict('accepted input cannot become pending again')
                        self.journal.db.execute('UPDATE native_queue_intents SET queue_id=? WHERE event_id=? AND number=?',
                                               (association, event['event_id'], intent['number']))
                        self.journal._set(event['event_id'], 'queued', 'native pending pointer observed')
                        self.journal._attempt_state(event['event_id'], 'queued')
                else:
                    self.ledger.hold(event['event_id'], 'native original input not yet established; no automatic resubmission')
                    clean = False
            except IntegrityConflict:
                self.ledger.hold(event['event_id'], 'native queue/history conflicts with durable intent', conflict=True)
                clean = False
        return clean, history, pending
