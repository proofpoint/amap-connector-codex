"""Negative controls for the probe transport; no native CLI or network."""
import asyncio
import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import struct

import pytest

from amap_codex.app_server import Uncertain

_path = Path(__file__).parent / 'integration/_unified_ws.py'
_spec = importlib.util.spec_from_file_location('probe_ws', _path)
probe_ws = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe_ws)
_evidence_spec = importlib.util.spec_from_file_location('probe_evidence', _path.with_name('_unified_evidence.py'))
evidence = importlib.util.module_from_spec(_evidence_spec)
_evidence_spec.loader.exec_module(evidence)


def pointer_history():
    pointer = {'event_id': 'synthetic_event', 'lane': 'peer', 'notice_id': 'synthetic_notice'}
    item = {'id': 'input1', 'type': 'functionCallOutput', 'name': 'amap_notice',
            'namespace': 'amap_connector', 'output': json.dumps(pointer)}
    return pointer, {'thread': {'id': 'canonical', 'turns': [
        {'id': 'human-turn', 'status': 'completed', 'items': [
            {'id': 'human-input', 'type': 'userMessage', 'content': [{'type': 'text', 'text': 'original human input'}]}]},
        {'id': 'accepted-turn', 'status': 'completed', 'items': [item,
            {'id': 'reply', 'type': 'agentMessage', 'text': 'FINAL_MARKER'}]},
    ]}}


def test_probe_evidence_accepts_exact_pointer_and_unchanged_history():
    pointer, history = pointer_history()
    evidence.assert_original_pointer(history, pointer, 'accepted-turn')
    evidence.assert_retained_history(evidence.history_snapshot(history), copy.deepcopy(history))


@pytest.mark.parametrize('change', ['missing_lane', 'wrong_lane', 'missing_notice', 'wrong_notice',
                                  'extra_key', 'wrong_turn', 'duplicate', 'assistant_echo', 'wrong_namespace', 'wrong_name'])
def test_probe_evidence_rejects_partial_wrong_or_ambiguous_pointers(change):
    pointer, history = pointer_history()
    turn = history['thread']['turns'][1]
    item = turn['items'][0]
    altered = dict(pointer)
    if change == 'missing_lane':
        altered.pop('lane')
    elif change == 'wrong_lane':
        altered['lane'] = 'mail'
    elif change == 'missing_notice':
        altered.pop('notice_id')
    elif change == 'wrong_notice':
        altered['notice_id'] = 'another_notice'
    elif change == 'extra_key':
        altered['unexpected'] = True
    elif change == 'wrong_turn':
        turn['id'] = 'unaccepted-turn'
    elif change == 'duplicate':
        turn['items'].append(copy.deepcopy(item))
    elif change == 'assistant_echo':
        item['type'] = 'agentMessage'
    elif change == 'wrong_name':
        item['name'] = 'another_tool'
    else:
        item['namespace'] = 'another_namespace'
    item['output'] = json.dumps(altered)
    with pytest.raises(AssertionError):
        evidence.assert_original_pointer(history, pointer, 'accepted-turn')


@pytest.mark.parametrize('change', ['turn_id', 'thread_id', 'human_content', 'reply_content',
                                  'item_id', 'status', 'order', 'replace_with_equal_count'])
def test_probe_evidence_rejects_changed_history_even_with_same_count_and_final_marker(change):
    _, history = pointer_history()
    before = evidence.history_snapshot(history)
    turns = history['thread']['turns']
    if change == 'turn_id':
        turns[0]['id'] = 'replacement'
    elif change == 'thread_id':
        history['thread']['id'] = 'another-canonical'
    elif change == 'human_content':
        turns[0]['items'][0]['content'][0]['text'] = 'replacement human input'
    elif change == 'reply_content':
        turns[1]['items'].append({'id': 'extra', 'type': 'agentMessage', 'text': 'extra reply'})
    elif change == 'item_id':
        turns[0]['items'][0]['id'] = 'replacement-item'
    elif change == 'status':
        turns[0]['status'] = 'failed'
    elif change == 'order':
        turns.reverse()
    else:
        turns[0] = {'id': 'replacement', 'status': 'completed', 'items': []}
    with pytest.raises(AssertionError):
        evidence.assert_retained_history(before, history)


