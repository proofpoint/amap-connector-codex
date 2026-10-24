"""Negative controls for the probe transport; no native CLI or network."""
import asyncio
import base64
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
