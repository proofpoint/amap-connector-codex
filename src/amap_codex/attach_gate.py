"""Single native client, canonical binding and protected settings; no scheduler."""
import asyncio
import contextlib
import os
from pathlib import Path
import re

from .local_socket import ProtocolError, Socket

READ_ONLY = {
    'server/diagnostics', 'model/list', 'modelProvider/capabilities/read', 'experimentalFeature/list',
    'permissionProfile/list', 'collaborationMode/list', 'plugin/list', 'plugin/installed', 'plugin/read',
    'app/list', 'app/installed', 'app/read', 'skills/list', 'hooks/list', 'config/read', 'configRequirements/read',
    'account/read', 'account/rateLimits/read', 'account/usage/read', 'account/workspaceMessages/read',
    'account/bedrock/checkGovCloudRequirements',
    'mcpServerStatus/list', 'fs/readFile', 'fs/readDirectory', 'fs/getMetadata', 'fuzzyFileSearch',
    'fuzzyFileSearch/sessionStart', 'fuzzyFileSearch/sessionUpdate', 'fuzzyFileSearch/sessionStop',
    'thread/list', 'thread/loaded/list',
}
THREAD_METHODS = {
    'thread/resume', 'thread/read', 'thread/turns/list', 'thread/items/list', 'thread/items/read',
    'thread/unsubscribe', 'thread/name/set', 'thread/readState/update', 'thread/goal/get',
    'thread/queue/add', 'thread/queue/list', 'thread/queue/update', 'thread/queue/delete',
    'thread/queue/reorder', 'thread/queue/start', 'thread/backgroundTerminals/list',
    'thread/timeline/list', 'turn/start', 'turn/steer', 'turn/interrupt',
    'thread/settings/update',
}


class AttachPolicy:
    def __init__(self, thread_id, *, cwd, model, sandbox_name, sandbox, approval_policy, roots=None,
                 approvals_reviewer='user'):
        self.thread_id, self.cwd, self.model = thread_id, str(Path(cwd).resolve()), model
        self.sandbox_name, self.sandbox, self.approval_policy = sandbox_name, sandbox, approval_policy
        self.roots = roots
        self.approvals_reviewer = approvals_reviewer

    def validate(self, method, params):
        if not isinstance(method, str) or not isinstance(params, dict):
            raise ProtocolError('invalid native request')
        if method == 'initialize':
            return
        if method not in READ_ONLY | THREAD_METHODS:
            raise ProtocolError('native control is unavailable in unified mode; use the audited operator workflow')
        if method in THREAD_METHODS and params.get('threadId') != self.thread_id:
            raise ProtocolError('native request must name the registered canonical conversation')
        if 'threadId' in params and params['threadId'] != self.thread_id:
            raise ProtocolError('native request names another conversation')
        if params.get('cwd') is not None and str(Path(params['cwd']).resolve()) != self.cwd:
            raise ProtocolError('native request changes the registered workspace')
        if params.get('model') is not None and params['model'] != self.model:
            raise ProtocolError('native model differs from the managed model; change configuration explicitly')
        if params.get('approvalPolicy') is not None and params['approvalPolicy'] != self.approval_policy:
            raise ProtocolError('native approval policy differs from the managed pane policy')
        if params.get('approvalsReviewer') is not None and params['approvalsReviewer'] != self.approvals_reviewer:
            raise ProtocolError('native approval reviewer differs from the managed pane policy')
        if params.get('sandbox') is not None and params['sandbox'] != self.sandbox_name:
            raise ProtocolError('native sandbox differs from the managed pane policy')
        if params.get('sandboxPolicy') is not None and params['sandboxPolicy'] != self.sandbox:
            raise ProtocolError('native sandbox policy differs from the managed pane policy')
        if params.get('runtimeWorkspaceRoots') is not None and params['runtimeWorkspaceRoots'] != self.roots:
            raise ProtocolError('native request changes registered workspace roots')
        forbidden = {'config', 'baseInstructions', 'developerInstructions', 'history', 'path',
                     'permissions', 'environments', 'disabledPluginIds'}
        if any(params.get(key) is not None for key in forbidden):
            raise ProtocolError('native request attempts an unsupported protected configuration change')
        if params.get('toolOutput') is not None:
            raise ProtocolError('native terminal input cannot forge connector tool output')
        collaboration = params.get('collaborationMode')
        if collaboration is not None:
            settings = collaboration.get('settings') if isinstance(collaboration, dict) else None
            if (not isinstance(settings, dict) or settings.get('model') != self.model
                    or collaboration.get('mode') not in {'default', 'plan'}
                    or set(settings) - {'model', 'reasoning_effort', 'developer_instructions'}):
                raise ProtocolError('native collaboration settings change protected model or instructions')

    def normalized(self, method, params):
        self.validate(method, params)
        if params.get('collaborationMode') is not None:
            collaboration = params['collaborationMode']
            # Native resume can send a cached copy of its built-in mode text.
            # The API defines null as "use the backend's built-in instructions".
            # Keep that trusted behavior without accepting caller-provided
            # developer text or replacing the protected AMAP thread policy.
            return {**params, 'collaborationMode': {**collaboration,
                'settings': {**collaboration['settings'], 'developer_instructions': None}}}
        return params


