import json
import sqlite3
import pytest
from amap_codex.journal import DEFAULT_MODEL, Journal, IntegrityConflict, StateConflict
from amap_codex.spool import Admission, event_id

def admission(identifier='receiver_notice_123',lane='peer',digest='a'*64,state='pending'):
    eid = event_id('instance-a',lane,identifier)
    return Admission(eid,lane,identifier,digest,{'event_id':eid,'lane':lane,'notice_id':identifier},state,'validation failed' if state=='refused' else '')

def journal(tmp_path,clock=None):
    kwargs = {'clock':clock} if clock else {}
    return Journal(tmp_path/'journal.db','instance-a','config-fingerprint','codex-cli 0.160.1','gpt-test',**kwargs)


def test_unused_thread_release_checks_binding_and_audits_atomically(tmp_path):
    with journal(tmp_path) as db:
        db.bind_thread('old-thread')
        for invalid in ('different-thread', '', None):
            with pytest.raises(IntegrityConflict, match='bound thread'):
                db.release_unused_thread(invalid)
            assert db.thread_id == 'old-thread'
        assert db.db.execute('SELECT COUNT(*) FROM audit').fetchone()[0] == 0
        db.release_unused_thread('old-thread')
        assert db.thread_id is None
        assert db.db.execute('SELECT action FROM audit').fetchone()[0] == 'unused_thread_released'
        db.bind_thread('replacement')
    with journal(tmp_path) as db:
        assert db.thread_id == 'replacement'


def test_unused_thread_release_rolls_back_binding_when_audit_fails(tmp_path, monkeypatch):
    with journal(tmp_path) as db:
        db.bind_thread('old-thread')
        def fail_audit(*args):
            raise OSError('synthetic audit failure')
        monkeypatch.setattr(db, '_audit', fail_audit)
        with pytest.raises(OSError, match='audit failure'):
            db.release_unused_thread('old-thread')
        assert db.thread_id == 'old-thread'


@pytest.mark.parametrize('table', ['events', 'attempts', 'operator_runs'])
def test_unused_thread_release_refuses_every_ledger_even_orphan_attempt(tmp_path, table):
    with journal(tmp_path) as db:
        db.bind_thread('old-thread')
        if table == 'events':
            db.admit(admission())
        elif table == 'operator_runs':
            db.admit_operator('operator', 'a' * 64)
        else:
            # Corrupt/legacy orphan metadata must not look like an empty ledger.
            db.db.execute('PRAGMA foreign_keys=OFF')
            db.db.execute("INSERT INTO attempts(event_id,number,rpc_id,started_at,state) VALUES('orphan',1,'42',0,'unsent')")
            db.db.execute('PRAGMA foreign_keys=ON')
        with pytest.raises(StateConflict, match='recorded work'):
            db.release_unused_thread('old-thread')
        assert db.thread_id == 'old-thread'
        assert db.db.execute('SELECT COUNT(*) FROM audit').fetchone()[0] == 0
        assert db.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 1

def test_durable_acceptance_no_replay(tmp_path):
    db = journal(tmp_path)
    first,second = admission(),admission('receiver_notice_999')
    assert db.admit(first)
    assert not db.admit(first)
    db.admit(second)
    db.bind_thread('thread-a')
    db.begin_attempt(first.event_id,41)
    assert db.next_pending() is None
    db.accept(first.event_id,'turn-a')
    db.finish(first.event_id,'failed')
    db.close()
    db = journal(tmp_path)
    assert db.event(first.event_id)['state']=='finished'
    assert db.next_pending()['event_id']==second.event_id
    assert [r['outcome'] for r in db.pending_outcomes()] == ['delivered']
    assert db.db.execute('PRAGMA synchronous').fetchone()[0] == 2
    db.close()

def test_crash_dispatch_uncertain_and_operator_actions(tmp_path):
    db = journal(tmp_path)
    first = admission()
    db.admit(first); db.bind_thread('thread-a'); db.begin_attempt(first.event_id,41)
    db.close()
    db = journal(tmp_path)
    assert db.event(first.event_id)['state']=='uncertain'
    assert db.next_pending() is None
    with pytest.raises(ValueError): db.retry(first.event_id,{'allow_risk':True})
    db.hold(first.event_id,'History unavailable; retain hold')
    db.retry(first.event_id,{'kind':'confirmed_not_submitted','reference':'app-server transport audit attests request not submitted'})
    assert db.next_pending()['event_id']==first.event_id
    assert db.db.execute('SELECT COUNT(*) FROM audit').fetchone()[0] == 2
    db.close()

def test_history_positive_acceptance_and_handled(tmp_path):
    db = journal(tmp_path)
    first = admission()
    db.admit(first); db.bind_thread('thread-a'); db.begin_attempt(first.event_id,41)
    db.uncertain(first.event_id,'connection lost')
    db.accept(first.event_id,'history-turn-id')
    with pytest.raises(StateConflict): db.retry(first.event_id,{'kind':'transport_not_written','reference':'unsupported assertion'})
    assert db.event(first.event_id)['state']=='accepted'
    assert db.recovery_events()[0]['turn_id']=='history-turn-id'
    db.finish(first.event_id,'completed')
    second=admission('other-notice'); db.admit(second)
    with pytest.raises(ValueError): db.handled(second.event_id,'')
    db.handled(second.event_id,'Operator completed task outside controller')
    assert db.event(second.event_id)['execution_status']=='operator_handled'
    db.close()

