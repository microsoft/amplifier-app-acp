"""ACP v1 JSON-RPC stdio, translating the existing native application runtime."""
from __future__ import annotations
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import uuid
from . import __version__
from .worker import NativeWorker, NativeError

NATIVE='amplifier.dev/native'
HOST='amplifier.dev/host'
MAX_LINE=48_000_000

class RpcError(Exception):
    def __init__(self,code,message,data=None):
        self.code,self.message,self.data=code,message,data
        super().__init__(message)

class Session:
    def __init__(self,server,identity,cwd):
        self.server,self.identity,self.cwd=server,identity,cwd
        self.bundle_override=None;self.execution_directory=cwd;self.relocating=False
        self.worker=None; self.prompt=None; self.input_id=None; self.cancelled=False
        self.streamed=False; self.had_deltas=False; self.last_model_id=None; self.awaiting_children=False; self.tools=set(); self.lock=asyncio.Lock();self.report={}
        from .steering import Receipts
        from .relocation import home
        self.steering=Receipts(home(server),identity);self.steering_lock=asyncio.Lock();self.generation_id=None
        self.config_options=[];self.mcp_servers=[];self.idle_task=None;self.start_lock=asyncio.Lock();self.config_cancel=None;self.config_applying=False;self.retiring=False;self.retired=False;self.active_requests=0;self.worker_retirement_pending=None

    async def start(self):
        async with self.start_lock:
            await self._start()

    def configuration_journal(self):
        home=Path(self.server.config.get('appHome') or os.environ.get('AMPLIFIER_WEB_HOME') or Path.home()/'.amplifier-unified').expanduser().resolve()
        return home/'sessions'/self.identity/'configuration-transaction.json'

    def restore_configuration(self):
        path=self.configuration_journal()
        if not path.exists():return
        saved=json.loads(path.read_text())
        expected={'configuration.json','control-state.json','effective-configuration.json'}
        if set(saved)!=expected:raise RpcError(-32000,'Invalid configuration recovery journal; files preserved.')
        from .native.host.config import write_private
        for name,value in saved.items():
            target=path.with_name(name)
            if value is None:target.unlink(missing_ok=True)
            else:write_private(target,value)
        path.unlink()

    async def _start(self):
        if self.retiring or self.retired:raise RpcError(-32008,'Native session is retiring; this handle cannot accept new work.')
        if not self.config_applying:self.restore_configuration()
        if not self.relocating:
            from .relocation import state
            location=state(self.server,self.identity,self.cwd)
            if location['blocked']:raise RpcError(-32000,'Native relocation outcome is unresolved; inspect its receipt before execution.',{'outcome':'unknown','replayed':False})
            self.execution_directory=location['executionDirectory']
        if self.worker and (self.worker.closed or self.cancelled):await self.worker.close()
        if self.worker and self.worker.process.returncode is None:return
        config={**self.server.config,'executionDirectory':self.execution_directory,**({'cloneBundle':self.bundle_override} if self.bundle_override else {})}
        self.worker=NativeWorker(self.identity,self.cwd,config,self.observe,self.bridge)
        self.report=await self.worker.start(self.server.host_info if HOST in self.server.extensions else None,self.mcp_servers)
        self.cancelled=False
        await self.refresh_options(notify=False)
        self.schedule_retirement()

    def schedule_retirement(self):
        if self.idle_task and not self.idle_task.done():self.idle_task.cancel()
        async def retire():
            while self.worker and not self.worker.closed:
                await asyncio.sleep(max(1,self.server.config.get("idleSeconds",60)))
                if self.active_requests or self.start_lock.locked() or self.config_applying or self.prompt and not self.prompt.done():continue
                try:
                    worker=self.worker;self.worker_retirement_pending=worker
                    result=await worker.command("retire")
                    if result.get("retired"):
                        await self.worker.process.wait()
                        return
                except NativeError:return
                finally:self.worker_retirement_pending=None
        self.idle_task=asyncio.create_task(retire())

    async def bridge(self,operation,args):
        if HOST not in self.server.extensions:
            raise RpcError(-32601,'This client has not negotiated the Amplifier host capability.',{'operation':operation})
        return await self.server.request('_amplifier/host',{'sessionId':self.identity,'operation':operation,'args':args})

    async def update(self,session_update,**data):
        await self.server.notify('session/update',{'sessionId':self.identity,'update':{'sessionUpdate':session_update,**data}})

    async def permission(self,row):
        options=[]
        for value in row.get('options') or ['allow','deny']:
            options.append({'optionId':value,'name':value.replace('_',' ').title(),
                'kind':'allow_once' if value in {'allow','approve'} else 'reject_once'})
        try:
            result=await self.server.request('session/request_permission',{'sessionId':self.identity,
                'toolCall':{'toolCallId':row['id'],'title':row.get('prompt','Permission required'),'status':'pending'},'options':options})
            outcome=result.get('outcome',{})
            decision=outcome.get('optionId') if outcome.get('outcome')=='selected' else None
            if decision not in {o['optionId'] for o in options}:decision=next((o['optionId'] for o in options if o['kind']=='reject_once'),None)
            if decision is None:raise NativeError('No rejection option was provided.')
            if self.worker and self.worker.process.returncode is None:
                await self.worker.command('approval',approval_id=row['id'],decision=decision)
        except (NativeError,RpcError,asyncio.CancelledError):
            # Never convert unavailable permission into approval.
            if self.worker and self.worker.process.returncode is None:
                deny=next((o['optionId'] for o in options if o['kind']=='reject_once'),None)
                if deny is not None:
                    try:await self.worker.command('approval',approval_id=row['id'],decision=deny)
                    except NativeError:pass

    async def observe(self,row):
        kind=row.get('type')
        root=row.get('sessionId') or row.get('session_id') or self.identity
        is_root=root==self.identity
        if is_root and kind=='generation.started':
            self.generation_id=row.get('generation_id')
            if self.prompt and not self.prompt.done():row={**row,'activeInputId':self.input_id}
        if is_root and kind in {'input.delivered','steering.applied','steering.held','steering.unknown'} and row.get('input_id'):
            receipt=self.steering.read(row['input_id'])
            if receipt and receipt['generationId']==row.get('target_generation_id'):
                disposition='applied' if kind in {'input.delivered','steering.applied'} else kind.split('.')[1]
                self.steering.update(row['input_id'],{'accepted':True,'disposition':disposition,**({'reason':row['reason']} if row.get('reason') else {})})
                row={**row,'activeInputId':receipt['activeInputId']}
        if is_root and kind in {'generation.finished','generation.failed','generation.detached','runtime.closed'}:
            # A late anchored command may be classified held immediately after
            # generation.finished when the loop drains its inbox. Only runtime
            # loss proves there will be no further authoritative disposition.
            uncertain=self.steering.uncertain() if kind=='runtime.closed' else []
            for receipt in uncertain:
                if NATIVE in self.server.extensions:await self.server.notify('_amplifier/event',{'sessionId':self.identity,'event':{'type':'steering.unknown','input_id':receipt['inputId'],'activeInputId':receipt['activeInputId'],'target_generation_id':receipt['generationId'],'reason':receipt['reason']}})
            self.generation_id=None
        if NATIVE in self.server.extensions and kind not in {'assistant.delta','assistant.message','execution.event'}:
            safe={k:v for k,v in row.items() if k not in {'prompt','options','error','report'}}
            await self.server.notify('_amplifier/event',{'sessionId':self.identity,'event':safe})
        if kind=='runtime.ready':self.report=row.get('report',{})
        if kind=='approval.requested':self.server.track(self.permission(row))
        elif kind=='assistant.delta' and is_root:
            self.streamed=True;self.had_deltas=True
            await self.update('agent_message_chunk',content={'type':'text','text':row.get('text','')})
        elif kind=='assistant.message' and is_root and not self.had_deltas:
            text=row.get('text','')
            if text:await self.update('agent_message_chunk',content={'type':'text','text':text});self.streamed=True
        elif kind=='execution.event':
            event=row.get('event',{})
            if event.get('kind')=='llm' and event.get('phase')=='running' and event.get('lifecycle')!='background' and event.get('sessionId')==self.identity and event.get('id')!=self.last_model_id:
                self.last_model_id=event.get('id');self.streamed=False;self.had_deltas=False
            if event.get('kind')=='tool':
                identity=event.get('toolCallId') or event.get('id')
                if not identity:return
                # Child IDs are scoped so concurrent delegates cannot collide.
                identity=str(event.get('sessionId') or self.identity)+':'+str(identity)
                status={'running':'in_progress','completed':'completed','error':'failed','cancelled':'failed'}.get(event.get('phase'),'pending')
                data={'toolCallId':identity,'status':status}
                if identity not in self.tools:
                    self.tools.add(identity);data.update(title=event.get('label','Tool'),kind='other')
                    if 'input' in event:data['rawInput']=event['input']
                    await self.update('tool_call',**data)
                else:
                    if 'output' in event:data['rawOutput']=event['output']
                    await self.update('tool_call_update',**data)
        elif kind in {'generation.finished','generation.failed','generation.detached'} and is_root:
            if not self.prompt or self.prompt.done():return
            ids=row.get('input_ids',[])
            if self.input_id and ids and self.input_id not in ids and not self.awaiting_children:return
            if kind=='generation.finished' and row.get('active_job_ids'):
                self.awaiting_children=True
                return
            if kind=='generation.finished':
                if not self.streamed and row.get('text'):await self.update('agent_message_chunk',content={'type':'text','text':row['text']})
                self.prompt.set_result({'stopReason':'cancelled' if self.cancelled else 'end_turn'})
            elif kind=='generation.detached' and self.cancelled:self.prompt.set_result({'stopReason':'cancelled'})
            else:self.prompt.set_exception(NativeError('Native generation failed; no work replayed.',data={k:row[k] for k in ('error_type','error_category','retryable','cancellation') if k in row}))
        elif kind=='runtime.closed':
            if self.prompt and not self.prompt.done():
                if self.cancelled:self.prompt.set_result({'stopReason':'cancelled'})
                else:self.prompt.set_exception(NativeError(row['error'],data=row.get('data')))

    async def refresh_options(self,notify=True):
        state=await self.worker.control('mode.list')
        options=[]
        if state.get('available') and state.get('modes'):
            options.append({'id':'mode','name':'Mode','category':'mode','type':'select',
                'currentValue':state.get('active') or '__default__',
                'options':[{'value':'__default__','name':'Bundle default'}]+[
                    {'value':r['name'],'name':r['name'],'description':r.get('description','')} for r in state['modes']]})
        # Configured choices are not a remote model catalog and never trigger model calls.
        choices=[r for r in self.report.get('provider_choices',[]) if r.get('model')]
        selection=self.report.get('selection') or self.report.get('effective_selection') or {}
        selected_id=selection.get('instance') or selection.get('id')
        if selected_id and selection.get('model') and not any(r.get('id')==selected_id and r.get('model')==selection['model'] for r in choices):
            choices.append({'id':selected_id,'model':selection['model'],'display_name':selected_id})
        self.model_choices=choices
        if choices:
            current=next((str(i) for i,r in enumerate(choices) if r.get('id')==selected_id and r.get('model')==selection.get('model')),'__default__')
            options.append({'id':'model','name':'Configured model','category':'model','type':'select','currentValue':current,
                'options':[{'value':'__default__','name':'Bundle default'}]+[{'value':str(i),'name':str(r.get('display_name') or r['id'])+' / '+r['model']} for i,r in enumerate(choices)]})
        self.config_options=options
        if notify:await self.update('config_option_update',configOptions=options)
        return options

    async def configure(self,identity,value):
        if self.prompt and not self.prompt.done():raise RpcError(-32000,'Configuration requires an idle turn.')
        await self.start()
        option=next((r for r in self.config_options if r['id']==identity),None)
        if not option or value not in {r['value'] for r in option['options']}:raise RpcError(-32602,'Unknown session configuration option or value.')
        if identity=='mode':await self.worker.control('mode.clear' if value=='__default__' else 'mode.set',{} if value=='__default__' else {'name':value})
        elif value=='__default__':await self.worker.control('provider.reset');self.report['selection']=None;self.report['effective_selection']=None
        else:
            choice=self.model_choices[int(value)]
            selection={'instance':choice['id'],'model':choice['model']}
            await self.worker.control('provider.select',selection);self.report['selection']=selection
        return {'configOptions':await self.refresh_options()}

    async def apply_configuration(self,args):
        if self.config_cancel is not None:raise RpcError(-32000,'A configuration change is already pending.')
        if self.lock.locked() and not args.get('whenIdle'):raise RpcError(-32000,'Configuration requires an idle turn.')
        cancel=self.config_cancel=asyncio.Event()
        acquiring=asyncio.create_task(self.lock.acquire());cancelled=asyncio.create_task(cancel.wait())
        held=False
        try:
            await asyncio.wait({acquiring,cancelled},return_when=asyncio.FIRST_COMPLETED)
            held=acquiring.done() and not acquiring.cancelled() and acquiring.result()
            if cancel.is_set():return {'cancelled':True,'applied':False}
            if not held:await acquiring;held=True
            self.config_applying=True
            journal=self.configuration_journal()
            paths=[journal.with_name(name) for name in ('configuration.json','control-state.json','effective-configuration.json')]
            saved={path.name:path.read_text() if path.exists() else None for path in paths}
            from .native.host.config import write_private
            write_private(journal,json.dumps(saved))
            try:
                result=await self.worker.control('configuration.apply',args)
                if result.get('requiresRestart'):
                    await self.worker.close()
                    await self.start()
                    result={**result,'requiresRestart':False,'remounted':True,'configuration':await self.worker.control('configuration.inspect')}
                journal.unlink()
            except (NativeError,ValueError,RpcError) as error:
                await self.worker.close()
                self.restore_configuration()
                try:await self.start()
                except Exception:
                    raise RpcError(-32000,'Configuration failed; prior settings restored but runtime recovery failed. No input was replayed.',{'restored':True,'ready':False,'replayed':False}) from error
                raise RpcError(-32000,'Configuration failed; prior settings and runtime restored. No input was replayed.',{'restored':True,'ready':True,'replayed':False}) from error
            return result
        finally:
            acquiring.cancel();cancelled.cancel();await asyncio.gather(acquiring,cancelled,return_exceptions=True)
            if held:self.lock.release()
            self.config_cancel=None;self.config_applying=False

    async def send(self,blocks,meta):
        if self.lock.locked():raise RpcError(-32000,'A prompt is already active for this session.')
        async with self.lock:
            await self.start()
            text=[];attachments=[]
            for block in blocks:
                if not isinstance(block,dict):raise RpcError(-32602,'Content blocks must be objects.')
                if block.get('type')=='text' and isinstance(block.get('text'),str):text.append(block['text'])
                elif block.get('type')=='image':
                    from .content import image_attachment
                    if len(attachments)>=8:raise RpcError(-32602,'Attach up to 8 images per prompt.')
                    attachments.append(image_attachment(self.server.config,block))
                elif block.get('type')=='resource' and isinstance(block.get('resource'),dict) and isinstance(block['resource'].get('text'),str):
                    text.append('Embedded reference material: '+json.dumps(block['resource'],ensure_ascii=False))
                elif block.get('type')=='resource' and isinstance(block.get('resource'),dict) and isinstance(block['resource'].get('blob'),str):
                    from .content import embedded_attachment
                    if len(attachments)>=8:raise RpcError(-32602,'Attach up to 8 resources per prompt.')
                    attachments.append(embedded_attachment(self.server.config,block['resource']))
                    text.append('Embedded resource: '+str(block['resource'].get('uri','')))
                elif block.get('type')=='resource_link' and isinstance(block.get('uri'),str):
                    # Explicit quoted reference, interpreted by configured tools; no implicit network read.
                    text.append('Referenced resource: '+json.dumps({k:block[k] for k in ('uri','name','description','mimeType') if k in block},ensure_ascii=False))
                else:raise RpcError(-32602,'This content type is not advertised by the adapter.',{'type':block.get('type')})
            value='\n'.join(text) or ('[Image attachment]' if attachments else '')
            if not value.strip():raise RpcError(-32602,'A nonempty prompt is required.')
            command=(meta or {}).get('amplifier.dev/command') or {}
            command_id=command.get('commandId') if NATIVE in self.server.extensions else None
            if command_id is not None and (not isinstance(command_id,str) or not command_id or len(command_id)>128):
                raise RpcError(-32602,'commandId must contain 1 to 128 characters.')
            self.input_id=command_id or str(uuid.uuid4())
            if command_id:
                previous=await self.worker.command('delivery',input_id=command_id)
                if previous.get('delivery')=='accepted':
                    raise RpcError(-32001,'This input was already admitted. Read native history; it was not replayed.',{'outcome':'already-admitted','commandId':command_id,'replayed':False})
            self.cancelled=False;self.streamed=False;self.had_deltas=False;self.last_model_id=None;self.awaiting_children=False
            self.prompt=asyncio.get_running_loop().create_future()
            try:
                await self.worker.command('send',text=value,input_id=self.input_id,attachments=attachments)
                return await self.prompt
            finally:
                if self.prompt and not self.prompt.done():self.prompt.cancel()
                self.prompt=None
                self.schedule_retirement()

    async def settled_input(self,operation,args,*,require_existing=False):
        """One admitted finite run shares ACP streaming and terminal settlement."""
        if self.lock.locked():return {'accepted':False,'executed':False,'reason':'A turn is already active.'}
        identity=args.get('inputId')
        if not isinstance(identity,str) or not identity or len(identity)>200:raise RpcError(-32602,'A bounded deterministic inputId is required.')
        if operation!='observation.submit' and (not isinstance(args.get('text'),str) or not args['text'].strip()):raise RpcError(-32602,'Input text is required.')
        if operation=='schedule.submit' and args.get('kind') not in {'task','monitor'}:raise RpcError(-32602,'Scheduled kind must be task or monitor.')
        resident=self.worker if require_existing else None
        def available():
            return resident is not None and self.worker is resident and not resident.closed and resident.process and resident.process.returncode is None and not self.cancelled and not self.retiring and self.worker_retirement_pending is not resident
        refused={'accepted':False,'executed':False,'reason':'The original native worker is unavailable or retiring; no runtime was started.'}
        async with self.lock:
            if require_existing:
                if not available():return refused
            else:await self.start()
            worker=resident if require_existing else self.worker
            try:previous=await worker.command('delivery',input_id=identity)
            except NativeError as error:
                if require_existing and error.data.get('executed') is False:return refused
                raise
            if require_existing and not available():return refused
            if previous.get('delivery')=='accepted':
                raise RpcError(-32001,'Input was already admitted; inspect its history.',{'outcome':'already-admitted','commandId':identity,'replayed':False})
            self.input_id=identity;self.cancelled=False;self.streamed=False;self.had_deltas=False;self.last_model_id=None;self.awaiting_children=False
            self.prompt=asyncio.get_running_loop().create_future()
            try:
                try:result=await worker.control(operation,args)
                except NativeError as error:
                    if require_existing and error.data.get('executed') is False:return refused
                    raise
                if not result.get('accepted'):return result
                return {**result,**await self.prompt}
            finally:
                if self.prompt and not self.prompt.done():self.prompt.cancel()
                self.prompt=None
                self.schedule_retirement()

    async def cancel(self):
        self.cancelled=True
        if self.worker and self.worker.process.returncode is None:
            await self.worker.close()

    async def retire(self):
        if self.retired:return {'retired':True}
        if self.retiring:return {'retired':False,'reason':'retirement_unknown'}
        if self.active_requests or self.lock.locked() or self.config_applying or self.start_lock.locked():return {'retired':False,'reason':'active_request'}
        async with self.start_lock:
            self.retiring=True
            if self.idle_task:self.idle_task.cancel()
            worker=self.worker
            try:
                if worker and worker.process and worker.process.returncode is None:
                    result=await worker.command('retire')
                    if not result.get('retired'):
                        self.retiring=False;self.schedule_retirement()
                        return {'retired':False,'reason':'owned_work'}
                    await asyncio.wait_for(worker.process.wait(),30)
                self.retired=True
                return {'retired':True}
            except (NativeError,asyncio.TimeoutError):
                # A lost retirement result never authorizes process termination.
                return {'retired':False,'reason':'retirement_unknown'}

    async def close(self):
        if self.config_cancel:self.config_cancel.set()
        if self.idle_task:self.idle_task.cancel()
        self.cancelled=True
        if self.worker:await self.worker.close()