def server_frame(payload, first=0x81, mask_flag=0):
    length = len(payload)
    if length < 126:
        return bytes([first, length | mask_flag]) + payload
    if length < 65536:
        return bytes([first, 126 | mask_flag]) + struct.pack('!H', length) + payload
    return bytes([first, 127 | mask_flag]) + struct.pack('!Q', length) + payload


class Writer:
    def __init__(self):
        self.data = bytearray()
    def write(self, data):
        self.data.extend(data)
    async def drain(self):
        pass
    def close(self):
        pass
    async def wait_closed(self):
        pass


def test_probe_passive_observer_does_not_answer_server_approval():
    async def check():
        observed = []
        async def record(frame):
            observed.append(frame)
        client = probe_ws.UnixRPC(Path('/unused'), 'codex-cli 0.164.0', request_handler=record)
        client.reader, client.writer = asyncio.StreamReader(), Writer()
        request = {'id': 99, 'method': 'item/commandExecution/requestApproval',
                   'params': {'threadId': 'canonical', 'turnId': 'turn1'}}
        client.reader.feed_data(server_frame(json.dumps(request).encode()))
        client.reader.feed_eof()
        await client.read()
        assert observed == [request]
        assert client.writer.data == b'', 'passive observer must not cancel or grant approval'
    asyncio.run(check())


@pytest.mark.parametrize('bad', ['fragmented', 'masked', 'reserved', 'binary', 'oversized'])
def test_probe_transport_refuses_unsupported_frames_before_delivering_rpc_result(bad):
    async def check():
        client = probe_ws.UnixRPC(Path('/unused'), 'codex-cli 0.164.0')
        client.reader, client.writer = asyncio.StreamReader(), Writer()
        future = asyncio.get_running_loop().create_future()
        client.pending[7] = future
        payload = json.dumps({'id': 7, 'result': {}}).encode()
        if bad == 'oversized':
            payload += b' ' * (8 * 1024 * 1024 + 1 - len(payload))
        wire = server_frame(payload, first={'fragmented': 0x01, 'reserved': 0xc1,
                                           'binary': 0x82}.get(bad, 0x81),
                            mask_flag=0x80 if bad == 'masked' else 0)
        client.reader.feed_data(wire)
        client.reader.feed_eof()
        await client.read()
        with pytest.raises(Uncertain):
            await future
    asyncio.run(check())


@pytest.mark.parametrize('bad', ['status', 'accept', 'version'])
def test_probe_upgrade_and_build_must_match_before_use(monkeypatch, bad):
    async def check():
        reader = asyncio.StreamReader()
        class UpgradeWriter(Writer):
            def write(self, data):
                super().write(data)
                if data.startswith(b'GET '):
                    headers = dict(line.split(': ', 1) for line in data.decode().split('\r\n') if ': ' in line)
                    key = headers['Sec-WebSocket-Key']
                    accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
                    if bad == 'accept':
                        accept = 'wrong'
                    status = '200 OK' if bad == 'status' else '101 Switching Protocols'
                    reader.feed_data(f'HTTP/1.1 {status}\r\nSec-WebSocket-Accept: {accept}\r\n\r\n'.encode())
                    version = '0.999.0' if bad == 'version' else '0.164.0'
                    reader.feed_data(server_frame(json.dumps({'id': 1, 'result': {
                        'userAgent': 'codex-fixture/' + version}}).encode()))
        async def connect(*args):
            return reader, UpgradeWriter()
        monkeypatch.setattr(asyncio, 'open_unix_connection', connect)
        client = probe_ws.UnixRPC(Path('/unused'), 'codex-cli 0.164.0')
        try:
            with pytest.raises(AssertionError if bad == 'version' else RuntimeError):
                await client.start()
        finally:
            await client.stop()
    asyncio.run(check())