def test_integrity_and_binding_conflicts(tmp_path):
    db = journal(tmp_path)
    db.admit(admission())
    with pytest.raises(IntegrityConflict): db.admit(admission(digest='b'*64))
    assert db.status()['last_error']
    assert db.event(admission().event_id)['state']=='uncertain'
    assert db.next_pending() is None
    db.bind_thread('thread-a')
    with pytest.raises(IntegrityConflict): db.bind_thread('thread-b')
    db.close()
    with pytest.raises(IntegrityConflict): Journal(tmp_path/'journal.db','another-instance','config-fingerprint','codex-cli 0.160.1','gpt-test')
    with pytest.raises(IntegrityConflict): Journal(tmp_path/'journal.db','instance-a','changed-settings','codex-cli 0.160.1','gpt-test')


@pytest.mark.parametrize('publication_state', ['publishing', 'ambiguous'])
def test_clean_thread_recovery_cannot_clear_unresolved_publication_error(tmp_path, publication_state):
    with journal(tmp_path) as db:
        incoming = admission()
        db.admit(incoming)
        db.bind_thread('thread-a')
        db.begin_attempt(incoming.event_id, 41)
        db.accept(incoming.event_id, 'turn-a')
        db.finish(incoming.event_id, 'completed')
        transition = db.pending_outcomes()[0]
        db.publication_state(transition['id'], publication_state)
        db.operational_error('outcome publication uncertain; router evidence required')
        db.clear_operational_error()
        assert db.status()['last_error'] == 'outcome publication uncertain; router evidence required'
        assert db.pending_outcomes(include_ambiguous=True)[0]['publication_state'] == publication_state
        # Positive publication evidence resolves the independent uncertainty.
        db.publication_state(transition['id'], 'published')
        db.clear_operational_error()
        assert db.status()['last_error'] is None
        notes = [tuple(r) for r in db.db.execute("SELECT action,note FROM audit")]
        assert notes[-1] == ('operational_error_cleared', 'outcome publication uncertain; router evidence required')


def test_a_codex_build_change_is_audited_and_keeps_the_instance(tmp_path):
    db = journal(tmp_path); db.admit(admission()); db.bind_thread('thread-a'); db.close()
    db = Journal(tmp_path/'journal.db','instance-a','config-fingerprint','codex-cli 0.161.0','gpt-test')
    try:
        assert db.thread_id == 'thread-a'
        assert db.event(admission().event_id)['state'] == 'pending'
        assert db.db.execute("SELECT codex_version FROM instance").fetchone()[0] == 'codex-cli 0.161.0'
        notes = [tuple(r) for r in db.db.execute("SELECT action,note FROM audit")]
        assert notes == [('codex_version_changed', 'codex-cli 0.160.1 -> codex-cli 0.161.0')]
    finally:
        db.close()

def test_a_model_change_is_audited_and_keeps_the_instance(tmp_path):
    db = journal(tmp_path); db.admit(admission()); db.bind_thread('thread-a'); db.close()
    db = Journal(tmp_path/'journal.db','instance-a','config-fingerprint','codex-cli 0.160.1','gpt-other')
    try:
        assert db.thread_id == 'thread-a'
        assert db.event(admission().event_id)['state'] == 'pending'
        assert db.db.execute("SELECT codex_model FROM instance").fetchone()[0] == 'gpt-other'
        notes = [tuple(r) for r in db.db.execute("SELECT action,note FROM audit")]
        assert notes == [('codex_model_changed', 'gpt-test -> gpt-other')]
    finally:
        db.close()

def test_no_model_is_recorded_as_codexs_default_and_a_move_to_it_is_audited(tmp_path):
    db = journal(tmp_path); db.close()
    db = Journal(tmp_path/'journal.db','instance-a','config-fingerprint','codex-cli 0.160.1',None)
    try:
        assert db.db.execute("SELECT codex_model FROM instance").fetchone()[0] == DEFAULT_MODEL
        notes = [tuple(r) for r in db.db.execute("SELECT action,note FROM audit")]
        assert notes == [('codex_model_changed', f'gpt-test -> {DEFAULT_MODEL}')]
    finally:
        db.close()

def test_proven_unsent_bounded_backoff(tmp_path):
    now = [100.0]
    db = journal(tmp_path,clock=lambda:now[0])
    first = admission(); db.admit(first); db.bind_thread('thread-a')
    for number in range(1,4):
        db.begin_attempt(first.event_id,number)
        db.unsent(first.event_id,'write never called')
        assert db.next_pending() is None
        now[0] += 2**(number-1)
    assert db.event(first.event_id)['state']=='uncertain'
    assert db.status()['uncertain_count']==1
    db.close()

def test_no_body_storage_and_no_mail_outcomes(tmp_path):
    db=journal(tmp_path)
    record=admission(lane='mail',state='refused')
    db.admit(record)
    assert db.pending_outcomes()==[]
    bad=admission('unsafe-payload')
    bad.payload['body_text']='SECRET'
    with pytest.raises(ValueError): db.admit(bad)
    db.close()
    assert b'SECRET' not in (tmp_path/'journal.db').read_bytes()
