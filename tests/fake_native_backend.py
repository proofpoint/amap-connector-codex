"""Unix-only local RPC child for lifecycle tests; no model or network."""
import asyncio
import os
from pathlib import Path
import sys

from amap_codex.local_socket import Socket


async def main():
    os.umask(0o077)
    path = Path(sys.argv[-1].removeprefix('unix://'))
    async def accept(reader, writer):
        connection = await Socket.accept(reader, writer)
        try:
            while True:
                request = await connection.receive()
                if 'id' in request:
                    await connection.send({'id': request['id'], 'result': {'userAgent': 'codex-fixture/0.164.0'}})
        except (OSError, asyncio.IncompleteReadError):
            pass
        finally:
            await connection.close()
    listener = await asyncio.start_unix_server(accept, str(path))
    path.chmod(0o600)
    async with listener:
        await listener.serve_forever()


asyncio.run(main())
