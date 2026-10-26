import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from amap_codex.app_server import NotSubmitted, Uncertain
from amap_codex.journal import Journal, IntegrityConflict, StateConflict
from amap_codex.native_queue import NativeQueue, QueueLedger, observe_intent, pages, pointer_input, preflight_pending


def journal_for(path, lane='peer'):
    j = Journal(path / 'journal.sqlite3', 'instance', 'fingerprint', 'codex-cli 0.164.0', None)
    j.bind_thread('canonical')
    j.admit(SimpleNamespace(state='pending', lane=lane, notice_id='notice1', artifact_hash='hash1',
        event_id='event1', payload={'event_id': 'event1', 'lane': lane, 'notice_id': 'notice1'}, detail=''))
    return j


class Backend:
    def __init__(self):
        self.sequence = self.adds = 0
        self.pending, self.turns = [], []
        self.failure = None
    def reserve_id(self):
        self.sequence += 1
        return self.sequence
    async def request(self, method, params, **kwargs):
        if method == 'thread/queue/add':
            if self.failure == 'unsent': raise NotSubmitted()
            self.adds += 1
            value = {'id': 'queued1', 'clientUserMessageId': params['clientUserMessageId'],
                     'input': copy.deepcopy(params['input'])}
            self.pending.append(value)
            if self.failure == 'uncertain': raise Uncertain()
            if self.failure == 'wrong_ack': value = {**value, 'clientUserMessageId': 'foreign'}
            return {'queuedSubmission': value}
        if method == 'thread/read': return {'thread': {'id': 'canonical', 'status': {'type': 'idle'}}}
        if method == 'thread/turns/list': return {'data': [{**t, 'items': []} for t in self.turns]}
        if method == 'thread/items/list':
            return {'data': [{'turnId': t['id'], 'item': i} for t in self.turns for i in t['items']]}
        if method == 'thread/queue/list': return {'data': copy.deepcopy(self.pending)}
        raise AssertionError(method)
    def complete(self, status='completed'):
        q = self.pending.pop(0)
        self.turns.append({'id': 'turn1', 'status': status, 'items': [
            {'type': 'userMessage', 'id': 'input1', 'clientId': q['clientUserMessageId'], 'content': q['input']}]})


def test_queue_admission_is_not_execution_and_survives_restart(tmp_path):
    async def check():
        backend = Backend()
        with journal_for(tmp_path) as journal:
            queue = NativeQueue(journal, backend, 'generation1')
            await queue.dispatch('event1')
            intent = queue.ledger.latest('event1')
            assert intent['queue_id'] == 'queued1' and intent['generation'] == 'generation1'
            assert journal.event('event1')['state'] == 'queued'
            assert journal.pending_outcomes() == []
            journal.admit(SimpleNamespace(state='pending', lane='peer', notice_id='notice2', artifact_hash='hash2',
                event_id='event2', payload={'event_id': 'event2', 'lane': 'peer', 'notice_id': 'notice2'}, detail=''))
            assert journal.next_pending() is None
            with pytest.raises(StateConflict): queue.ledger.begin('event2', 99, 'generation1')
            with pytest.raises(StateConflict): await queue.dispatch('event1')
        with journal_for(tmp_path) as journal:
            queue = NativeQueue(journal, backend, 'generation2')
            clean, _, _ = await queue.reconcile()
            assert clean and journal.event('event1')['state'] == 'queued'
            assert backend.adds == 1
            backend.complete()
            assert (await queue.reconcile())[0]
            event = journal.event('event1')
            assert (event['state'], event['turn_id'], event['execution_status']) == ('finished', 'turn1', 'completed')
            assert [r['outcome'] for r in journal.pending_outcomes()] == ['delivered']
            assert (await queue.reconcile())[0] and backend.adds == 1
    asyncio.run(check())


