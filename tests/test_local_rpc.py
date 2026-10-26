import asyncio
from pathlib import Path

import pytest

from amap_codex.app_server import NotSubmitted, Uncertain
from amap_codex.local_rpc import LocalRPC
from amap_codex.local_socket import Socket


def test_passive_requests_reordered_responses_and_disconnect(tmp_path):
    async def check():
        requests = []
        done = asyncio.Event()
        async def server(reader, writer):
            socket = await Socket.accept(reader, writer)
            try:
                init = await socket.receive()
                await socket.send({'id': init['id'], 'result': {'userAgent': 'codex-fixture/0.164.0'}})
                assert (await socket.receive())['method'] == 'initialized'
                first, second = await socket.receive(), await socket.receive()
                requests.extend([first, second])
                await socket.send({'id': 98, 'method': 'item/commandExecution/requestApproval',
                    'params': {'threadId': 'thread1', 'turnId': 'turn1', 'command': 'SECRET'}})
                await socket.send({'id': second['id'], 'result': {'value': 2}})
                await socket.send({'id': first['id'], 'result': {'value': 1}})
                requests.append(await socket.receive())
                await socket.send({'method': 'serverRequest/resolved', 'params': {'requestId': 98}})
                await socket.close()
            finally:
                done.set()
        path = tmp_path / 'socket'
        listener = await asyncio.start_unix_server(server, str(path))
        client = LocalRPC(path, expected_version='codex-cli 0.164.0', timeout=.5)
        try:
            await client.start()
            values = await asyncio.gather(client.request('first', {}), client.request('second', {}))
            assert values == [{'value': 1}, {'value': 2}]
            assert len(client.blocked) == 1 and 'SECRET' not in str(client.blocked)
            with pytest.raises(Uncertain): await client.request('inspect', {})
            await done.wait()
            assert client.blocked == []
            assert [r.get('method') for r in requests] == ['first', 'second', 'inspect']
            with pytest.raises(NotSubmitted): await client.request('third', {})
        finally:
            await client.stop()
            listener.close()
            await listener.wait_closed()
    asyncio.run(check())


@pytest.mark.parametrize('bad', ['build', 'id', 'envelope', 'method', 'event_params'])
def test_bad_backend_cannot_complete_rpc(tmp_path, bad):
    async def check():
        done = asyncio.Event()
        async def server(reader, writer):
            socket = await Socket.accept(reader, writer)
            try:
                init = await socket.receive()
                await socket.send({'id': init['id'], 'result': {'userAgent': 'codex-fixture/' +
                    ('9.0.0' if bad == 'build' else '0.164.0')}})
                if bad == 'build': return
                await socket.receive()
                request = await socket.receive()
                frame = {'id': request['id'], 'result': {}}
                if bad == 'id': frame['id'] = True
                elif bad == 'envelope': frame['error'] = {'code': -1}
                elif bad == 'method': frame = {'method': [], 'params': {}}
                else: frame = {'method': 'turn/completed', 'params': []}
                await socket.send(frame)
            finally:
                await socket.close()
                done.set()
        path = tmp_path / 'socket'
        listener = await asyncio.start_unix_server(server, str(path))
        client = LocalRPC(path, expected_version='codex-cli 0.164.0', timeout=.3)
        try:
            if bad == 'build':
                with pytest.raises(RuntimeError, match='build'): await client.start()
            else:
                await client.start()
                with pytest.raises(Uncertain): await client.request('inspect', {})
            await done.wait()
        finally:
            await client.stop()
            listener.close()
            await listener.wait_closed()
    asyncio.run(check())
