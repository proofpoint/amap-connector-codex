"""Small Unix-WebSocket client for the isolated feasibility probe only.

Not a production transport: it deliberately refuses fragmented/data extensions.
Uses no network listener or dependency beyond the Python standard library.
"""
import asyncio
import base64
import hashlib
import json
import os
import struct

from amap_codex.app_server import RPCError, Uncertain, version_matches


class UnixRPC:
    def __init__(self, path, expected_version):
        self.path, self.expected_version = path, expected_version
        self.turn_starts = 0
        self.initialize_result = {}
        self.notifications = asyncio.Queue(maxsize=10000)
        self.last_error = None
        self.pending = {}
        self.sequence = 0
        self.writer = None
        self.task = None
        self.blocked = 0

    def reserve_id(self):
        self.sequence += 1
        return self.sequence

    async def start(self):
        self.reader, self.writer = await asyncio.open_unix_connection(str(self.path))
        key = base64.b64encode(os.urandom(16)).decode()
        self.writer.write(('GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n'
                           'Connection: Upgrade\r\nSec-WebSocket-Key: ' + key +
                           '\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
        await self.writer.drain()
        upgrade = await asyncio.wait_for(self.reader.readuntil(b'\r\n\r\n'), 5)
        lines = upgrade.decode('ascii').split('\r\n')
        headers = {name.lower(): value.strip() for line in lines[1:] if ':' in line
                   for name, value in [line.split(':', 1)]}
        accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        if not lines[0].startswith('HTTP/1.1 101 ') or headers.get('sec-websocket-accept') != accept:
            raise RuntimeError('probe WebSocket upgrade rejected')
        self.task = asyncio.create_task(self.read())
        self.initialize_result = await self.request('initialize', {
            'clientInfo': {'name': 'amap_probe', 'version': '0.1.0'},
            'capabilities': {'experimentalApi': True}})
        assert version_matches(self.initialize_result.get('userAgent'), self.expected_version)
        await self.send({'method': 'initialized', 'params': {}})

    async def frame(self, payload, opcode=1):
        length = len(payload)
        header = bytes([0x80 | opcode])
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 65536:
            header += bytes([0x80 | 126]) + struct.pack('!H', length)
        else:
            header += bytes([0x80 | 127]) + struct.pack('!Q', length)
        mask = os.urandom(4)
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        self.writer.write(header + mask + masked)
        await self.writer.drain()

    async def send(self, message):
        await self.frame(json.dumps(message, separators=(',', ':')).encode())

    async def request(self, method, params, *, rpc_id=None):
        rpc_id = self.reserve_id() if rpc_id is None else rpc_id
        future = asyncio.get_running_loop().create_future()
        self.pending[rpc_id] = future
        if method == 'turn/start':
            self.turn_starts += 1
        try:
            await self.send({'id': rpc_id, 'method': method, 'params': params})
            return await asyncio.wait_for(future, 20)
        finally:
            self.pending.pop(rpc_id, None)

    async def read(self):
        try:
            while True:
                first, second = await self.reader.readexactly(2)
                if not first & 0x80 or second & 0x80 or first & 0x70:
                    raise ValueError('unsupported probe WebSocket framing')
                opcode, length = first & 15, second & 127
                if length == 126:
                    length = struct.unpack('!H', await self.reader.readexactly(2))[0]
                elif length == 127:
                    length = struct.unpack('!Q', await self.reader.readexactly(8))[0]
                if length > 8 * 1024 * 1024:
                    raise ValueError('probe frame exceeds bound')
                payload = await self.reader.readexactly(length)
                if opcode == 8:
                    break
                if opcode == 9:
                    await self.frame(payload, 10)
                    continue
                if opcode == 10:
                    continue
                if opcode != 1:
                    raise ValueError('unsupported probe WebSocket opcode')
                message = json.loads(payload)
                if 'method' in message:
                    if 'id' in message:
                        self.blocked += 1
                        await self.send({'id': message['id'], 'error': {
                            'code': -32000, 'message': 'synthetic probe authorizes no interaction'}})
                    elif message['method'] in {'turn/started', 'turn/completed', 'thread/status/changed'}:
                        self.notifications.put_nowait(message)
                elif 'id' in message:
                    future = self.pending.get(message['id'])
                    if future is not None and not future.done():
                        if 'error' in message:
                            future.set_exception(RPCError(message['error']))
                        else:
                            future.set_result(message['result'])
        except (OSError, ValueError, asyncio.IncompleteReadError):
            self.last_error = 'probe transport closed'
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(Uncertain('probe transport closed'))

    async def stop(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.writer is not None:
            self.writer.close()
            await self.writer.wait_closed()
