import json
import pytest
from amap_codex.journal import Journal
from amap_codex.outcomes import OutcomePublisher, OutcomeUncertain
from amap_codex.spool import Admission, event_id

def setup(tmp_path):
    db = Journal(tmp_path/'journal.db','instance-a','fingerprint','codex-cli 0.160.1','gpt-test')
    identifier = 'receiver_notice_123'
    eid = event_id('instance-a','peer',identifier)
    db.admit(Admission(eid,'peer',identifier,'a'*64,{'event_id':eid,'lane':'peer','notice_id':identifier}))
    db.bind_thread('thread-a'); db.begin_attempt(eid,41)
    return db,eid,tmp_path/'outcomes'

def test_atomic_committed_shape_and_no_absence_replay(tmp_path):
    db,eid,directory=setup(tmp_path)
    db.accept(eid,'turn-a')
    publisher=OutcomePublisher(db,directory)
    assert publisher.publish_pending()==1
    path=directory/'peer-receiver_notice_123.json'
    doc=json.loads(path.read_text())
    assert set(doc)=={'outcome','ts','tree','notice_id','detail'}
    assert doc['outcome']=='delivered' and 'acceptance' in doc['detail']
    assert path.stat().st_nlink==1 and path.stat().st_mode & 0o777 == 0o644
    path.unlink()
    assert publisher.publish_pending()==0
    db.close()
    db=Journal(tmp_path/'journal.db','instance-a','fingerprint','codex-cli 0.160.1','gpt-test')
    assert OutcomePublisher(db,directory).publish_pending()==0
    assert not path.exists()
    db.close()

def test_interrupt_publication_conservative_and_agreed_replay(tmp_path,monkeypatch):
    db,eid,directory=setup(tmp_path)
    db.uncertain(eid,'timeout')
    publisher=OutcomePublisher(db,directory)
    original=publisher._atomic_write
    def crash(filename,payload):
        original(filename,payload)
        raise OSError('simulated death after rename before journal commit')
    monkeypatch.setattr(publisher,'_atomic_write',crash)
    with pytest.raises(OSError): publisher.publish_pending()
    path=directory/'peer-receiver_notice_123.json'
    path.unlink() # router may have consumed the transition
    with pytest.raises(OutcomeUncertain): OutcomePublisher(db,directory).publish_pending()
    assert not path.exists()
    # Explicit router agreement allows replay of the in-flight transition only.
    assert OutcomePublisher(db,directory,idempotent_replay=True).publish_pending()==1
    db.close()

def test_each_transition_waits_for_router_consumption(tmp_path):
    db,eid,directory=setup(tmp_path)
    db.uncertain(eid,'timeout')
    db.accept(eid,'turn-a')
    publisher=OutcomePublisher(db,directory)
    assert publisher.publish_pending()==1
    path=directory/'peer-receiver_notice_123.json'
    assert json.loads(path.read_text())['outcome']=='held'
    assert publisher.publish_pending()==0
    path.unlink()
    assert publisher.publish_pending()==1
    assert json.loads(path.read_text())['outcome']=='delivered'
    db.finish(eid,'failed')
    path.unlink()
    assert publisher.publish_pending()==0
    db.close()

def test_symlink_outcome_root_and_destinations(tmp_path):
    db,eid,directory=setup(tmp_path)
    db.accept(eid,'turn-a')
    directory.mkdir()
    link=tmp_path/'linked'; link.symlink_to(directory,target_is_directory=True)
    with pytest.raises(ValueError): OutcomePublisher(db,link).publish_pending()
    (directory/'peer-receiver_notice_123.json').symlink_to(tmp_path/'elsewhere')
    with pytest.raises(ValueError): OutcomePublisher(db,directory).publish_pending()
    db.close()
