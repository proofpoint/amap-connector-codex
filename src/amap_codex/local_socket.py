"""Bounded RFC6455 text transport over explicitly selected local Unix sockets.

No ambient daemon discovery, TCP listener, dependency, automatic reconnect or
RPC replay. The same framing serves a native attach proxy and its backend.
"""
import asyncio
import base64
import contextlib
import hashlib
import json
import math
import os
import struct


class ProtocolError(RuntimeError):
    pass


GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
HEADER_LIMIT = 16384


def accept_key(key):
    try:
        raw = base64.b64decode(key, validate=True)
    except (ValueError, TypeError) as exc:
        raise ProtocolError('invalid WebSocket key') from exc
    if len(raw) != 16:
        raise ProtocolError('invalid WebSocket key length')
    return base64.b64encode(hashlib.sha1((key + GUID).encode('ascii')).digest()).decode('ascii')


async def headers(reader, timeout):
    try:
        raw = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), timeout)
        if len(raw) > HEADER_LIMIT:
            raise ProtocolError('WebSocket headers exceeded bound')
        lines = raw.decode('ascii').split('\r\n')
        result = {}
        for line in lines[1:-2]:
            name, separator, value = line.partition(':')
            if not separator or not name or name != name.strip() or name.lower() in result:
                raise ProtocolError('invalid or duplicate WebSocket header')
            result[name.lower()] = value.strip()
        if (result.get('upgrade', '').lower() != 'websocket' or
                'upgrade' not in {t.strip().lower() for t in result.get('connection', '').split(',')} or
                'sec-websocket-extensions' in result or 'sec-websocket-protocol' in result):
            raise ProtocolError('unsupported WebSocket upgrade')
        return lines[0], result
    except (UnicodeError, ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
        raise ProtocolError('invalid WebSocket upgrade') from exc


class Socket:
    def __init__(self, reader, writer, *, client, limit, timeout=30):
        self.reader, self.writer, self.client = reader, writer, client
        self.limit, self.timeout = limit, timeout
        self.lock = asyncio.Lock()
        self.closed = False
        self.closing = None

    @classmethod
    async def connect(cls, path, *, limit=32 * 1024 * 1024, timeout=30):
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(path), limit=HEADER_LIMIT), timeout)
        socket = cls(reader, writer, client=True, limit=limit, timeout=timeout)
        try:
            key = base64.b64encode(os.urandom(16)).decode('ascii')
            writer.write(('GET /rpc HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                          f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n').encode('ascii'))
            await asyncio.wait_for(writer.drain(), timeout)
            status, response = await headers(reader, timeout)
            if status != 'HTTP/1.1 101 Switching Protocols' or response.get('sec-websocket-accept') != accept_key(key):
                raise ProtocolError('WebSocket peer rejected upgrade')
            return socket
        except BaseException:
            await socket.close()
            raise

    @classmethod
    async def accept(cls, reader, writer, *, limit=32 * 1024 * 1024, timeout=30):
        socket = cls(reader, writer, client=False, limit=limit, timeout=timeout)
        try:
            request, values = await headers(reader, timeout)
            if request not in {'GET / HTTP/1.1', 'GET /rpc HTTP/1.1'} or values.get('sec-websocket-version') != '13':
                raise ProtocolError('unsupported WebSocket request')
            key = accept_key(values.get('sec-websocket-key'))
            writer.write(('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                          f'Sec-WebSocket-Accept: {key}\r\n\r\n').encode('ascii'))
            await asyncio.wait_for(writer.drain(), timeout)
            return socket
        except BaseException:
            await socket.close()
            raise

    async def frame(self, payload, opcode=1):
        if self.closed:
            raise ConnectionError('local socket is closed')
        size = len(payload)
        if size > self.limit or (opcode >= 8 and size > 125):
            raise ProtocolError('outbound WebSocket frame exceeded bound')
        mask_bit = 128 if self.client else 0
        wire = bytes([128 | opcode])
        if size < 126:
            wire += bytes([mask_bit | size])
        elif size < 65536:
            wire += bytes([mask_bit | 126]) + struct.pack('!H', size)
        else:
            wire += bytes([mask_bit | 127]) + struct.pack('!Q', size)
        if self.client:
            mask = os.urandom(4)
            wire += mask
            payload = bytes(value ^ mask[i % 4] for i, value in enumerate(payload))
        async with self.lock:
            self.writer.write(wire + payload)
            await asyncio.wait_for(self.writer.drain(), self.timeout)

    async def send(self, value):
        try:
            wire = json.dumps(value, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('ascii')
        except (TypeError, ValueError) as exc:
            raise ProtocolError('invalid local JSON frame') from exc
        await self.frame(wire)

    async def receive(self):
        chunks = bytearray()
        fragmented = False
        fragment_deadline = None
        while True:
            first = (await self.reader.readexactly(1) if fragment_deadline is None else
                     await asyncio.wait_for(self.reader.readexactly(1), max(0, fragment_deadline - asyncio.get_running_loop().time())))[0]
            async with asyncio.timeout(self.timeout if fragment_deadline is None else
                                       max(0, fragment_deadline - asyncio.get_running_loop().time())):
                final, opcode, payload = await self._incoming(first, len(chunks))
            if opcode == 8:
                if len(payload) == 1:
                    raise ProtocolError('invalid WebSocket close payload')
                if len(payload) >= 2:
                    code = struct.unpack('!H', payload[:2])[0]
                    if not (code in {1000, 1001, 1002, 1003, 1007, 1008, 1009, 1010, 1011, 1012, 1013, 1014}
                            or 3000 <= code < 5000):
                        raise ProtocolError('invalid WebSocket close code')
                    try:
                        payload[2:].decode('utf-8')
                    except UnicodeError as exc:
                        raise ProtocolError('invalid WebSocket close reason') from exc
                await self.frame(payload, 8)
                raise ConnectionError('local WebSocket peer closed')
            if opcode == 9:
                await self.frame(payload, 10)
                continue
            if opcode == 10:
                continue
            if opcode not in {0, 1} or (opcode == 0) != fragmented:
                raise ProtocolError('unsupported WebSocket opcode or fragment sequence')
            chunks.extend(payload)
            fragmented = not final
            if fragmented and fragment_deadline is None:
                fragment_deadline = asyncio.get_running_loop().time() + self.timeout
            if final:
                try:
                    value = json.loads(chunks.decode('utf-8'), object_pairs_hook=unique_pairs,
                                       parse_constant=invalid_constant, parse_float=finite_float)
                except (ValueError, UnicodeError, RecursionError) as exc:
                    raise ProtocolError('invalid JSON message') from exc
                if not isinstance(value, dict):
                    raise ProtocolError('RPC message must be an object')
                return value

    async def _incoming(self, first, accumulated):
        second = (await self.reader.readexactly(1))[0]
        final, opcode = bool(first & 128), first & 15
        if first & 112 or bool(second & 128) == self.client:
            raise ProtocolError('invalid WebSocket flags or masking')
        size = second & 127
        if size == 126:
            size = struct.unpack('!H', await self.reader.readexactly(2))[0]
            if size < 126:
                raise ProtocolError('nonminimal WebSocket length')
        elif size == 127:
            size = struct.unpack('!Q', await self.reader.readexactly(8))[0]
            if size < 65536 or size >= 2**63:
                raise ProtocolError('invalid WebSocket length')
        if size > self.limit or (opcode < 8 and accumulated + size > self.limit):
            raise ProtocolError('inbound WebSocket message exceeded bound')
        if opcode >= 8 and (not final or size > 125):
            raise ProtocolError('invalid WebSocket control frame')
        mask = await self.reader.readexactly(4) if second & 128 else None
        payload = await self.reader.readexactly(size)
        if mask:
            payload = bytes(value ^ mask[i % 4] for i, value in enumerate(payload))
        return final, opcode, payload

    async def close(self):
        self.closed = True
        self.writer.close()
        if self.closing is None:
            self.closing = asyncio.create_task(self.writer.wait_closed())
        with contextlib.suppress(OSError, asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(self.closing), self.timeout)


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON member')
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError('nonfinite JSON number')


def finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('nonfinite JSON number')
    return result
