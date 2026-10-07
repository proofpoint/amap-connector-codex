import json
import sqlite3
import pytest
from amap_codex.journal import Journal, IntegrityConflict, StateConflict
from amap_codex.spool import Admission, event_id

def admission(identifier='receiver_notice_123',lane='peer',digest='a'*64,state='pending'):
    eid = event_id('instance-a',lane,identifier)
    return Admission(eid,lane,identifier,digest,{'event_id':eid,'lane':lane,'notice_id':identifier},state,'validation failed' if state=='refused' else '')

def journal(tmp_path,clock=None):
    kwargs = {'clock':clock} if clock else {}
    return Journal(tmp_path/'journal.db','instance-a','config-fingerprint','codex-cli 0.160.1',**kwargs)

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
    with pytest.raises(IntegrityConflict): Journal(tmp_path/'journal.db','another-instance','config-fingerprint','codex-cli 0.160.1')
    with pytest.raises(IntegrityConflict): Journal(tmp_path/'journal.db','instance-a','changed-settings','codex-cli 0.160.1')

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