@pytest.mark.parametrize('failure', ['uncertain', 'wrong_ack'])
def test_lost_ack_is_reconciled_and_wrong_ack_is_sticky(tmp_path, failure):
    async def check():
        backend = Backend()
        backend.failure = failure
        with journal_for(tmp_path) as j:
            queue = NativeQueue(j, backend, 'generation1')
            if failure == 'wrong_ack':
                with pytest.raises(IntegrityConflict): await queue.dispatch('event1')
            else: await queue.dispatch('event1')
            assert j.event('event1')['state'] == 'uncertain' and backend.adds == 1
        with journal_for(tmp_path) as j:
            queue = NativeQueue(j, backend, 'generation2')
            assert (await queue.reconcile())[0] == (failure != 'wrong_ack')
            assert backend.adds == 1
            if failure == 'uncertain':
                backend.complete()
                await queue.reconcile()
                assert j.event('event1')['state'] == 'finished'
            else: assert j.event('event1')['state'] == 'uncertain'
    asyncio.run(check())


def test_missing_pending_never_means_retry_and_original_history_can_resolve_it(tmp_path):
    async def check():
        backend = Backend()
        with journal_for(tmp_path) as j:
            queue = NativeQueue(j, backend, 'generation1')
            await queue.dispatch('event1')
            original = backend.pending.pop()
            assert not (await queue.reconcile())[0]
            assert j.event('event1')['state'] == 'uncertain'
            assert j.next_pending() is None and backend.adds == 1
            with pytest.raises(StateConflict): await queue.dispatch('event1')
            backend.pending.append(original)
            backend.complete('interrupted')
            await queue.reconcile()
            assert j.event('event1')['execution_status'] == 'interrupted'
            assert backend.adds == 1
    asyncio.run(check())


def test_atomic_intent_failure_rolls_back_attempt_before_transport(tmp_path, monkeypatch):
    with journal_for(tmp_path) as j:
        ledger = QueueLedger(j)
        def fail(payload): raise ValueError('invalid local pointer')
        monkeypatch.setattr('amap_codex.native_queue.pointer_input', fail)
        with pytest.raises(ValueError): ledger.begin('event1', 1, 'generation1')
        assert j.event('event1')['state'] == 'pending'
        assert j.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0] == 0
        assert ledger.latest('event1') is None


def test_proven_unsent_allows_bounded_retry_but_uncertain_does_not(tmp_path):
    async def check():
        with journal_for(tmp_path) as j:
            backend = Backend()
            backend.failure = 'unsent'
            queue = NativeQueue(j, backend, 'generation1')
            await queue.dispatch('event1')
            first = queue.ledger.latest('event1')
            assert j.event('event1')['state'] == 'pending' and backend.adds == 0
            j.db.execute('UPDATE events SET eligible_at=0')
            backend.failure = None
            await queue.dispatch('event1')
            second = queue.ledger.latest('event1')
            assert first['client_id'] != second['client_id'] and second['number'] == 2
            assert backend.adds == 1
    asyncio.run(check())


@pytest.mark.parametrize('change', ['input', 'queue_id', 'client', 'intent', 'binding', 'state'])
def test_queue_ack_must_match_the_current_complete_durable_intent(tmp_path, change):
    with journal_for(tmp_path) as j:
        ledger = QueueLedger(j)
        intent = ledger.begin('event1', 1, 'generation1')
        ack = {'id': 'q1', 'clientUserMessageId': intent['client_id'], 'input': json.loads(intent['input_json'])}
        if change == 'input': ack['input'][0]['text'] += 'changed'
        elif change == 'queue_id': ack['id'] = ''
        elif change == 'client': ack['clientUserMessageId'] = 'foreign'
        elif change == 'intent': intent['generation'] = 'foreign'
        elif change == 'binding': j.db.execute("UPDATE instance SET thread_id='foreign'")
        else: j._set('event1', 'uncertain', 'synthetic conflict')
        with pytest.raises((IntegrityConflict, StateConflict)): ledger.queued(intent, ack)
        assert j.event('event1')['state'] != 'queued'


