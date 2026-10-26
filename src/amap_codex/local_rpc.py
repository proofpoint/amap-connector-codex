"""Passive app-server RPC connection. Owns no process and answers no approvals."""
import asyncio
import contextlib

from .app_server import NotSubmitted, RPCError, Uncertain, version_matches
from .local_socket import ProtocolError, Socket


class LocalRPC:
    def __init__(self, path, *, expected_version, timeout=30, frame_limit=32 * 1024 * 1024):
        self.path, self.expected_version = path, expected_version
        self.timeout, self.frame_limit = timeout, frame_limit
        self.socket = None
        self.pending = {}
        self.sequence = self.turn_starts = 0
        self.initialize_result = {}
        self.notifications = asyncio.Queue(maxsize=1000)
        self.blocked = []
        self.last_error = None
        self.closed = False
        self.task = None

    def reserve_id(self):
        self.sequence += 1
        return self.sequence

    async def start(self):
        if self.socket is not None or self.closed:
            raise RuntimeError('connection is already started or closed')
        try:
            self.socket = await Socket.connect(self.path, limit=self.frame_limit, timeout=self.timeout)
            self.task = asyncio.create_task(self._read())
            self.initialize_result = await self.request('initialize', {
                'clientInfo': {'name': 'amap_connector', 'version': '0.1.0'},
                'capabilities': {'experimentalApi': True}})
            if not version_matches(self.initialize_result.get('userAgent'), self.expected_version):
                raise ProtocolError('local backend build differs from configured build')
            await self.notify('initialized', {})
        except BaseException:
            await self.stop()
            raise

    async def notify(self, method, params):
        await self._send({'method': method, 'params': params})

    async def _send(self, frame):
        if self.closed or self.socket is None or self.socket.closed:
            raise NotSubmitted('local backend is unavailable before write')
        try:
            await self.socket.send(frame)
        except ProtocolError as exc:
            # Serialization/size checks precede the write in Socket.send/frame.
            raise NotSubmitted('local frame rejected before write') from exc
        except (OSError, asyncio.TimeoutError) as exc:
            raise Uncertain('local backend write not confirmed') from exc

    async def request(self, method, params, *, rpc_id=None):
        rpc_id = self.reserve_id() if rpc_id is None else rpc_id
        if type(rpc_id) not in {int, str} or rpc_id in self.pending or len(self.pending) >= 1000:
            raise ValueError('RPC ID must be distinct')
        future = asyncio.get_running_loop().create_future()
        self.pending[rpc_id] = future
        if method == 'turn/start':
            self.turn_starts += 1
        try:
            await self._send({'id': rpc_id, 'method': method, 'params': params})
            return await asyncio.wait_for(asyncio.shield(future), self.timeout)
        except RPCError as exc:
            raise exc.at_method(method)
        except asyncio.TimeoutError as exc:
            raise Uncertain('local RPC acknowledgement not confirmed') from exc
        finally:
            self.pending.pop(rpc_id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()

    async def _read(self):
        try:
            while True:
                frame = await self.socket.receive()
                identifier = frame.get('id')
                if 'id' in frame and type(identifier) not in {int, str}:
                    raise ProtocolError('invalid RPC identity')
                method = frame.get('method')
                if method is not None:
                    params = frame.get('params', {})
                    if not isinstance(method, str) or not isinstance(params, dict):
                        raise ProtocolError('invalid server event')
                    if identifier is not None:
                        if len(self.blocked) >= 100:
                            raise ProtocolError('too many unanswered server requests')
                        # Retain metadata only. Native clients make the decision;
                        # this connection must never cancel, grant or interrupt.
                        self.blocked.append({'id': identifier, 'method': method,
                            'thread_id': params.get('threadId'), 'turn_id': params.get('turnId')})
                    elif method == 'serverRequest/resolved':
                        self.blocked = [r for r in self.blocked if r['id'] != params.get('requestId')]
                    elif method in {'turn/started', 'turn/completed', 'thread/status/changed'}:
                        self.notifications.put_nowait(frame)
                elif identifier is not None:
                    future = self.pending.get(identifier)
                    if future is None or future.done():
                        continue
                    if 'error' in frame and isinstance(frame['error'], dict) and 'result' not in frame:
                        future.set_exception(RPCError(frame['error']))
                    elif 'result' in frame and isinstance(frame['result'], dict) and 'error' not in frame:
                        future.set_result(frame['result'])
                    else:
                        raise ProtocolError('invalid RPC result')
                else:
                    raise ProtocolError('unidentified RPC frame')
        except (OSError, ValueError, TypeError, ProtocolError, asyncio.IncompleteReadError, asyncio.QueueFull):
            self.last_error = 'local backend connection failed'
        finally:
            self.closed = True
            self.last_error = self.last_error or 'local backend connection closed'
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(Uncertain('local backend connection closed'))
            if self.socket:
                await self.socket.close()

    async def stop(self):
        self.closed = True
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.socket:
            with contextlib.suppress(OSError, asyncio.TimeoutError):
                await self.socket.close()
