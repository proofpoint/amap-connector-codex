import asyncio
import json
from pathlib import Path
import struct

import pytest

from amap_codex.local_socket import ProtocolError, Socket, accept_key


class Writer:
    def __init__(self): self.data = bytearray()
    def write(self, data): self.data.extend(data)
    async def drain(self): pass
    def close(self): pass
    async def wait_closed(self): pass


def frame(data, *, first=129, masked=False, wide=None):
    size = len(data)
    head = bytes([first])
    flag = 128 if masked else 0
    if wide == 8 or size > 65535:
        head += bytes([flag | 127]) + struct.pack('!Q', size)
    elif wide == 2 or size > 125:
        head += bytes([flag | 126]) + struct.pack('!H', size)
    else:
        head += bytes([flag | size])
    if masked:
        key = b'abcd'
        head += key
        data = bytes(v ^ key[i % 4] for i, v in enumerate(data))
    return head + data


def socket_for(data, *, client=True, limit=4096):
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return Socket(reader, Writer(), client=client, limit=limit, timeout=.1)


@pytest.mark.parametrize('masked', [False, True])
def test_fragmented_text_with_ping_and_correct_masking(masked):
    async def check():
        data = frame(b'{"ok":', first=1, masked=masked)
        data += frame(b'ping', first=137, masked=masked)
        data += frame(b'true}', first=128, masked=masked)
        socket = socket_for(data, client=not masked)
        assert await socket.receive() == {'ok': True}
        assert socket.writer.data[0] == 138
    asyncio.run(check())


@pytest.mark.parametrize('case', ['mask', 'extension', 'binary', 'continuation', 'new_fragment', 'size',
                                  'combined_size', 'short_wide', 'long_wide', 'control_size', 'control_fragment',
                                  'utf8', 'json_array', 'duplicate_key', 'nan', 'overflow',
                                  'close_length', 'close_code', 'close_utf8'])
def test_protocol_guards(case):
    async def check():
        data = frame(b'{}')
        limit = 4096
        if case == 'mask': data = frame(b'{}', masked=True)
        elif case == 'extension': data = frame(b'{}', first=193)
        elif case == 'binary': data = frame(b'{}', first=130)
        elif case == 'continuation': data = frame(b'{}', first=128)
        elif case == 'new_fragment': data = frame(b'{', first=1) + frame(b'}')
        elif case == 'size': data = frame(json.dumps({'value': 'x' * 4096}).encode())
        elif case == 'combined_size':
            message = json.dumps({'value': 'x' * 5000}).encode()
            data = frame(message[:3000], first=1) + frame(message[3000:], first=128)
        elif case == 'short_wide': data = frame(b'{}', wide=2)
        elif case == 'long_wide': data = frame(b'{}', wide=8)
        elif case == 'control_size': data = frame(b' ' * 126, first=137)
        elif case == 'control_fragment': data = frame(b'ping', first=9)
        elif case == 'utf8': data = frame(b'{"a":"\xff"}')
        elif case == 'json_array': data = frame(b'[]')
        elif case == 'duplicate_key': data = frame(b'{"a":1,"a":2}')
        elif case == 'nan': data = frame(b'{"a":NaN}')
        elif case == 'overflow': data = frame(b'{"a":1e999}')
        elif case == 'close_length': data = frame(b'1', first=136)
        elif case == 'close_code': data = frame(struct.pack('!H', 1005), first=136)
        elif case == 'close_utf8': data = frame(struct.pack('!H', 1000) + b'\xff', first=136)
        with pytest.raises(ProtocolError):
            await socket_for(data, limit=limit).receive()
    asyncio.run(check())


@pytest.mark.parametrize('partial', ['header', 'body', 'fragments'])
def test_partial_frames_have_a_deadline_without_timing_out_idle_sessions(partial):
    async def check():
        reader = asyncio.StreamReader()
        socket = Socket(reader, Writer(), client=True, limit=4096, timeout=.01)
        data = b'\x81' if partial == 'header' else b'\x81\x03{' if partial == 'body' else frame(b'{', first=1)
        reader.feed_data(data)
        with pytest.raises(asyncio.TimeoutError): await socket.receive()
    asyncio.run(check())


def test_outgoing_mask_length_serialization_and_bound():
    async def check():
        for client in (True, False):
            socket = socket_for(b'', client=client)
            await socket.send({'value': 'x' * 200})
            received = socket_for(bytes(socket.writer.data), client=not client)
            assert await received.receive() == {'value': 'x' * 200}
        for value in ({'value': float('nan')}, {'value': 'x' * 4096}):
            with pytest.raises(ProtocolError):
                await socket_for(b'').send(value)
    asyncio.run(check())


@pytest.mark.parametrize('key', [None, '', 'a', 'YQ==', '!' * 24, 'YWJjZGVmZ2hpamtsbW5vcA==!'])
def test_upgrade_keys_are_exact_base64_16_bytes(key):
    with pytest.raises(ProtocolError): accept_key(key)


def test_real_local_upgrade_and_bidirectional_rpc(tmp_path):
    async def check():
        ready = asyncio.Future()
        async def accept(reader, writer):
            try:
                socket = await Socket.accept(reader, writer)
                message = await socket.receive()
                await socket.send({'result': message})
                await socket.close()
                ready.set_result(None)
            except BaseException as exc: ready.set_exception(exc)
        path = tmp_path / 'sock'
        server = await asyncio.start_unix_server(accept, str(path))
        try:
            socket = await Socket.connect(path)
            await socket.send({'id': 1})
            assert await socket.receive() == {'result': {'id': 1}}
            await socket.close()
            await ready
        finally:
            server.close()
            await server.wait_closed()
    asyncio.run(check())