class AttachGate:
    def __init__(self, backend, policy):
        self.backend, self.policy = backend, policy
        self.listener = None
        self.active = None
        self.tasks = set()
        self.refused_methods = []
        self.accepting = True

    @property
    def client_count(self):
        return int(self.active is not None)

    async def start(self):
        self.listener = await asyncio.start_unix_server(self._accept, str(self.backend.front_path))
        self.backend.front_path.chmod(0o600)

    async def _accept(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        if self.active is not None or not self.accepting:
            try:
                writer.write(b'HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n')
            finally:
                writer.close()
                self.tasks.discard(task)
            return
        self.active = task
        front = back = None
        copies = []
        requests, server_requests = {}, set()
        try:
            front = await Socket.accept(reader, writer, limit=self.backend.frame_limit, timeout=self.backend.timeout)
            back = await Socket.connect(self.backend.backend_path, limit=self.backend.frame_limit,
                                        timeout=self.backend.timeout)

            async def human():
                initialized = False
                while True:
                    frame = await front.receive()
                    identifier, method = frame.get('id'), frame.get('method')
                    if method is None:
                        if type(identifier) not in {int, str} or identifier not in server_requests:
                            raise ProtocolError('native response has no current server request')
                        if ('result' in frame) == ('error' in frame):
                            raise ProtocolError('invalid native approval response')
                        server_requests.remove(identifier)
                        await back.send(frame)
                        continue
                    if identifier is None:
                        if method != 'initialized' or not initialized:
                            raise ProtocolError('unsupported native notification')
                        await back.send(frame)
                        continue
                    if type(identifier) not in {int, str} or identifier in requests or len(requests) >= 1000:
                        raise ProtocolError('invalid or excessive native request identity')
                    try:
                        if not initialized and method != 'initialize':
                            raise ProtocolError('native connection must initialize first')
                        frame = {**frame, 'params': self.policy.normalized(method, frame.get('params', {}))}
                        if method == 'initialize':
                            if initialized:
                                raise ProtocolError('native connection initialized twice')
                            initialized = True
                    except (ProtocolError, TypeError, ValueError):
                        if isinstance(method, str) and re.fullmatch(r'[A-Za-z/]+', method):
                            self.refused_methods = (self.refused_methods + [method])[-20:]
                        await front.send({'id': identifier, 'error': {'code': -32000,
                            'message': 'Unified attachment refused this control or configuration; keep the canonical conversation and managed pane settings.'}})
                        continue
                    requests[identifier] = method
                    await back.send(frame)

            async def native():
                while True:
                    frame = await back.receive()
                    identifier, method = frame.get('id'), frame.get('method')
                    if method is not None and identifier is not None:
                        if type(identifier) not in {int, str} or len(server_requests) >= 100:
                            raise ProtocolError('invalid or excessive native server requests')
                        server_requests.add(identifier)
                    elif method == 'serverRequest/resolved':
                        server_requests.discard(frame.get('params', {}).get('requestId'))
                    elif method is None:
                        name = requests.pop(identifier, None)
                        if name is None:
                            raise ProtocolError('native backend returned an unknown response identity')
                        result = frame.get('result')
                        if isinstance(result, dict) and name in {'thread/list', 'thread/loaded/list'}:
                            result['data'] = [item for item in result.get('data', []) if
                                (item == self.policy.thread_id if name == 'thread/loaded/list' else
                                 isinstance(item, dict) and item.get('id') == self.policy.thread_id)]
                            result['nextCursor'] = None
                    await front.send(frame)

            copies = [asyncio.create_task(human()), asyncio.create_task(native())]
            await asyncio.wait(copies, return_when=asyncio.FIRST_COMPLETED)
        except (OSError, ProtocolError, ValueError, asyncio.IncompleteReadError, asyncio.TimeoutError):
            pass  # No retry, backend launch or permission answer follows a failed attach.
        finally:
            for copy in copies:
                copy.cancel()
            await asyncio.gather(*copies, return_exceptions=True)
            for socket in (front, back):
                if socket:
                    await socket.close()
            writer.close()
            self.active = None
            self.tasks.discard(task)

    async def close(self):
        self.accepting = False
        if self.listener:
            self.listener.close()
            await self.listener.wait_closed()
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        with contextlib.suppress(FileNotFoundError):
            self.backend.front_path.unlink()
