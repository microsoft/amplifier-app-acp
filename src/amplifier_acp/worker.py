"""One process per native session: cwd, credentials and writer leases never mix."""
from __future__ import annotations
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

class NativeError(RuntimeError):
    def __init__(self, message, *, data=None):
        super().__init__(message)
        self.data = data or {}

class NativeWorker:
    def __init__(self, session_id, cwd, config, observe, bridge):
        self.session_id, self.cwd, self.config = session_id, cwd, config
        self.observe, self.bridge = observe, bridge
        self.process = None
        self.pending = {}
        self.tasks = set()
        self.closed = False
        self.transport_closed = False
        self.generation_lease = None
        self.ready = asyncio.get_running_loop().create_future()
        self.report = {}
        self._write_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()

    async def start(self, host_info=None, mcp_servers=None):
        env = os.environ.copy()
        # An inherited shell's venv must not redirect Foundation installers.
        env.pop('VIRTUAL_ENV',None)
        from .preparation_policy import configure as configure_preparation, metadata as preparation_metadata
        configure_preparation(self.config,env)
        from .module_sources import configure
        configure(self.config,env)
        # Explicit process env remains authoritative over settings keys.env.
        if self.config.get('home'):
            env['AMPLIFIER_HOME'] = str(Path(self.config['home']).expanduser().resolve())
        if self.config.get('appHome'):
            env['AMPLIFIER_WEB_HOME'] = str(Path(self.config['appHome']).expanduser().resolve())
        if self.config.get('registryHome'):
            env['AMPLIFIER_ACP_REGISTRY_HOME'] = str(Path(self.config['registryHome']).expanduser().resolve())
        env['AMPLIFIER_ACP_MANAGED_ROOTS']=json.dumps(self.config.get('managedSessionRoots',[]))
        env['AMPLIFIER_ACP_HOST_FEATURES']=json.dumps((host_info or {}).get('features',{}))
        from .native.updates import active_release
        app_home=Path(env.get('AMPLIFIER_WEB_HOME',Path.home()/'.amplifier-unified'))
        generation=self.config.get('runtimeGeneration',active_release(app_home).get('current'))
        if generation is not None and (not isinstance(generation,str) or len(generation)!=32 or any(c not in '0123456789abcdef' for c in generation)):
            raise NativeError('Invalid configured native runtime generation.')
        managed_command=None
        if generation and self.config.get('managedGenerations') is True:
            from .native.runtime_profiles import ensure
            from .generations import launcher
            generation=await ensure(app_home,generation,{'id':self.session_id,'workspace':self.cwd,'bundle':self.config.get('cloneBundle') or self.config.get('bundle')},shared_home=env.get('AMPLIFIER_HOME'))
            managed_command=(await launcher(app_home,generation))['workerCommand']
        if generation and not self.config.get('workerCommand') and not managed_command:
            raise NativeError('This native home has a qualified generation. Configure its explicit workerCommand or opt into managedGenerations before starting; the adapter will not mount a different environment silently.')
        env['AMPLIFIER_UNIFIED_RELEASE']=generation or ''
        if generation and (app_home/'updates/releases'/generation/'profiles-qualified.json').exists():env['AMPLIFIER_RUNTIME_IMMUTABLE']='1'
        command = managed_command or self.config.get('workerCommand') or [sys.executable, '-m', 'amplifier_acp.native.runtime_worker']
        self.process = await asyncio.create_subprocess_exec(*command, cwd=self.config.get('executionDirectory',self.cwd), env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=sys.stderr, limit=48_000_001)
        if generation:
            from .native.generation_leases import acquire
            try:self.generation_lease=acquire(app_home,generation,self.process.pid)
            except BaseException:
                self.process.terminate();await self.process.wait();raise
        self.reader = asyncio.create_task(self.read())
        bundle=self.config.get('bundle')
        app_home=Path(env.get('AMPLIFIER_WEB_HOME',Path.home()/'.amplifier-unified'))
        settings=app_home/'admin-settings.yaml'
        if settings.exists():
            import yaml
            saved=(yaml.safe_load(settings.read_text()) or {}).get('bundle',{})
            if 'active' in saved:bundle=saved['active']
        if self.config.get('cloneBundle'):bundle=self.config['cloneBundle']
        await self.write({'op':'start','session':{'id':self.session_id,'workspace':self.cwd,
            'workingDirectory':self.config.get('executionDirectory',self.cwd),'bundle':bundle, 'hostBridge':host_info is not None, 'hostInfo':host_info or {}, 'mcpServers':mcp_servers or []}})
        try:
            self.report = await asyncio.wait_for(asyncio.shield(self.ready), self.config.get('startupTimeout',180))
            expected_policy=preparation_metadata(env).get('runtimePreparation')
            if expected_policy and self.report.get('runtimePreparation')!=expected_policy:
                raise NativeError('Native worker did not confirm the launcher immutable preparation policy; use the qualified native worker package.')
        except BaseException:
            await self.close()
            raise
        return self.report

    async def write(self, message):
        if self.transport_closed or not self.process or self.process.returncode is not None:
            raise NativeError('Native runtime is not running; no work was replayed.')
        encoded = (json.dumps(message,ensure_ascii=False,allow_nan=False)+'\n').encode()
        if len(encoded)>48_000_000:
            raise NativeError('Native request exceeds the 48 MB transport limit.')
        async with self._write_lock:
            self.process.stdin.write(encoded)
            await self.process.stdin.drain()

    async def command(self, op, **arguments):
        identity = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self.pending[identity]=future
        try:
            await self.write({'op':op,'id':identity,**arguments})
            # No automatic timeout/retry: an admitted operation can have effects.
            return await future
        finally:
            self.pending.pop(identity,None)

    async def control(self, operation, arguments=None):
        return await self.command('control',operation=operation,arguments=arguments or {})

    def track(self, awaitable):
        task=asyncio.create_task(awaitable); self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def bridge_request(self, row):
        try:
            value = await self.bridge(row['operation'],row.get('args',{}))
            await self.write({'op':'bridge.result','id':row['id'],'result':value})
        except BaseException as exc:
            if not self.closed:
                await self.write({'op':'bridge.result','id':row['id'],'error':str(exc)})

    async def read(self):
        failure = NativeError('Native runtime stopped; inspect durable history before retrying.', data={'outcome':'unknown','replayed':False})
        try:
            while line := await self.process.stdout.readline():
                row=json.loads(line)
                if row.get('op')=='reply':
                    future=self.pending.get(row.get('id'))
                    if future and not future.done():
                        if row.get('error'):future.set_exception(NativeError(row['error'],data={k:v for k,v in row.items() if k not in {'error','op','id'}}))
                        else:future.set_result(row.get('result'))
                elif row.get('op')=='bridge':self.track(self.bridge_request(row))
                else:
                    if row.get('type')=='runtime.ready' and not self.ready.done():self.ready.set_result(row.get('report',{}))
                    if row.get('type')=='runtime.error':
                        failure=NativeError(row.get('error','Runtime failed'),data={k:v for k,v in row.items() if k not in {'type','error'}})
                        if not self.ready.done():self.ready.set_exception(failure)
                    await self.observe(row)
        except (ValueError, OSError, asyncio.IncompleteReadError) as exc:
            failure=NativeError('Invalid native runtime response; no work replayed.',data={'outcome':'unknown','errorType':type(exc).__name__})
        finally:
            # EOF is not process exit. Refuse writes immediately, then join
            # the process before publishing closure/settling terminal proof.
            self.transport_closed=True
            if not self.ready.done():self.ready.set_exception(failure)
            for future in self.pending.values():
                if not future.done():future.set_exception(failure)
            await self.reap()
            if self.generation_lease:self.generation_lease.unlink(missing_ok=True)
            await self.observe({'type':'runtime.closed','error':str(failure),'data':failure.data})

    async def reap(self):
        """Join a disconnected worker without joining its reader from itself."""
        if not self.process or self.process.returncode is not None:return
        try:await asyncio.wait_for(self.process.wait(),10)
        except TimeoutError:
            if self.process.returncode is None:
                try:self.process.terminate()
                except ProcessLookupError:pass
            try:await asyncio.wait_for(self.process.wait(),5)
            except TimeoutError:
                if self.process.returncode is None:
                    try:self.process.kill()
                    except ProcessLookupError:pass
                await self.process.wait()

    async def close(self):
        async with self._close_lock:
            await self._close()

    async def _close(self):
        if self.closed:return
        self.closed=True
        if self.process:
            if self.process.returncode is None:
                try:await self.write({'op':'stop'})
                except (BrokenPipeError, ConnectionResetError, NativeError):pass
                await self.reap()
            if hasattr(self,'reader'):await asyncio.gather(self.reader,return_exceptions=True)
        for task in self.tasks:task.cancel()
        await asyncio.gather(*self.tasks,return_exceptions=True)
        if self.generation_lease:self.generation_lease.unlink(missing_ok=True)