class Server:
    def __init__(self,config,output):
        self.config,self.output=config,output
        self.initialized=False;self.extensions=set();self.sessions={};self.pending={};self.tasks=set();self._counter=0

    def track(self,awaitable):
        task=asyncio.create_task(awaitable);self.tasks.add(task);task.add_done_callback(self.tasks.discard)

    async def send(self,row):
        text=json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n'
        if len(text.encode())>MAX_LINE:raise RpcError(-32000,'Response exceeds transport bound; use a narrower operation.')
        self.output.write(text);self.output.flush()

    async def notify(self,method,params):await self.send({'jsonrpc':'2.0','method':method,'params':params})

    async def request(self,method,params):
        self._counter+=1;identity='agent:'+str(self._counter)
        future=asyncio.get_running_loop().create_future();self.pending[identity]=future
        try:
            await self.send({'jsonrpc':'2.0','id':identity,'method':method,'params':params})
            return await future
        finally:self.pending.pop(identity,None)

    async def handle(self,row):
        identity=row.get('id') if isinstance(row,dict) else None
        params=row.get('params') if isinstance(row,dict) else None
        tracked=self.sessions.get(params.get('sessionId')) if isinstance(params,dict) and row.get('method')!='_amplifier/retire' else None
        if tracked:tracked.active_requests+=1
        try:
            if not isinstance(row,dict) or row.get('jsonrpc')!='2.0' or ('method' in row and not isinstance(row['method'],str)):
                await self.send({'jsonrpc':'2.0','id':identity,'error':{'code':-32600,'message':'Invalid JSON-RPC request.'}})
                return
            if 'method' not in row:
                future=self.pending.get(identity)
                if future and not future.done():
                    if 'error' in row:future.set_exception(RpcError(row['error'].get('code',-32000),row['error'].get('message','Client request failed')))
                    elif 'result' in row:future.set_result(row['result'])
                return
            if not isinstance(row.get('params',{}),dict):raise RpcError(-32602,'Parameters must be an object.')
            value=await self.dispatch(row['method'],row.get('params',{}))
            if identity is not None:await self.send({'jsonrpc':'2.0','id':identity,'result':value})
        except RpcError as exc:
            if identity is not None:await self.send({'jsonrpc':'2.0','id':identity,'error':{'code':exc.code,'message':exc.message,**({'data':exc.data} if exc.data is not None else {})}})
        except (NativeError,ValueError,KeyError,OSError) as exc:
            if identity is not None:await self.send({'jsonrpc':'2.0','id':identity,'error':{'code':-32000,'message':str(exc),**({'data':exc.data} if getattr(exc,'data',None) is not None else {})}})
        except Exception as exc:
            print(f'ACP request failed: {type(exc).__name__}',file=sys.stderr)
            if identity is not None:await self.send({'jsonrpc':'2.0','id':identity,'error':{'code':-32603,'message':'Agent request failed; see local diagnostics.'}})

        finally:
            if tracked:tracked.active_requests-=1

    def directory(self,cwd,identity):
        from .native.session_files import project_slug,validate_id
        home=Path(self.config.get('home') or os.environ.get('AMPLIFIER_HOME') or Path.home()/'.amplifier').expanduser().resolve()
        directory=home/'projects'/project_slug(cwd)/'sessions'/validate_id(identity)
        current=home
        for part in directory.relative_to(home).parts:
            current=current/part
            if current.is_symlink():raise ValueError('Native storage directories cannot be symbolic links.')
        return directory

    async def replay(self,session):
        from .history import forward
        for update in forward(self.directory(session.cwd,session.identity)):
            await self.notify('session/update',{'sessionId':session.identity,'update':update})

    async def dispatch(self,method,params):
        if method=='initialize':
            if self.initialized:raise RpcError(-32600,'Connection already initialized.')
            self.initialized=True
            meta=(params.get('clientCapabilities') or {}).get('_meta') or {}
            host=meta.get(HOST,{})
            self.host_info=host if isinstance(host,dict) and host.get('version')==1 else {}
            features=self.host_info.get('features',{})
            app=self.host_info.get('appControl',{})
            if not isinstance(features,dict) or any(not isinstance(value,bool) for value in features.values()):raise RpcError(-32602,'Host feature flags must be booleans.')
            if not isinstance(app,dict) or not isinstance(app.get('operations',[]),list) or any(not isinstance(op,str) for op in app.get('operations',[])):raise RpcError(-32602,'Host appControl requires an operation list.')
            if 'guidance' in app and not isinstance(app['guidance'],str):raise RpcError(-32602,'Host guidance must be text.')
            if len(json.dumps(self.host_info).encode())>32768:raise RpcError(-32602,'Host capability metadata exceeds 32 KiB.')
            self.extensions={key for key in (NATIVE,HOST) if isinstance(meta.get(key),dict) and meta[key].get('version')==1}
            return {'protocolVersion':1,'agentInfo':{'name':'amplifier-acp','title':'Amplifier','version':__version__},
                'agentCapabilities':{'loadSession':True,'sessionCapabilities':{'resume':{},'close':{}},
                    'promptCapabilities':{'image':True,'audio':False,'embeddedContext':True},
                    '_meta':{NATIVE:{'version':1,'history':{'version':1,'paging':'complete-turns','passive':True,'passiveInspect':True},'scheduledTurns':{'version':1,'completion':'settled'},'questionTurns':{'version':1,'completion':'settled','active':False},'observationTurns':{'version':1,'completion':'settled','active':False},'configurationClone':{'version':1},'taskState':{'version':1,'passive':True},'activeSteering':{'version':1,'mode':'request_boundary','cancellable':False,'runtimeNegotiated':True},'lifecycle':{'version':1,'gracefulRetire':True},'relocation':{'version':1,'passiveInspection':True},**({'admin':{'version':1,**({'generations':{'version':1}} if self.config.get('adminGenerations') else {})}} if self.config.get('adminWorkspaceRoots') else {})},HOST:{'version':1}}},'authMethods':[]}
        if not self.initialized:raise RpcError(-32600,'Initialize this connection first.')
        if method in {'_amplifier/relocation','_amplifier/relocation/reconcile'}:
            if NATIVE not in self.extensions:raise RpcError(-32601,'Native extension was not negotiated.')
            from .relocation import passive
            return passive(self,params,reconcile=method.endswith('/reconcile'))
        if method=='_amplifier/admin':
            if NATIVE not in self.extensions or not self.config.get('adminWorkspaceRoots'):raise RpcError(-32601,'Native administration was not negotiated or launcher-enabled.')
            from .admin import Administration
            if not hasattr(self,'admin'):self.admin=Administration(self.config)
            return await self.admin.perform(params.get('cwd'),params.get('operation'),params.get('args',{}))
        if method=='_amplifier/steering':
            if NATIVE not in self.extensions:raise RpcError(-32601,'Native extension was not negotiated.')
            cwd=Path(params.get('cwd',''))
            if not cwd.is_absolute() or not cwd.is_dir():raise RpcError(-32602,'Existing native history workspace required.')
            directory=self.directory(str(cwd.resolve()),params.get('sessionId'))
            if not directory.is_dir():raise RpcError(-32602,'Native session does not exist in this workspace.')
            from .steering import Receipts
            from .relocation import home
            return {'receipt':Receipts(home(self),params['sessionId']).read(params.get('inputId'))}
        if method=='_amplifier/task/inspect':
            if NATIVE not in self.extensions:raise RpcError(-32601,'Native extension was not negotiated.')
            from .passive_task import inspect
            return inspect(self,params)
        if method in {'_amplifier/history','_amplifier/history/inspect'}:
            if NATIVE not in self.extensions:raise RpcError(-32601,'Native extension was not negotiated.')
            cwd=Path(params.get('cwd','')).expanduser()
            if not cwd.is_absolute() or not cwd.is_dir():raise RpcError(-32602,'cwd must be an existing absolute directory.')
            from .history import page,inspect
            directory=self.directory(str(cwd.resolve()),params.get('sessionId'))
            if method.endswith('/inspect'):return inspect(directory)
            return page(directory,cursor=params.get('cursor'),limit=params.get('limit',50),expected_revision=params.get('expectedRevision'))
        if method=='_amplifier/session/new':
            if NATIVE not in self.extensions:raise RpcError(-32601,'Native extension was not negotiated.')
            from .configuration_clone import prepare
            identity,bundle=prepare(self,params)
            session=Session(self,identity,str(Path(params['cwd']).resolve()));session.bundle_override=bundle
            self.sessions[identity]=session
            from .mcp import validate_servers
            session.mcp_servers=validate_servers(params.get('mcpServers',[]))
            try:await session.start()
            except BaseException:self.sessions.pop(identity,None);raise
            from .configuration_clone import confirmed
            confirmed(self,identity)
            return {'sessionId':identity,'configOptions':session.config_options,'_meta':{NATIVE:{'nativeSessionId':identity,'agentKind':'amplifier-core-foundation','creationConfirmed':True,'configurationHash':params['source']['configurationHash'],'historyCwd':session.cwd,'executionDirectory':session.execution_directory,'executionRevision':0}}}
        if method in {'session/new','session/load','session/resume'}:
            requested=Path(params.get('cwd','')).expanduser()
            native_meta=(params.get('_meta') or {}).get(NATIVE,{}) if NATIVE in self.extensions else {}
            cwd=Path(native_meta.get('historyCwd') or requested).expanduser()
            if not cwd.is_absolute() or not cwd.is_dir() or not requested.is_absolute() or not requested.is_dir():raise RpcError(-32602,'cwd/historyCwd must be existing absolute directories.')
            from .mcp import validate_servers
            mcp_servers=validate_servers(params.get('mcpServers',[]))
            if params.get('additionalDirectories'):raise RpcError(-32602,'Additional directories are not advertised; configure native filesystem policy explicitly.')
            identity=str(uuid.uuid4()) if method=='session/new' else params.get('sessionId')
            if not isinstance(identity,str):raise RpcError(-32602,'sessionId is required.')
            directory=self.directory(str(cwd.resolve()),identity)
            if method!='session/new' and not any((directory/name).is_file() for name in ('transcript.jsonl','transcript.jsonl.backup')):
                raise RpcError(-32000,'Native session was not found for this cwd. No work was replayed.',{'compatibility':'unsupported','reason':'missing-native-history'})
            if identity in self.sessions:
                session=self.sessions[identity]
                if session.cwd!=str(cwd.resolve()):raise RpcError(-32602,'Native session is bound to another workspace.')
            else:
                if len(self.sessions)>=self.config.get('maxSessions',32):raise RpcError(-32000,'Connection session capacity reached; close an idle session first.')
                session=Session(self,identity,str(cwd.resolve()));self.sessions[identity]=session
            if session.worker and session.mcp_servers!=mcp_servers:
                if session.prompt and not session.prompt.done():raise RpcError(-32000,'Cannot change MCP connections during an active turn.')
                await session.worker.close()
            session.mcp_servers=mcp_servers
            if native_meta.get('historyCwd'):
                from .relocation import state
                if state(self,identity,session.cwd)['executionDirectory']!=str(requested.resolve()):raise RpcError(-32602,'Requested execution cwd does not match native saved location; inspect relocation metadata.')
            try:await session.start()
            except BaseException:self.sessions.pop(identity,None);raise
            if native_meta.get('historyCwd') and str(requested.resolve())!=session.execution_directory:
                await session.close();raise RpcError(-32602,'Requested execution cwd does not match native saved location; inspect relocation metadata.')
            if method=='session/load':await self.replay(session)
            from .relocation import state
            location=state(self,identity,session.cwd)
            result={'configOptions':session.config_options,'_meta':{NATIVE:{'nativeSessionId':identity,'agentKind':'amplifier-core-foundation','historyCwd':session.cwd,'executionDirectory':session.execution_directory,'executionRevision':location['executionRevision']}}}
            if method=='session/new':result['sessionId']=identity
            return result
        identity=params.get('sessionId');session=self.sessions.get(identity)
        if not session:raise RpcError(-32602,'Session is not loaded on this connection.')
        if method=='_amplifier/retire':
            if NATIVE not in self.extensions:raise RpcError(-32601,'Native extension was not negotiated.')
            return await session.retire()
        if method!='session/close' and (session.retiring or session.retired):raise RpcError(-32008,'Native session handle has retired; reopen it explicitly without replay.')
        if method=='session/prompt':
            blocks=params.get('prompt')
            if not isinstance(blocks,list):raise RpcError(-32602,'prompt must be a content block array.')
            return await session.send(blocks,params.get('_meta',{}))
        if method=='session/cancel':await session.cancel();return None
        if method=='session/close':await session.close();self.sessions.pop(identity,None);return {}
        if method=='session/set_config_option':return await session.configure(params.get('configId'),params.get('value'))
        if method=='session/set_mode':
            await session.configure('mode',params.get('modeId'));return {}
        if method=='_amplifier/native':
            if NATIVE not in self.extensions:raise RpcError(-32601,'Native extension was not negotiated.')
            operation=params.get('operation');args=params.get('args',{})
            if not isinstance(operation,str) or not isinstance(args,dict):raise RpcError(-32602,'Native operation and object args are required.')
            require_existing=((params.get('_meta') or {}).get(NATIVE) or {}).get('requireExisting',False)
            if require_existing and (not session.worker or session.worker.closed or not session.worker.process or session.worker.process.returncode is not None or session.cancelled):
                raise RpcError(-32008,'The original native worker is unavailable; no process was started.',{'executed':False,'replayed':False})
            if operation=='session.relocate':
                from .relocation import relocate
                return await relocate(session,args)
            if operation=='session.relocation.inspect':
                from .relocation import state
                return state(self,identity,session.cwd)
            if operation in {'schedule.submit','question.submit','observation.submit'}:return await session.settled_input(operation,args,require_existing=require_existing)
            if operation=='steer':raise RpcError(-32601,'Unanchored root steering is unavailable over ACP; use negotiated steer.submit.')
            if operation=='steer.submit':
                from .steering import submit
                return await submit(session,args)
            await session.start()
            if operation=='configuration.snapshot':
                if session.lock.locked():raise RpcError(-32000,'Configuration snapshot requires an idle session.')
                result=await session.worker.control(operation,{'bundle':session.report.get('bundle'),'historyCwd':session.cwd})
                return result
            if operation=='capabilities':
                return {'version':1,'agentKind':'amplifier-core-foundation','report':session.report,'hostBridge':HOST in self.extensions,'hostCapabilities':self.host_info if HOST in self.extensions else {},
                    'controls':NATIVE_CONTROLS,'workerOperations':['worker.steer','worker.message','worker.stop']}
            if operation in {'worker.steer','worker.message','worker.stop'}:
                return await session.worker.command(operation,**args)
            if operation not in NATIVE_CONTROLS:raise RpcError(-32601,'Unknown native operation.')
            if operation=='configuration.cancel':
                if session.config_applying:raise RpcError(-32000,'Configuration changes are already applying.')
                if session.config_cancel:session.config_cancel.set()
                return {'cancelled':session.config_cancel is not None}
            result=await session.apply_configuration(args) if operation=='configuration.apply' else await session.worker.control(operation,args)
            if operation in {'configuration.apply','bundle.switch','provider.select','provider.reset','mode.set','mode.clear'}:
                if operation in {'provider.select','provider.reset'}:session.report['selection']=result.get('selection');session.report['effective_selection']=None
                await session.refresh_options()
            return result
        raise RpcError(-32601,'Method is not supported.')

    async def run(self):
        reader=asyncio.StreamReader(limit=MAX_LINE+1);protocol=asyncio.StreamReaderProtocol(reader)
        transport,_=await asyncio.get_running_loop().connect_read_pipe(lambda:protocol,sys.stdin)
        try:
            while line:=await reader.readline():
                try:row=json.loads(line)
                except ValueError:
                    await self.send({'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':'Parse error'}});continue
                self.track(self.handle(row))
        finally:
            transport.close()
            if hasattr(self,'admin'):await self.admin.close()
            # A lost client means cancellation, never automatic replay on reconnect.
            for session in list(self.sessions.values()):await session.close()
            for future in self.pending.values():
                if not future.done():future.cancel()
            for task in self.tasks:task.cancel()
            await asyncio.gather(*self.tasks,return_exceptions=True)