@pytest.mark.parametrize('bad', [None, 'duplicate', 'edited', 'finished', 'unretained', 'original_present'])
def test_persisted_pending_is_checked_before_any_resume(tmp_path, bad):
    async def check():
        with journal_for(tmp_path) as j:
            backend = Backend()
            queue = NativeQueue(j, backend, 'generation1')
            await queue.dispatch('event1')
            if bad == 'duplicate': backend.pending.append(copy.deepcopy(backend.pending[0]))
            elif bad == 'edited': backend.pending[0]['input'][0]['text'] += 'edited'
            elif bad == 'finished': j.handled('event1', 'operator disposition')
            elif bad == 'original_present':
                kept = copy.deepcopy(backend.pending[0])
                backend.complete()
                backend.pending.append(kept)
            if bad:
                with pytest.raises(IntegrityConflict):
                    await preflight_pending(backend, j, lambda e: bad != 'unretained')
            else:
                await preflight_pending(backend, j, lambda e: True)
            assert backend.adds == 1
    asyncio.run(check())


@pytest.mark.parametrize('change', ['client', 'client_edited_input', 'client_unretained', 'client_finished'])
def test_known_queue_identity_never_loses_amap_custody(tmp_path, change):
    async def check():
        with journal_for(tmp_path) as j:
            backend = Backend()
            await NativeQueue(j, backend, 'generation1').dispatch('event1')
            backend.pending[0]['clientUserMessageId'] = 'different-client'
            if change == 'client_edited_input': backend.pending[0]['input'][0]['text'] += ' edited'
            if change == 'client_finished': j.handled('event1', 'explicit prior disposition')
            with pytest.raises(IntegrityConflict):
                await preflight_pending(backend, j, lambda e: change != 'client_unretained')
            assert backend.adds == 1
    asyncio.run(check())


def test_unrelated_native_human_queue_retains_native_ownership(tmp_path):
    async def check():
        with journal_for(tmp_path) as j:
            backend = Backend()
            await NativeQueue(j, backend, 'generation1').dispatch('event1')
            backend.pending = [{'id': 'human-queue', 'clientUserMessageId': 'human-client',
                                'input': [{'type': 'text', 'text': 'human input'}]}]
            await preflight_pending(backend, j, lambda e: False)
            assert backend.adds == 1
    asyncio.run(check())


def test_online_reconciliation_holds_changed_client_on_known_queue_id(tmp_path):
    async def check():
        with journal_for(tmp_path) as j:
            backend = Backend()
            queue = NativeQueue(j, backend, 'generation1')
            await queue.dispatch('event1')
            backend.pending[0]['clientUserMessageId'] = 'different-client'
            assert not (await queue.reconcile())[0]
            assert queue.ledger.latest('event1')['blocked'] == 1
            assert j.event('event1')['state'] == 'uncertain'
    asyncio.run(check())


@pytest.mark.parametrize('bad', ['missing', 'duplicate'])
def test_human_queue_ids_must_also_be_explicit_and_unique(tmp_path, bad):
    async def check():
        with journal_for(tmp_path) as j:
            backend = Backend()
            backend.pending = [{'id': 'human-queue', 'clientUserMessageId': 'human-client', 'input': []}]
            if bad == 'missing': backend.pending[0].pop('id')
            else: backend.pending.append({**backend.pending[0], 'clientUserMessageId': 'another-client'})
            with pytest.raises(IntegrityConflict): await preflight_pending(backend, j, lambda e: True)
    asyncio.run(check())


def test_one_native_id_cannot_refer_to_two_durable_events(tmp_path):
    async def check():
        with journal_for(tmp_path) as j:
            backend = Backend()
            await NativeQueue(j, backend, 'generation1').dispatch('event1')
            j.admit(SimpleNamespace(state='pending', lane='peer', notice_id='notice2', artifact_hash='hash2',
                event_id='event2', payload={'event_id': 'event2', 'lane': 'peer', 'notice_id': 'notice2'}, detail=''))
            j.db.execute("INSERT INTO attempts(event_id,number,rpc_id,started_at,state) VALUES('event2',1,'old',0,'finished')")
            j.db.execute("INSERT INTO native_queue_intents(event_id,number,thread_id,generation,client_id,input_json,queue_id) "
                         "VALUES('event2',1,'canonical','old','old-client','[]','queued1')")
            j._set('event2', 'finished', 'earlier synthetic event')
            with pytest.raises(IntegrityConflict): await preflight_pending(backend, j, lambda e: True)
    asyncio.run(check())


