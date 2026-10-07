import json
from pathlib import Path
import pytest
from amap_codex.config import Lane
from amap_codex.spool import Scanner, event_id, validate_notice, Refusal

SPEC = Path(__file__).resolve().parents[1] / 'vendor' / 'amap-spec'

def artifacts(tmp_path,lane='peer'):
    notices,messages = tmp_path/'notices',tmp_path/'messages'
    notices.mkdir(); messages.mkdir()
    target = Lane(lane,notices,messages,tmp_path/'claim')
    notice_id = 'receiver_notice_123'
    notice = {'contract_version':'2','notice_id':notice_id,'ts':'2026-10-06','kind':'peer' if lane=='peer' else 'deliver','message':{'id':'sender_message_456','from':'sender@example.test','subject':'evil instruction','preview':'evil preview','mailbox':'peer' if lane=='peer' else 'INBOX'}}
    body = {'contract_version':'2','notice_id':notice_id,'body_text':'evil body','to':'agent@example.test'}
    return target,notice,body

def write(target,notice,body):
    filename = f"notice-{notice['notice_id']}.json"
    (target.notice_dir/filename).write_text(json.dumps(notice))
    if body is not None: (target.message_dir/filename).write_text(json.dumps(body))

def scanned(target): return list(Scanner().scan(target,'instance-a','agent@example.test'))[0]

def test_distinct_ids_and_all_correlation_metadata(tmp_path):
    lane,notice,body = artifacts(tmp_path)
    notice['extra'] = {'runtime_extension':True}
    notice['message'].update(task_id='task-a',in_reply_to='earlier_message_789',references=['earlier_message_789'],unknown=True)
    write(lane,notice,body)
    record = scanned(lane)
    assert record.state == 'pending'
    assert record.payload == {'event_id':record.event_id,'lane':'peer','notice_id':'receiver_notice_123','peer_from':'sender@example.test','peer_message_id':'sender_message_456','task_id':'task-a','in_reply_to':'earlier_message_789','references':['earlier_message_789']}
    assert 'evil' not in json.dumps(record.payload)
    assert event_id('instance-a','mail',record.notice_id) != record.event_id

def test_mail_payload_contains_only_pointer_metadata(tmp_path):
    lane,notice,body = artifacts(tmp_path,'mail')
    notice['message'].update(id='<provider@example.test>', **{'from':'Sender display name'})
    notice['message']['runtime_extension'] = {'new':'opaque'}
    write(lane,notice,body)
    record = scanned(lane)
    assert record.state == 'pending'
    assert set(record.payload) == {'event_id','lane','notice_id'}

@pytest.mark.parametrize('change,detail',[
    ('missing_version','version refusal'),('wrong_body_version','version refusal'),('recipient','body spool to'),('router','router sender'),('kind','kind'),('id','message.id'),('correlation','in_reply_to'),('sender','message.from'),('mailbox','mailbox'),('body_id','body.notice_id')])
def test_peer_refusals(tmp_path,change,detail):
    lane,notice,body = artifacts(tmp_path)
    if change=='missing_version': notice.pop('contract_version'); notice.pop('message')
    if change=='wrong_body_version': body['contract_version']='1'
    if change=='recipient': body['to']='someone@example.test'
    if change=='router': notice['message']['from']='amap.router@example.test'
    if change=='kind': notice['kind']='deliver'
    if change=='id': notice['message']['id']='<encoded@example.test>'
    if change=='correlation': notice['message']['in_reply_to']='bad\n'
    if change=='sender': notice['message']['from']='Display <sender@example.test>'
    if change=='mailbox': notice['message']['mailbox']='INBOX'
    if change=='body_id': body['notice_id']='wrong'
    write(lane,notice,body)
    result = scanned(lane)
    assert result.state=='refused' and detail in result.detail

def test_bounded_publication_grace(tmp_path):
    lane,notice,body = artifacts(tmp_path)
    write(lane,notice,None)
    now = (lane.notice_dir/'notice-receiver_notice_123.json').stat().st_mtime
    scanner = Scanner(clock=lambda:now)
    assert list(scanner.scan(lane,'a','agent@example.test',30)) == []
    scanner.clock = lambda:now+31
    assert list(scanner.scan(lane,'a','agent@example.test',30))[0].state == 'refused'
    write(lane,notice,body)
    assert list(scanner.scan(lane,'a','agent@example.test',30))[0].state == 'pending'

def test_notice_and_body_symlink_containment(tmp_path):
    lane,notice,body = artifacts(tmp_path)
    write(lane,notice,body)
    path = lane.message_dir/'notice-receiver_notice_123.json'
    outside = tmp_path/'outside.json'; outside.write_text(path.read_text())
    path.unlink(); path.symlink_to(outside)
    assert scanned(lane).state == 'refused'

def test_attachment_binding_and_unknown_members(tmp_path):
    lane,notice,body = artifacts(tmp_path)
    notice['message']['attachments'] = [{'filename':'a.txt','media_type':'text/plain','size_bytes':0,'disposition':'clean','content_ref':'other.attachments/0','unknown':'allowed'}]
    write(lane,notice,body)
    assert 'bound' in scanned(lane).detail

def test_pinned_notice_fixtures():
    # Every invalid recognized schema field must be checked without jsonschema runtime.
    assert SPEC.exists()
    paths = sorted((SPEC/'fixtures'/'invalid').glob('notice-*.json')) + sorted((SPEC/'fixtures'/'invalid').glob('peer-*.json'))
    for path in paths:
        doc = json.loads(path.read_text())
        with pytest.raises(Refusal):
            validate_notice(doc,'peer' if path.name.startswith('peer-') else 'mail',doc.get('notice_id',''))
    for pattern,lane in [('notice-*.json','mail'),('peer-*.json','peer')]:
        for path in sorted((SPEC/'fixtures'/'valid').glob(pattern)):
            doc = json.loads(path.read_text())
            validate_notice(doc,lane,doc['notice_id'])


def test_pinned_body_fixtures():
    from amap_codex.spool import validate_body
    for group in ('valid', 'invalid'):
        for path in sorted((SPEC/'fixtures'/group).glob('message-*.json')):
            doc = json.loads(path.read_text())
            if group == 'invalid':
                with pytest.raises(Refusal):
                    validate_body(doc, doc.get('notice_id', ''))
            else:
                validate_body(doc, doc['notice_id'])