NATIVE_CONTROLS=['session.relocate','session.relocation.inspect','configuration.snapshot','configuration.inspect','configuration.exportResources','configuration.providers','configuration.providerModels',
    'configuration.providerTest','configuration.providerLogin','configuration.providerLoginStatus','configuration.providerLoginCancel','configuration.cancel','configuration.apply','configuration.toggle','catalog.inspect','usage.inspect',
    'context.clear','goals.get','goals.set','goals.clear','budget.get','budget.set','provider.reset','provider.select','tool.invoke',
    'voice.transcript.record','mode.list','mode.set','mode.clear','native.status','native.compact','history.snapshot','history.edit','bundle.preview','bundle.switch',
    'session.naming','steer.submit','question.submit','schedule.submit','observation.submit','memory.consolidate','operations.submit','operations.cancel','operations.write',
    'kernels.create','kernels.execute','kernels.interrupt','kernels.reset','kernels.close','capacity.get','capacity.set','capacity.quota',
    'task.get','task.create','task.update','task.pause','task.resume','task.block','task.complete']

def main():
    parser=argparse.ArgumentParser(description='Modular Amplifier ACP v1 agent')
    parser.add_argument('--config',type=Path)
    args=parser.parse_args();config=json.loads(args.config.read_text()) if args.config else {}
    if not isinstance(config,dict):parser.error('config must be a JSON object')
    # Reserve wire output before any native libraries can print to stdout.
    wire=os.fdopen(os.dup(sys.stdout.fileno()),'w',buffering=1)
    os.dup2(sys.stderr.fileno(),sys.stdout.fileno());sys.stdout=sys.stderr
    asyncio.run(Server(config,wire).run())