@pytest.mark.parametrize('bad', ['duplicate', 'already_original'])
def test_native_human_pending_is_not_automatically_replayed_when_duplicated(tmp_path, bad):
    async def check():
        with journal_for(tmp_path) as j:
            backend = Backend()
            item = {'id': 'human-queued', 'clientUserMessageId': 'human-client',
                    'input': [{'type': 'text', 'text': 'native operator input', 'text_elements': []}]}
            backend.pending.append(item)
            if bad == 'duplicate': backend.pending.append(copy.deepcopy(item))
            else:
                backend.complete()
                backend.pending.append(item)
            with pytest.raises(IntegrityConflict): await preflight_pending(backend, j, lambda e: True)
            assert backend.adds == 0
    asyncio.run(check())


def observations():
    expected = pointer_input({'event_id': 'e', 'lane': 'peer', 'notice_id': 'n'})
    intent = {'thread_id': 'canonical', 'client_id': 'client1', 'input_json': json.dumps(expected), 'queue_id': 'q1'}
    history = {'thread': {'id': 'canonical', 'turns': [{'id': 'turn1', 'status': 'completed', 'items': [
        {'id': 'i1', 'type': 'userMessage', 'clientId': 'client1', 'content': copy.deepcopy(expected)}]}]}}
    pending = [{'id': 'q1', 'clientUserMessageId': 'client1', 'input': copy.deepcopy(expected)}]
    return intent, history, pending


@pytest.mark.parametrize('change', ['thread', 'content', 'status', 'duplicate_original', 'duplicate_pending',
                                  'both_pending_and_original', 'queue_id', 'queue_content'])
def test_conflicting_native_evidence_is_not_delivery(change):
    intent, history, pending = observations()
    turn = history['thread']['turns'][0]
    if change == 'thread': history['thread']['id'] = 'foreign'
    elif change == 'content': turn['items'][0]['content'][0]['text'] += 'edited'
    elif change == 'status': turn['status'] = 'unknown'
    elif change == 'duplicate_original': turn['items'].append(copy.deepcopy(turn['items'][0]))
    elif change == 'duplicate_pending': pending.append(copy.deepcopy(pending[0]))
    elif change == 'queue_id': pending[0]['id'] = 'foreign'
    elif change == 'queue_content': pending[0]['input'][0]['text'] += 'edited'
    use_pending = change in {'duplicate_pending', 'both_pending_and_original', 'queue_id', 'queue_content'}
    use_history = change not in {'duplicate_pending', 'queue_id', 'queue_content'}
    if not use_history: history['thread']['turns'] = []
    with pytest.raises(IntegrityConflict): observe_intent(intent, history, pending if use_pending else [])


@pytest.mark.parametrize('change', ['role', 'client'])
def test_echo_or_wrong_client_is_absence_not_proof_of_non_submission(change):
    intent, history, _ = observations()
    item = history['thread']['turns'][0]['items'][0]
    if change == 'role': item['type'] = 'agentMessage'
    else: item['clientId'] = 'foreign'
    assert observe_intent(intent, history, []) == ('unknown', None, None)


@pytest.mark.parametrize('mode', ['all_pages', 'repeated_cursor', 'invalid_cursor', 'bad_data', 'bound'])
def test_pagination_is_exhaustive_or_refuses_incomplete_evidence(mode):
    async def check():
        class Client:
            count = 0
            async def request(self, method, params):
                self.count += 1
                if mode == 'bad_data': return {'data': None}
                return {'data': [{'page': self.count}], 'nextCursor': (
                    None if mode == 'all_pages' and self.count == 3 else
                    7 if mode == 'invalid_cursor' else
                    'repeated' if mode == 'repeated_cursor' else str(self.count))}
        client = Client()
        if mode == 'all_pages':
            assert await pages(client, 'list', {}, max_pages=3) == [{'page': 1}, {'page': 2}, {'page': 3}]
        else:
            with pytest.raises(IntegrityConflict): await pages(client, 'list', {}, max_pages=3)
            assert client.count == {'repeated_cursor': 2, 'invalid_cursor': 1, 'bad_data': 1, 'bound': 3}[mode]
    asyncio.run(check())
