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
from .transfer_lifecycle import managed_probe

NATIVE='amplifier.dev/native'
HOST='amplifier.dev/host'
MAX_LINE=48_000_000

def _host_runtime_retention(host):
    # The spawning host asserts ownership over this private ACP connection.
    # Unsupported or ambiguous offers retain the standalone timer.
    if type(host) is not dict or type(host.get('version')) is not int or host['version'] != 1:return False
    offer=host.get('runtimeRetention')
    return (type(offer) is dict and set(offer)=={'version','owner','lifetime'}
            and type(offer['version']) is int and offer['version']==1
            and offer['owner']=='host' and offer['lifetime']=='connection')

from amplifier_acp import preparation_policy as runtime_preparation

class RpcError(Exception):
    def __init__(self,code,message,data=None):
        self.code,self.message,self.data=code,message,data
        super().__init__(message)

class Session:
    def __init__(self,server,identity,cwd):
        self.server,self.identity,self.cwd=server,identity,cwd
        self.bundle_override=None;self.execution_directory=cwd;self.relocating=False
        self.worker=None; self.prompt=None; self.input_id=None; self.cancelled=False; self.prompt_calls={}
        self.streamed=False; self.had_deltas=False; self.last_model_id=None; self.awaiting_children=False; self.tools=set(); self.lock=asyncio.Lock();self.report={}
        from .steering import Receipts
        from .relocation import home
        from .coordination import Store
        self.coordination=Store(home(server),identity);self.coordination_lock=asyncio.Lock()
        self.steering=Receipts(home(server),identity);self.steering_lock=asyncio.Lock();self.generation_id=None
        self.config_options=[];self.mcp_servers=[];self.idle_task=None;self.start_lock=asyncio.Lock();self.config_cancel=None;self.config_applying=False;self.config_command=None;self.retiring=False;self.retired=False;self.retiring_worker=None;self.active_requests=0;self.worker_retirement_pending=None

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
        from .native.history_revision import guard_context_clear
        from .relocation import home
        guard_context_clear(home(self.server), self.identity)
        from .session_lifecycle import guard_start
        from .managed_session import runtime_scope
        managed=runtime_scope(self.cwd,self.identity,self.server.config)
        fork_bundle=guard_start(self.server,self.identity,self.cwd)
        if managed:self.bundle_override=managed['result']['bundle']
        if fork_bundle:self.bundle_override=fork_bundle
        if self.retiring or self.retired:raise RpcError(-32008,'Native session is retiring; this handle cannot accept new work.')
        if not self.config_applying:self.restore_configuration()
        if not self.relocating:
            from .relocation import state
            location=state(self.server,self.identity,self.cwd)
            if location['blocked']:raise RpcError(-32000,'Native relocation outcome is unresolved; inspect its receipt before execution.',{'outcome':'unknown','replayed':False})
            self.execution_directory=location['executionDirectory']
        if self.worker and (self.worker.closed or self.worker.transport_closed or self.cancelled):await self.worker.close()
        if self.worker and self.worker.process and self.worker.process.returncode is None:return
        config={**self.server.config,'executionDirectory':self.execution_directory,**({'cloneBundle':self.bundle_override} if self.bundle_override else {})}
        self.coordination.lost()
        self.worker=NativeWorker(self.identity,self.cwd,config,self.observe,self.bridge)
        self.report=await self.worker.start(self.server.host_info if HOST in self.server.extensions else None,self.mcp_servers)
        self.cancelled=False
        if await self.refresh_options(notify=False):
            await self.update('config_option_update',configOptions=self.config_options)
        self.schedule_retirement()

    def schedule_retirement(self):
        if self.idle_task and not self.idle_task.done():self.idle_task.cancel()
        if self.server.host_runtime_retention:return
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

    async def publish_title(self):
        # The event is only an invalidation. A later manual edit remains the
        # authority even when an earlier generated-name notification is delayed.
        from .native.naming import read
        try:title=read(self.server.directory(self.cwd,self.identity)).get('name')
        except (OSError,ValueError,TypeError):return
        if isinstance(title,str) and 0<len(title)<=200:
            await self.update('session_info_update',title=title)

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
        if self.coordination.observe(row) and NATIVE in self.server.extensions:
            await self.server.notify('_amplifier/event',{'sessionId':self.identity,'event':{'type':'workers.changed'}})
        root=row.get('sessionId') or row.get('session_id') or self.identity
        is_root=root==self.identity
        if (is_root and kind=='session.naming' and isinstance(row.get('name'),str)
                and 0<len(row['name'])<=200 and type(row.get('nameRevision')) is int and row['nameRevision']>=0):
            await self.publish_title()
        if is_root and kind in {'generation.failed','generation.started'}:
            from .lifecycle_failure import save as save_failure,supersede
            if kind=='generation.failed':save_failure(self.server,self.identity,row,self.input_id)
            else:supersede(self.server,self.identity)
        if is_root and kind=='runtime.error' and row.get('phase')=='worker_startup' and row.get('inputSubmitted') is False:
            from .lifecycle_failure import save_startup
            save_startup(self.server,self.identity,row)
        if is_root and kind=='runtime.ready':
            from .lifecycle_failure import supersede
            supersede(self.server,self.identity,source='recorded-native-startup')
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
            safe={k:v for k,v in row.items() if k not in {'prompt','options','error','report','_input_outcomes'}}
            if kind=='runtime.ready' and isinstance(row.get('report'),dict) and 'publicMessagePreservation' in row['report']:
                safe['report']={'publicMessagePreservation':row['report']['publicMessagePreservation']}
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
            if self.prompt and not self.prompt.done() and event.get('kind')=='llm' and event.get('lifecycle')!='background' and event.get('id'):
                self.prompt_calls[event['id']]=event.get('usage',{})
            if event.get('kind')=='llm' and event.get('phase')=='running' and event.get('lifecycle')!='background' and event.get('sessionId')==self.identity and event.get('id')!=self.last_model_id:
                self.last_model_id=event.get('id');self.streamed=False;self.had_deltas=False
            if event.get('kind')=='llm' and NATIVE in self.server.extensions:
                await self.server.notify('_amplifier/event',{'sessionId':self.identity,'event':{'type':'execution.event','event':event,'activeInputId':self.input_id}})
            if event.get('kind')=='tool':
                identity=event.get('toolCallId') or event.get('id')
                if not identity:return
                # Child IDs are scoped so concurrent delegates cannot collide.
                identity=str(event.get('sessionId') or self.identity)+':'+str(identity)
                status={'running':'in_progress','completed':'completed','error':'failed','cancelled':'failed'}.get(event.get('phase'),'pending')
                data={'toolCallId':identity,'status':status}
                if 'input' in event:data['rawInput']=event['input']
                if 'output' in event:
                    data['rawOutput']=event['output']
                    output=event['output']
                    text=output if isinstance(output,str) else json.dumps(output,ensure_ascii=True,separators=(',',':'))
                    data['content']=[{'type':'content','content':{'type':'text','text':text}}]
                if event.get('label') == 'image_generate' and event.get('phase') == 'completed':
                    from .image_artifacts import deliver
                    delivery = await deliver(self, event)
                    if delivery:
                        data.setdefault('content', []).append({'type':'content','content':{'type':'text','text':delivery}})
                if identity not in self.tools:
                    self.tools.add(identity);data.update(title=event.get('label','Tool'),kind='other')
                    await self.update('tool_call',**data)
                else:
                    await self.update('tool_call_update',**data)
                if event.get('sessionId') == self.identity and event.get('label') == 'todo' and event.get('phase') == 'completed':
                    from .plan import accepted_plan
                    plan = event.get('plan') if 'plan' in event else accepted_plan(event.get('input'), event.get('output'))
                    if plan is not None:
                        await self.update('plan', **{key: value for key, value in plan.items() if key != 'sessionUpdate'})
        elif kind in {'generation.finished','generation.failed','generation.detached'} and is_root:
            if not self.prompt or self.prompt.done():return
            ids=row.get('input_ids',[])
            if self.input_id and ids and self.input_id not in ids and not self.awaiting_children:return
            if kind=='generation.finished' and row.get('active_job_ids'):
                self.awaiting_children=True
                return
            if kind=='generation.finished':
                if not self.streamed and row.get('text'):await self.update('agent_message_chunk',content={'type':'text','text':row['text']})
                from .prompt_usage import prompt_usage
                usage=prompt_usage(self.prompt_calls)
                self.prompt.set_result({'stopReason':'cancelled' if self.cancelled else 'end_turn',**({'usage':usage} if usage is not None else {})})
            elif kind=='generation.detached' and self.cancelled:self.prompt.set_result({'stopReason':'cancelled'})
            else:
                data={k:row[k] for k in ('error_type','error_category','retryable','cancellation') if k in row}
                if NATIVE in self.server.extensions:
                    from .input_outcome import KEY,Outcomes,validate
                    from .relocation import home
                    candidate=(row.get('_input_outcomes') or {}).get(self.input_id)
                    if candidate is not None:
                        validate(candidate,self.identity,self.input_id)
                        # Attach only the exact committed owner record, not a
                        # worker event or current transcript used as substitute.
                        saved=Outcomes(home(self.server),self.identity,self.cwd).read(self.input_id)
                        if saved==candidate:data[KEY]=saved
                failure=NativeError('Native generation failed; no work replayed.',data=data)
                if 'amplifier.dev/inputOutcome' in data:
                    self.pending_input_failure=failure
                else:self.prompt.set_exception(failure)
        elif kind=='runtime.closed':
            if self.prompt and not self.prompt.done():
                if getattr(self,'pending_input_failure',None):self.prompt.set_exception(self.pending_input_failure)
                elif self.cancelled:self.prompt.set_result({'stopReason':'cancelled'})
                else:self.prompt.set_exception(NativeError(row['error'],data=row.get('data')))

    async def refresh_options(self,notify=True):
        state=await self.worker.control('mode.list')
        options=[]
        if state.get('available') and state.get('modes'):
            options.append({'id':'mode','name':'Mode','category':'mode','type':'select',
                'currentValue':state.get('active') or '__default__',
                'options':[{'value':'__default__','name':'Bundle default'}]+[
                    {'value':r['name'],'name':r['name'],'description':r.get('description','')} for r in state['modes']]})
        from .configuration_options import project
        provider_state=await self.worker.control('configuration.providers',{'includeAuthentication':False})
        model_options,self.model_choices,self.effort_choices=project(provider_state)
        self.provider_state=provider_state
        options.extend(model_options)
        self.config_options=options
        if notify:await self.update('config_option_update',configOptions=options)
        return options

    async def configure(self,identity,value):
        if self.lock.locked():raise RpcError(-32000,'Configuration requires an idle turn.',
            {'executed':False,'replayed':False,'reason':'provider-selection-busy'} if identity in {'model','reasoning_effort'} else None)
        async with self.lock:return await self.configure_idle(identity,value)

    async def configure_idle(self,identity,value):
        advertised=getattr(self,'model_choices',{}).get(value) if identity=='model' else None
        await self.start()
        # Validate against current owner state, not a stale client/private draft.
        await self.refresh_options(notify=False)
        # A cold worker loses its in-memory discovery cache. A deliberate
        # selection may re-read that one advertised provider, then validate the
        # token against current choices. Never accept a stale choice blindly.
        if advertised and value not in self.model_choices:
            provider=next((row for row in self.provider_state.get('providers',[]) if row.get('id')==advertised['instance']),None)
            if provider and provider.get('modelCatalog',{}).get('state')=='not-loaded':
                await self.worker.control('configuration.providerModels',{'provider':advertised['instance']})
                await self.refresh_options(notify=False)
        option=next((r for r in self.config_options if r['id']==identity),None)
        choices=[item for row in (option or {}).get('options',[]) for item in row.get('options',[row])]
        if not option or value not in {r['value'] for r in choices}:raise RpcError(-32602,'Unknown session configuration option or value.')
        if identity=='mode':await self.worker.control('mode.clear' if value=='__default__' else 'mode.set',{} if value=='__default__' else {'name':value})
        elif identity=='model':
            if value=='__default__':await self.worker.control('provider.reset')
            else:
                selected=self.model_choices[value]
                effective=self.provider_state.get('selection') or self.provider_state.get('effective') or {}
                # Re-selecting the current pair is a no-op, not an effort reset.
                if not self.provider_state.get('pinned') or any(selected[k]!=effective.get(k) for k in ('instance','model')):
                    await self.worker.control('provider.select',selected)
        elif identity=='reasoning_effort':
            if value=='__default__':
                effective=self.provider_state.get('selection') or self.provider_state.get('effective') or {}
                selected={key:effective[key] for key in ('instance','model')}
            else:selected=self.effort_choices[value]
            effective=self.provider_state.get('selection') or self.provider_state.get('effective') or {}
            if any(selected.get(k)!=effective.get(k) for k in ('instance','model','effort')):
                await self.worker.control('provider.select',selected)
        else:raise RpcError(-32602,'Unknown session configuration option.')
        return {'configOptions':await self.refresh_options()}

    async def configuration_control(self,operation,args):
        if self.lock.locked():raise RpcError(-32000,'Configuration requires an idle turn.',
            {'executed':False,'replayed':False,'reason':'provider-selection-busy'} if operation=='provider.select' else None)
        async with self.lock:
            await self.start()
            return await self.worker.control(operation,args)

    def pending_configuration(self):
        if self.config_cancel is None:return None
        return {'phase':'applying' if self.config_applying else 'queued',**({'commandId':self.config_command} if self.config_command else {})}

    async def configuration_pending(self):
        if NATIVE in getattr(self.server,'extensions',set()):
            await self.server.notify('_amplifier/event',{'sessionId':self.identity,'event':{'type':'configuration.pending','pendingConfiguration':self.pending_configuration()}})

    def cancel_configuration(self,args):
        pending=self.pending_configuration()
        if pending is None:return {'cancelled':False,'reason':'not-pending','pendingConfiguration':None}
        if self.config_command and args.get('pendingCommandId')!=self.config_command:
            return {'cancelled':False,'reason':'pending-command-mismatch','pendingConfiguration':pending}
        if self.config_applying:return {'cancelled':False,'reason':'already-applying','pendingConfiguration':pending}
        self.config_cancel.set()
        return {'cancelled':True,'pendingConfiguration':pending}

    async def apply_configuration(self,args):
        expected=args.get('expectedConfigurationRevision')
        if not isinstance(expected,str) or len(expected)!=64 or any(c not in '0123456789abcdef' for c in expected):
            raise RpcError(-32602,'Applying a mount plan requires its inspected expectedConfigurationRevision.')
        if self.config_cancel is not None:raise RpcError(-32000,'A configuration change is already pending.')
        if self.lock.locked() and not args.get('whenIdle'):raise RpcError(-32000,'Configuration requires an idle turn.')
        command=args.get('commandId')
        if command is not None and (not isinstance(command,str) or not command or len(command)>256):raise RpcError(-32602,'Configuration commandId must be a bounded nonempty string.')
        self.config_command=command
        cancel=self.config_cancel=asyncio.Event()
        acquiring=asyncio.create_task(self.lock.acquire());cancelled=asyncio.create_task(cancel.wait())
        held=False
        try:
            await self.configuration_pending()
            await asyncio.wait({acquiring,cancelled},return_when=asyncio.FIRST_COMPLETED)
            held=acquiring.done() and not acquiring.cancelled() and acquiring.result()
            if cancel.is_set():return {'cancelled':True,'applied':False}
            if not held:await acquiring;held=True
            self.config_applying=True
            await self.configuration_pending()
            current=await self.worker.control('configuration.inspect')
            if current.get('configurationRevision')!=expected:
                raise RpcError(-32009,'Configuration changed; inspect and review the current plan before applying this draft.',{'reason':'configuration-conflict','configurationRevision':current.get('configurationRevision'),'executed':False,'replayed':False})
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
                if isinstance(error,NativeError) and error.data.get('reason')=='configuration-conflict':
                    journal.unlink(missing_ok=True)
                    raise RpcError(-32009,str(error),error.data) from error
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
            self.config_cancel=None;self.config_applying=False;self.config_command=None
            await self.configuration_pending()

    def reject_failed_input(self, identity):
        from .input_outcome import KEY, Outcomes
        from .relocation import home
        saved = Outcomes(home(self.server), self.identity, self.cwd).read(identity)
        if saved is not None:
            raise NativeError('This original input already failed; it was not replayed.',
                data={KEY: saved} if NATIVE in self.server.extensions else {'outcome': 'already-admitted', 'replayed': False})

    async def send(self,blocks,meta):
        if self.lock.locked():raise RpcError(-32000,'A prompt is already active for this session.')
        async with self.lock:
            command=(meta or {}).get('amplifier.dev/command') or {}
            command_id=command.get('commandId') if NATIVE in self.server.extensions else None
            if command_id is not None and (not isinstance(command_id,str) or not command_id or len(command_id)>128):
                raise RpcError(-32602,'commandId must contain 1 to 128 characters.')
            self.input_id=command_id or str(uuid.uuid4())
            from .public_message import transport
            public_message=transport(self.server,meta,self.input_id)
            from .content import prompt_content
            value,attachments=prompt_content(self.server.config,blocks)
            provenance=(meta or {}).get('amplifier.dev/input') if NATIVE in self.server.extensions else None
            if provenance is not None:
                from .native.input_provenance import validate_prompt
                try: validate_prompt(provenance)
                except ValueError as error: raise RpcError(-32602,str(error)) from error
            self.reject_failed_input(self.input_id)
            reply=(meta or {}).get('amplifier.dev/messageReply')
            quote=None
            if command_id:
                from .message_metadata import guard_input_identity
                from .relocation import home
                guard_input_identity(home(self.server)/'sessions'/self.identity/'message-interactions.sqlite3',command_id,
                    reference=reply,input_payload={'text':value,'attachments':attachments,'provenance':provenance},
                    actor_id=self.server.message_principal(self.identity) if self.server.message_principal else None)
            if reply is not None:
                if NATIVE not in self.server.extensions or not self.server.message_principal or not command_id:
                    raise RpcError(-32602,'Quoted input requires negotiated message support and original command identity.',{'executed':False,'replayed':False})
                from .message_metadata import MessageMetadata,reserve_quote
                from .relocation import home
                from .native.history_revision import context_head
                quote=reserve_quote(MessageMetadata(home(self.server),self.identity,actor_id=self.server.message_principal(self.identity)),
                    self.server.directory(self.cwd,self.identity),reply,input_id=self.input_id,
                    input_payload={'text':value,'attachments':attachments,'provenance':provenance},
                    epoch=context_head(home(self.server),self.identity,self.cwd))
            await self.start()
            if command_id:
                previous=await self.worker.command('delivery',input_id=command_id)
                if previous.get('delivery')=='accepted':
                    raise RpcError(-32001,'This input was already admitted. Read native history; it was not replayed.',{'outcome':'already-admitted','commandId':command_id,'replayed':False})
            self.cancelled=False;self.streamed=False;self.had_deltas=False;self.last_model_id=None;self.awaiting_children=False;self.pending_input_failure=None
            self.prompt_calls={}
            self.prompt=asyncio.get_running_loop().create_future()
            try:
                admitted=await self.worker.command('send',public_input_transport='acp',text=value,input_id=self.input_id,attachments=attachments,**({'reply_context':quote} if quote is not None else {}),**({'input_provenance':provenance} if provenance is not None else {}),**({'public_message':public_message} if public_message is not None else {}))
                if quote is not None and admitted.get('accepted') is True:
                    from .message_metadata import quote_admitted
                    quote_admitted(MessageMetadata(home(self.server),self.identity,actor_id=self.server.message_principal(self.identity)),self.input_id)
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
        if operation not in {'observation.submit','peer.submit'} and (not isinstance(args.get('text'),str) or not args['text'].strip()):raise RpcError(-32602,'Input text is required.')
        if operation=='schedule.submit' and args.get('kind') not in {'task','monitor'}:raise RpcError(-32602,'Scheduled kind must be task or monitor.')
        resident=self.worker if require_existing else None
        def available():
            return resident is not None and self.worker is resident and not resident.closed and resident.process and resident.process.returncode is None and not self.cancelled and not self.retiring and self.worker_retirement_pending is not resident
        refused={'accepted':False,'executed':False,'reason':'The original native worker is unavailable or retiring; no runtime was started.'}
        async with self.lock:
            self.reject_failed_input(identity)
            from .message_metadata import guard_input_identity
            from .relocation import home
            guard_input_identity(home(self.server)/'sessions'/self.identity/'message-interactions.sqlite3',identity)
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
            self.input_id=identity;self.cancelled=False;self.streamed=False;self.had_deltas=False;self.last_model_id=None;self.awaiting_children=False;self.pending_input_failure=None
            self.prompt_calls={}
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

    def retirement_status(self):
        worker=self.retiring_worker
        confirmed=bool((self.retiring or self.retired) and worker is not None and worker is self.worker
            and worker.retirement_confirmed is True and worker.process and worker.process.returncode==0)
        return {'sessionId':self.identity,'retired':confirmed,**({} if confirmed else {'reason':'retirement_unknown' if self.retiring or self.retired else 'not_retiring'})}

    async def retire(self):
        if self.retired:return {'retired':True}
        if self.retiring:
            # A lost caller reply can be reconciled only for the same original
            # worker with its exact retirement acknowledgement and clean exit.
            # Exit/EOF alone never proves quiescence or completes unknown work.
            if self.retirement_status()['retired']:
                self.retired=True
                return {'retired':True}
            return {'retired':False,'reason':'retirement_unknown'}
        if self.active_requests or self.lock.locked() or self.config_applying or self.start_lock.locked():return {'retired':False,'reason':'active_request'}
        async with self.start_lock:
            self.retiring=True
            if self.idle_task:self.idle_task.cancel()
            worker=self.worker;self.retiring_worker=worker
            try:
                if worker and worker.process and worker.process.returncode is None:
                    result=await worker.command('retire')
                    if not result.get('retired'):
                        self.retiring=False;self.retiring_worker=None;self.schedule_retirement()
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
    def __init__(self,config,output,*,message_principal=None):
        from .preparation_policy import configure as configure_preparation
        configure_preparation(config)
        from .module_sources import configure
        configure(config)
        self.config,self.output=config,output
        self.message_principal=message_principal
        self._host_runtime_retention=False;self._standalone_idle_seconds=None
        # One ACP process owns one configured native home. Passive Foundation
        # fence/lifecycle APIs must resolve the same canonical files as workers.
        for key,variable in (('home','AMPLIFIER_HOME'),('appHome','AMPLIFIER_WEB_HOME')):
            if config.get(key):os.environ[variable]=str(Path(config[key]).expanduser().resolve())
        self.initialized=False;self.extensions=set();self.sessions={};self.pending={};self.tasks=set();self._counter=0

    @property
    def host_runtime_retention(self):
        return self._host_runtime_retention

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
        opened=None
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
            if identity is not None:
                await self.send({'jsonrpc':'2.0','id':identity,'result':value})
                if row['method'] in {'session/new','session/load','session/resume'}:
                    opened=self.sessions.get(value.get('sessionId') or row.get('params',{}).get('sessionId'))
        except RpcError as exc:
            if identity is not None:await self.send({'jsonrpc':'2.0','id':identity,'error':{'code':exc.code,'message':exc.message,**({'data':exc.data} if exc.data is not None else {})}})
        except (NativeError,ValueError,KeyError,OSError) as exc:
            if identity is not None:await self.send({'jsonrpc':'2.0','id':identity,'error':{'code':-32000,'message':str(exc),**({'data':exc.data} if getattr(exc,'data',None) is not None else {})}})
        except Exception as exc:
            print(f'ACP request failed: {type(exc).__name__}',file=sys.stderr)
            if identity is not None:await self.send({'jsonrpc':'2.0','id':identity,'error':{'code':-32603,'message':'Agent request failed; see local diagnostics.'}})

        finally:
            if tracked:tracked.active_requests-=1
        if opened:
            # Publish only after the response exposes the session identity. A
            # projection failure cannot change an already successful response.
            try:await opened.publish_title()
            except Exception as exc:print(f'ACP title publication failed: {type(exc).__name__}',file=sys.stderr)

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
        for update in forward(self.directory(session.cwd,session.identity),app_home=__import__('amplifier_acp.relocation',fromlist=['home']).home(self),history_cwd=session.cwd):
            await self.notify('session/update',{'sessionId':session.identity,'update':update})

    @runtime_preparation.governed
    async def dispatch(self,method,params):
        if method=='initialize' or method in {'_amplifier/admin','_amplifier/admin/lifecycle'} or not self.initialized:
            return await self._dispatch(method,params)
        if method.startswith('_amplifier/') and NATIVE not in self.extensions:
            raise RpcError(-32601,'Native extension was not negotiated.')
        if method in {'_amplifier/bundle/receipt','_amplifier/context/receipt','_amplifier/input/receipt'}:return await self._dispatch(method,params)
        if method=='_amplifier/transfer/lifecycle' and (params.get('operation')=='abortAdmission' or params.get('operation')=='acquire' and isinstance(params.get('args'),dict) and 'ownerId' in params['args']):
            # Only the negotiated distribution authority path owns its own
            # transient exclusive lease. Validate every selector before any
            # outer shared lease could create state or conflict with it.
            from .transfer_admission import admission_request
            if set(params)!={'operation','args'}:raise ValueError('Exact transfer admission request required')
            admission_request(params.get('args'),abort=params['operation']=='abortAdmission')
            return await self._dispatch(method,params)
        from .maintenance_gate import Gate
        # This exact reviewed effect owns an exclusive home gate for its entire
        # joined lifetime; acquiring the ordinary shared gate here would deadlock.
        if method=='_amplifier/session' and params.get('operation')=='managedFiles.dispose':
            return await self._dispatch(method,params)
        with Gate(self.config):return await self._dispatch(method,params)

    async def _dispatch(self,method,params):
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
            binding=meta.get('amplifier.dev/messagePrincipal')
            if binding is not None:
                from .message_metadata import token
                if NATIVE not in self.extensions or not isinstance(binding,dict) or set(binding)!={'version','account'} or type(binding['version']) is not int or binding['version']!=1:
                    raise RpcError(-32602,'Exact native message principal binding required.',{'executed':False,'replayed':False})
                try:account=token(binding['account'])
                except ValueError as error:raise RpcError(-32602,str(error),{'executed':False,'replayed':False}) from error
                if self.message_principal is not None:raise RpcError(-32602,'Trusted message principal is already bound.',{'executed':False,'replayed':False})
                # This is an assertion by the authenticated spawning host over
                # its private stdio connection, not independent authentication.
                # It is immutable and never accepted through per-action args.
                self.message_principal=lambda identity: account
            # Snapshot the offer once; later metadata/action mutations cannot
            # transfer timer ownership or renegotiate an existing connection.
            self._host_runtime_retention=_host_runtime_retention(host)
            if self.host_runtime_retention:self._standalone_idle_seconds=max(1,self.config.get("idleSeconds",60))
            from .managed_disposal import supported as managed_disposal_supported
            return {'protocolVersion':1,'agentInfo':{'name':'amplifier-acp','title':'Amplifier','version':__version__},
                'agentCapabilities':{'loadSession':True,'sessionCapabilities':{'resume':{},'close':{},'delete':{}},
                    'promptCapabilities':{'image':True,'audio':False,'embeddedContext':True},
                    '_meta':{NATIVE:{'version':1,**runtime_preparation.metadata(),'publicMessagePreservation':__import__('amplifier_acp.public_message',fromlist=['CAPABILITY']).CAPABILITY,**({'messageInteractions':{'version':1,'methods':['reaction','read','receipt'],'quoteMeta':'amplifier.dev/messageReply','maxQuoteChars':4000,'privateReplySelection':True}} if self.message_principal else {}),**({'initialSelection':__import__('amplifier_acp.initial_selection',fromlist=['CAPABILITY']).CAPABILITY,'managedCreation':{'version':1,'passive':True,'configurationScope':'global-and-session','typedSelection':True,'historyCopy':{'version':1,'freshAllocation':True},'configurationCopy':{'version':1,'passive':True},'defaults':{'version':1,'globalOnly':True},'models':{'version':1,'globalOnly':True,'maxPageSize':100,'maxModels':1000,'maxBytes':524288}}} if self.config.get('managedSessionRoots') else {}),'inputOutcome':__import__('amplifier_acp.input_outcome',fromlist=['CAPABILITY']).CAPABILITY,'contextClear':__import__('amplifier_acp.native.history_revision',fromlist=['CONTEXT_CLEAR']).CONTEXT_CLEAR,'naming':__import__('amplifier_acp.naming_commands',fromlist=['CAPABILITY']).CAPABILITY,'bundleCommands':__import__('amplifier_acp.bundle_commands',fromlist=['CAPABILITY']).CAPABILITY,'modelCalls':{'version':1,'passive':True,'recordedDetails':True},'history':{'version':1,'paging':'complete-turns','passive':True,'exactMessageRead':{'version':1,'scan':'stream','maxRecordBytes':67108864,'maxMessageBytes':65536},'passiveInspect':True,'missingWorkspaceRead':{'version':1,'exactMetadata':True,'execution':False}},'scheduledTurns':{'version':1,'completion':'settled'},'questionTurns':{'version':1,'completion':'settled','active':False},'observationTurns':{'version':1,'completion':'settled','active':False},'peerTurns':{'version':1,'completion':'settled','guarded':True,'results':__import__('amplifier_acp.native.peer_results',fromlist=['CAPABILITY']).CAPABILITY,'modes':['queue','steer'],'passiveNotifications':{'version':1,'delivery':'provider-request'}},'configurationClone':{'version':1},'taskState':{'version':1,'passive':True,'capacity':{'version':1,'passivePolicy':True,'admission':'native-owner-snapshot'}},'workers':{'version':1,'passiveInspection':True,'residentControls':True},'runtimeInspection':{'version':1,'residentOnly':True,'passiveStatus':True},'historyEdits':{'version':1,'completion':'settled','active':False,'originalContextVersion':1},'sessionLifecycle':{'version':1,'passive':True,'deletion':'preserve-history',**({'managedFiles':{'version':1,'preservesCanonical':True,'maxEntries':10000,'maxBytes':268435456}} if self.config.get('managedSessionRoots') and managed_disposal_supported() else {}),'retention':{'version':1,'preservesCanonical':True},**({'retiredCopies':{'version':1,'duplicateFilesOnly':True,'retirementAuthority':'launcher-assertion'}} if self.config.get('retiredSessionCopyRoots') else {}),'recoveryCopy':{'version':1,'mode':'readable-history'},'bundleFork':{'version':1,'configuredOnly':True},'descendantGuard':{'version':1,'maxSessions':100,'coverage':'indexed-and-native-worker-families'}},'activeSteering':{'version':1,'mode':'request_boundary','cancellable':False,'runtimeNegotiated':True,'content':{'version':1,'attachments':True,'quotedReplies':bool(self.message_principal)}},'lifecycle':{'version':1,'gracefulRetire':True,'retirementStatus':{'version':1,'method':'_amplifier/retire/status','passive':True},**({'runtimeRetention':{'version':1,'owner':'host','lifetime':'connection','standaloneIdleSeconds':self._standalone_idle_seconds}} if self.host_runtime_retention else {})},'relocation':{'version':1,'passiveInspection':True},**({'transfer':{'version':1,'passiveInspection':True,'lifecycle':{'version':1,'heldIntake':True,'serviceStop':{'version':1},'nativeAdminWriter':managed_probe(self.config),'admissionAbort':{'version':1}}}} if self.config.get('transferAuthorityDirectory') and self.config.get('transferWorkspaceRoots') else {}),**({'admin':{'version':1,'imageSetup':__import__('amplifier_acp.image_setup_contract',fromlist=['CAPABILITY']).CAPABILITY,'imagePreferences':__import__('amplifier_acp.image_setup_contract',fromlist=['PREFERENCES_CAPABILITY']).PREFERENCES_CAPABILITY,**({'providerRecording':__import__('amplifier_acp.provider_recording_policy',fromlist=['CAPABILITY']).CAPABILITY} if self.config.get('adminProviderRecording') is True else {}),'providerSignIn':{'version':1,'workspaceBound':True,'scopedAttempts':True},**({'permissions':{'version':1,'scopes':['global','project','local'],'cas':True,'receipt':True,'pathsPerList':64,'pathChars':4096,'requestBytes':262144,'application':'next-idle-execution'}} if self.config.get('adminPermissions') is True else {}),**({'historyImport':{'version':1,'staged':True,'maxBytes':1048576,'chunkBytes':16384}} if self.config.get('adminHistoryImport') is True else {}),'voice':{'version':1,'configuration':True,'credentials':self.config.get('adminVoiceCredentials') is True,**({'preferences':{'version':1,'cas':True,'receipt':True,'fields':['preferredModel','fallbackModel','voice','interruptions']}} if self.config.get('adminVoicePreferences') is True else {})},'lifecycle':{'version':1,'heldIntake':True,'crossProcessWriterFence':True,'admissionAbort':{'version':1,'durable':True,'receipt':True},'reconcileRelease':{'version':1,'durable':True,'receipt':True},'idleNotification':True,'serviceStop':{'version':1}},**({'generations':{'version':1}} if self.config.get('adminGenerations') else {}),**({'maintenance':{'version':1,**({'bundleReferences':{'version':1,'metadataOnly':True,'previewRequired':True,'receipt':True,'paged':True,'workReplayed':False}} if self.config.get('maintenanceFullNative') is True and self.config.get('maintenanceExternalWriters') in {'foundation-cooperative','stopped'} else {}),'selectedOnly':self.config.get('maintenanceFullNative') is not True,'privateArtifacts':True,**({'captureScope':{'version':1,'connectionScoped':True,'beforePrepare':True,'retainsNativeHome':True,'maxArtifacts':16,'expiry':'connection-lifetime'}} if self.config.get('maintenanceFullNative') is True else {}),'appReset':{'version':1,'parts':['native.app-bundle-default'],'retainedUndo':True,'requiresRecoveryAdminLease':True},'cacheInventory':{'version':1,'cold':True,'paged':True,'clearKinds':['python-bytecode'],'sourceRetirement':False,'requiresRecoveryAdminLease':True},'archivePlans':{'version':1,'batchSize':32,'pagedManifest':True,'nativeStores':{'version':1,'parts':['native-import-records','native-preference-receipts','native-recording-receipts','native-maintenance-records','native-retained-archives'],'requiresRecoveryAdminLease':True},**({'fullNative':{'version':1,'part':'full-native-authority','format':'amplifier-native-authority','coldInventory':True,'externalRootsDeclared':True,'artifactInspect':{'version':1},**({'restore':{'version':1,'newDestinationOnly':True,'pagedManifest':True,'receipt':True,'passiveInspect':True}} if self.config.get('maintenanceRestoreRoots') else {})}} if self.config.get('maintenanceFullNative') is True else {})}}} if self.config.get('adminMaintenance') is True else {})}} if self.config.get('adminWorkspaceRoots') else {})},HOST:{'version':1}}},'authMethods':[]}
        if not self.initialized:raise RpcError(-32600,'Initialize this connection first.')
        if method=='_amplifier/input/receipt':
            from .input_outcome import passive
            return passive(self,params)
        if method=='_amplifier/context/receipt':
            from .native.history_revision import passive_context_receipt
            return passive_context_receipt(self,params)
        if method=='_amplifier/bundle/receipt':
            from .bundle_commands import passive
            return passive(self,params)
        if method=='_amplifier/managed':
            if NATIVE not in self.extensions or not self.config.get('managedSessionRoots'):raise RpcError(-32601,'Managed creation was not launcher-enabled and negotiated.')
            from .managed_session import dispatch
            return await dispatch(self,params)
        if method in {'_amplifier/relocation','_amplifier/relocation/reconcile'}:
            if NATIVE not in self.extensions:raise RpcError(-32601,'Native extension was not negotiated.')
            from .relocation import passive
            return passive(self,params,reconcile=method.endswith('/reconcile'))
        if method in {'_amplifier/transfer','_amplifier/transfer/lifecycle'}:
            if NATIVE not in self.extensions or not self.config.get('transferAuthorityDirectory') or not self.config.get('transferWorkspaceRoots'):raise RpcError(-32601,'Native transfer authority was not launcher-enabled and negotiated.')
            from .transfer_lifecycle import TransferLifecycle
            if not hasattr(self,'transfers'):self.transfers=TransferLifecycle(self)
            if method=='_amplifier/transfer/lifecycle':return self.transfers.control(params.get('operation'),params.get('args',{}))
            loaded=self.sessions.get(params.get('sessionId'))
            if params.get('operation')!='inspect' and loaded and loaded.worker and loaded.worker.process.returncode is None:raise RpcError(-32000,'Retire the resident native worker before transfer fencing.',{'executed':False})
            return await self.transfers.perform(params)
        if method in {'_amplifier/admin','_amplifier/admin/lifecycle'}:
            if NATIVE not in self.extensions or not self.config.get('adminWorkspaceRoots'):raise RpcError(-32601,'Native administration was not negotiated or launcher-enabled.')
            from .admin import Administration
            if not hasattr(self,'admin'):self.admin=Administration(self.config,lambda:self.send({'jsonrpc':'2.0','method':'_amplifier/admin/idle','params':{'version':1}}))
            if method=='_amplifier/admin/lifecycle':
                self.admin.workspace(params.get('cwd'))
                return self.admin.lifecycle.perform(params.get('operation'),params.get('args',{}))
            return await self.admin.perform(params.get('cwd'),params.get('operation'),params.get('args',{}),fence=(params.get('_meta') or {}).get('amplifier.dev/admin'))
        if method=='session/delete':
            identity=params.get('sessionId');loaded=self.sessions.get(identity)
            native_meta=(params.get('_meta') or {}).get(NATIVE,{}) if NATIVE in self.extensions else {}
            cwd=loaded.cwd if loaded else native_meta.get('historyCwd')
            if not cwd:raise RpcError(-32602,'Load this session or supply negotiated native historyCwd; deletion never scans global history.')
            if loaded and (loaded.lock.locked() or loaded.config_cancel is not None or loaded.worker_retirement_pending):raise RpcError(-32000,'Finish native work before removal.',{'executed':False})
            from .session_lifecycle import Lifecycle
            owner=Lifecycle(self,identity,cwd);inspected=await asyncio.to_thread(owner.inspect)
            if inspected['state']['deleted']:return {}
            command=native_meta.get('commandId') or 'acp-delete:'+str(uuid.uuid4())
            await asyncio.to_thread(owner.perform,'delete.standard',{'commandId':command,'expectedHistoryRevision':inspected['historyRevision']})
            return {}
        if method=='_amplifier/history/before':
            if NATIVE not in self.extensions:raise RpcError(-32601,'Native extension was not negotiated.')
            from .history_edit import before
            return await asyncio.to_thread(before,self,params)
        if method=='_amplifier/history/edit':
            if NATIVE not in self.extensions:raise RpcError(-32601,'Native extension was not negotiated.')
            from .history_edit import receipt
            return receipt(self,params)
        if method=='_amplifier/session':
            if NATIVE not in self.extensions:raise RpcError(-32601,'Native extension was not negotiated.')
            from .session_lifecycle import Lifecycle
            operation=params.get('operation');loaded=self.sessions.get(params.get('sessionId'))
            if operation in {'fork','export','delete','restore','retention.hide','legacyCopy.purge','managedFiles.preview','managedFiles.dispose'} and loaded and (loaded.lock.locked() or loaded.config_cancel is not None or loaded.worker_retirement_pending):raise RpcError(-32000,'Native lifecycle mutation requires an idle, settled session.',{'executed':False})
            owner=Lifecycle(self,params.get('sessionId'),params.get('cwd',''))
            if operation.startswith('managedFiles.'):
                from .managed_disposal import dispatch
                result=await dispatch(owner,operation,params.get('args',{}))
            else:result=await asyncio.to_thread(owner.perform,operation,params.get('args',{}))
            if operation=='fork' and result.get('creationConfirmed'):
                await self.notify('_amplifier/event',{'sessionId':owner.identity,'event':{'type':'history.persisted','sessionId':result['sessionId'],'cwd':result['historyCwd'],'parentId':None,'metadataPath':str(self.directory(result['historyCwd'],result['sessionId'])/'metadata.json')}})
            return result
        if method=='_amplifier/steering':
            if NATIVE not in self.extensions:raise RpcError(-32601,'Native extension was not negotiated.')
            cwd=Path(params.get('cwd',''))
            if not cwd.is_absolute() or not cwd.is_dir():raise RpcError(-32602,'Existing native history workspace required.')
            directory=self.directory(str(cwd.resolve()),params.get('sessionId'))
            if not directory.is_dir():raise RpcError(-32602,'Native session does not exist in this workspace.')
            from .steering import Receipts
            from .relocation import home
            return {'receipt':Receipts(home(self),params['sessionId']).read(params.get('inputId'))}
        if method=='_amplifier/naming':
            if NATIVE not in self.extensions:raise RpcError(-32601,'Native extension was not negotiated.')
            from .naming_commands import handle
            return await handle(self,params)
        if method=='_amplifier/runtime':
            if NATIVE not in self.extensions:raise RpcError(-32601,'Native extension was not negotiated.')
            if set(params)-{'sessionId','cwd','cursor','limit','expectedRevision','refresh','surface'}:raise RpcError(-32602,'Unexpected runtime inspection field.')
            cwd=Path(params.get('cwd',''))
            if not cwd.is_absolute() or not cwd.is_dir():raise RpcError(-32602,'Existing native history workspace required.')
            identity=params.get('sessionId');directory=self.directory(str(cwd.resolve()),identity)
            if not (directory/'metadata.json').is_file():raise RpcError(-32602,'Native session identity is unavailable.')
            session=self.sessions.get(identity)
            if session and session.cwd!=str(cwd.resolve()):raise RpcError(-32602,'Native session belongs to another history workspace.')
            from .runtime_inspection import resident
            return await resident(session,{k:v for k,v in params.items() if k not in {'sessionId','cwd'}})
        if method=='_amplifier/workers':
            if NATIVE not in self.extensions:raise RpcError(-32601,'Native extension was not negotiated.')
            from .coordination import inspect
            return inspect(self,params)
        if method=='_amplifier/task/inspect':
            if NATIVE not in self.extensions:raise RpcError(-32601,'Native extension was not negotiated.')
            from .passive_task import inspect
            return inspect(self,params)
        if method in {'_amplifier/message/reaction','_amplifier/message/read','_amplifier/message/receipt'}:
            from .message_metadata import MessageMetadata,handle_message_metadata
            from .relocation import home
            from .native.history_revision import context_head
            identity=params.get('sessionId')
            actor=self.message_principal(identity) if self.message_principal else None
            if not actor:raise RpcError(-32601,'Authenticated native message principal is unavailable.',{'executed':False,'replayed':False})
            identity=params.get('sessionId')
            session=self.sessions.get(identity)
            cwd=session.cwd if session else getattr(self,'message_scopes',{}).get(identity)
            if method.endswith('/reaction') and not cwd:raise RpcError(-32602,'Read the selected canonical history before changing message metadata.',{'executed':False,'replayed':False})
            store=MessageMetadata(home(self),identity,actor_id=actor)
            previous=store.receipt(params['commandId'])['receipt'] if method.endswith('/reaction') and params.get('commandId') else None
            result=handle_message_metadata(store,method,params,
                directory=self.directory(cwd,identity) if method.endswith('/reaction') else None,
                epoch=context_head(home(self),identity,cwd) if method.endswith('/reaction') else None)
            if method.endswith('/reaction') and result['changed'] and previous is None:
                await self.notify('_amplifier/message/update',result)
            return result
        if method=='_amplifier/model-detail':
            if NATIVE not in self.extensions:raise RpcError(-32601,'Native extension was not negotiated.')
            from .model_detail import read
            from .relocation import home
            # Validate the exact native history scope without mounting a worker.
            cwd=Path(params.get('cwd','')).expanduser()
            if not cwd.is_absolute():raise RpcError(-32602,'cwd must be an absolute directory.')
            directory=self.directory(str(cwd.resolve()),params.get('sessionId'))
            if not (directory/'transcript.jsonl').is_file():raise RpcError(-32602,'Exact native history scope required.')
            return read(home(self),params)
        if method in {'_amplifier/history','_amplifier/history/inspect','_amplifier/history/message'}:
            if NATIVE not in self.extensions:raise RpcError(-32601,'Native extension was not negotiated.')
            cwd=Path(params.get('cwd','')).expanduser()
            if not cwd.is_absolute():raise RpcError(-32602,'cwd must be an absolute directory.')
            if 'readOnlyMissingWorkspace' in params and params['readOnlyMissingWorkspace'] is not True:raise RpcError(-32602,'Missing-workspace reads require an explicit true flag.')
            if params.get('readOnlyMissingWorkspace') is True:
                from .missing_workspace_history import validate
                validate(self,str(cwd),params.get('sessionId'))
            elif not cwd.is_dir():
                from .managed_session import history_scope
                if not history_scope(str(cwd),params.get('sessionId'),self.config):raise RpcError(-32602,'Existing or exactly disposed native history workspace required.')
            from .history import page,inspect
            directory=self.directory(str(cwd.resolve()),params.get('sessionId'))
            if method.endswith('/inspect'):return inspect(directory)
            from .relocation import home
            if method.endswith('/message'):
                from .history import exact_message
                return await asyncio.to_thread(exact_message,directory,params.get('messageId'),params.get('expectedRevision'),app_home=home(self),history_cwd=str(cwd.resolve()))
            public_rows=params.get('includePublicRows',False)
            if type(public_rows) is not bool:raise RpcError(-32602,'includePublicRows must be boolean')
            if 'rowLocator' in params:
                if not public_rows or params.get('cursor') is not None:raise RpcError(-32602,'Exact row selection requires public rows and no page cursor')
                from .public_message import read_row
                from .native.history_revision import context_head
                return read_row(directory,params['rowLocator'],epoch=context_head(home(self),directory.name,str(cwd.resolve())),expected_revision=params.get('expectedRevision'))
            result=page(directory,cursor=params.get('cursor'),limit=params.get('limit',50),expected_revision=params.get('expectedRevision'),app_home=home(self),history_cwd=str(cwd.resolve()),include_public_rows=public_rows)
            metadata_path=directory/'metadata.json'
            try:
                if metadata_path.stat().st_size<=262144:
                    metadata=json.loads(metadata_path.read_text())
                    selected=metadata.get('bundle_name') or metadata.get('bundle')
                    if isinstance(selected,str) and 0<len(selected)<=4096:
                        aliases=self.config.get('forkBundles',{})
                        label=next((name for name,value in aliases.items() if value==selected),selected)
                        if '://' not in label and '/' not in label:
                            label=label.replace('-',' ').replace('_',' ').title()
                        else:
                            label='Custom bundle'
                        result['selection']={'version':1,'bundle':selected,'bundleLabel':label}
            except (OSError,ValueError,TypeError):
                pass
            if self.message_principal and params.get('readOnlyMissingWorkspace') is not True:
                scopes=getattr(self,'message_scopes',{})
                identity=params.get('sessionId');canonical=str(cwd.resolve())
                if identity in scopes and scopes[identity]!=canonical:raise RpcError(-32602,'Native message scope changed; original history preserved.')
                scopes.pop(identity,None);scopes[identity]=canonical
                while len(scopes)>128:scopes.pop(next(iter(scopes)))
                self.message_scopes=scopes
            return result
        if method=='_amplifier/initial-selection':
            from . import initial_selection
            if not self.config.get('managedSessionRoots'):raise RpcError(-32601,'Initial selection is not enabled.')
            if set(params)-{'cwd','selection'}:raise initial_selection.Refused('Initial selection accepts only cwd and a typed draft.')
            response,_=await initial_selection.resolve(self.config,params.get('cwd'),params.get('selection',{}))
            return response
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
            return {'sessionId':identity,'configOptions':session.config_options,'_meta':{NATIVE:{'nativeSessionId':identity,'agentKind':'amplifier-core-foundation','creationConfirmed':True,**({'publicMessagePreservation':session.report['publicMessagePreservation']} if 'publicMessagePreservation' in session.report else {}),'configurationHash':params['source']['configurationHash'],'historyCwd':session.cwd,'executionDirectory':session.execution_directory,'executionRevision':0}}}
        if method in {'session/new','session/load','session/resume'}:
            requested=Path(params.get('cwd','')).expanduser()
            from . import initial_selection
            raw_meta=(params.get('_meta') or {}).get(NATIVE,{})
            if not isinstance(raw_meta,dict):raise RpcError(-32602,'Native metadata must be an object.')
            selected=initial_selection.requested(raw_meta.get('selection'),
                supported=bool(self.config.get('managedSessionRoots')),
                negotiated=NATIVE in self.extensions,new=method=='session/new')
            native_meta=raw_meta if NATIVE in self.extensions else {}
            cwd=Path(native_meta.get('historyCwd') or requested).expanduser()
            if not cwd.is_absolute() or not cwd.is_dir() or not requested.is_absolute() or not requested.is_dir():raise RpcError(-32602,'cwd/historyCwd must be existing absolute directories.')
            from .mcp import validate_servers
            mcp_servers=validate_servers(params.get('mcpServers',[]))
            if params.get('additionalDirectories'):raise RpcError(-32602,'Additional directories are not advertised; configure native filesystem policy explicitly.')
            initial_bundle=None
            if selected is not None:
                if len(self.sessions)>=self.config.get('maxSessions',32):raise RpcError(-32000,'Connection session capacity reached; close an idle session first.')
                initial_bundle=await initial_selection.validate(self.config,str(cwd.resolve()),selected)
            identity=str(uuid.uuid4()) if method=='session/new' else params.get('sessionId')
            if not isinstance(identity,str):raise RpcError(-32602,'sessionId is required.')
            from .managed_session import runtime_scope
            runtime_scope(str(cwd.resolve()),identity,self.config)
            directory=self.directory(str(cwd.resolve()),identity)
            if method!='session/new' and not any((directory/name).is_file() for name in ('transcript.jsonl','transcript.jsonl.backup')):
                raise RpcError(-32000,'Native session was not found for this cwd. No work was replayed.',{'compatibility':'unsupported','reason':'missing-native-history'})
            if identity in self.sessions:
                session=self.sessions[identity]
                if session.cwd!=str(cwd.resolve()):raise RpcError(-32602,'Native session is bound to another workspace.')
            else:
                if len(self.sessions)>=self.config.get('maxSessions',32):raise RpcError(-32000,'Connection session capacity reached; close an idle session first.')
                if selected is not None:initial_selection.persist(self.config,identity,selected)
                session=Session(self,identity,str(cwd.resolve()));self.sessions[identity]=session
                if selected is not None:session.bundle_override=initial_bundle
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
            result={'configOptions':session.config_options,'_meta':{NATIVE:{'nativeSessionId':identity,'agentKind':'amplifier-core-foundation','historyCwd':session.cwd,'executionDirectory':session.execution_directory,'executionRevision':location['executionRevision'],**({'publicMessagePreservation':session.report['publicMessagePreservation']} if 'publicMessagePreservation' in session.report else {})}}}
            if selected is not None:
                result['_meta'][NATIVE]['initialSelection']=initial_selection.confirmed(self.config,session,selected)
            if method=='session/new':result['sessionId']=identity
            return result
        identity=params.get('sessionId');session=self.sessions.get(identity)
        if not session:raise RpcError(-32602,'Session is not loaded on this connection.')
        if method=='_amplifier/retire/status':
            if NATIVE not in self.extensions:raise RpcError(-32601,'Native extension was not negotiated.')
            if set(params)!={'sessionId'}:raise RpcError(-32602,'Exact native retirement status selector required.')
            return session.retirement_status()
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
            if operation in {'steer.submit','history.revise'}:
                if 'public_message' in args:raise RpcError(-32602,'Public originals require the reserved outer metadata slot.')
                from .public_message import transport
                original=transport(self,params.get('_meta'),args.get('inputId'))
                if original is not None:args={**args,'public_message':original}
            require_existing=((params.get('_meta') or {}).get(NATIVE) or {}).get('requireExisting',False)
            if require_existing and (not session.worker or session.worker.closed or not session.worker.process or session.worker.process.returncode is not None or session.cancelled):
                raise RpcError(-32008,'The original native worker is unavailable; no process was started.',{'executed':False,'replayed':False})
            if operation=='session.relocate':
                from .relocation import relocate
                return await relocate(session,args)
            if operation=='session.relocation.inspect':
                from .relocation import state
                return state(self,identity,session.cwd)
            if operation=='peer.submit':
                from .native.peer_input import validate
                validate(args)
            if operation in {'schedule.submit','question.submit','observation.submit','peer.submit','history.revise'}:return await session.settled_input(operation,args,require_existing=require_existing)
            if operation=='history.edit':raise RpcError(-32601,'Legacy host-projection history edits are unavailable; use negotiated history.revise.')
            if operation=='steer':raise RpcError(-32601,'Unanchored root steering is unavailable over ACP; use negotiated steer.submit.')
            if operation=='steer.submit':
                from .message_metadata import guard_input_identity
                from .relocation import home
                if not session.steering.read(args.get('inputId')):
                    guard_input_identity(home(self)/'sessions'/identity/'message-interactions.sqlite3',args.get('inputId'),
                        reference=args.get('reply'),input_payload=args,actor_id=self.message_principal(identity) if self.message_principal else None)
                from .steering import submit
                return await submit(session,args)
            if operation=='peer.steer':
                from .steering import submit_peer
                return await submit_peer(session,args)
            if operation=='runtime.inspect':
                from .runtime_inspection import resident
                return await resident(session,args)
            if operation=='workers.inspect':
                from .coordination import inspect
                return inspect(self,{**args,'sessionId':identity,'cwd':session.cwd})
            if operation in {'worker.followup','worker.interrupt'}:
                from .coordination import submit
                return await submit(session,operation,args)
            if operation in {'worker.steer','worker.message','worker.stop'}:raise RpcError(-32601,'Use durable resident worker.followup or worker.interrupt; unanchored child steering is unavailable.')
            if operation=='bundle.switch' and 'commandId' in args:
                from .bundle_commands import BundleCommands
                async def activate(effect):
                    if session.lock.locked():raise ValueError('Bundle activation requires an idle turn')
                    effect()
                    value=await session.configuration_control(operation,args)
                    if not isinstance(value,dict):raise ValueError('Invalid native bundle activation result')
                    if value.get('requiresModelChoice') is True:return {**value,'applied':False}
                    if value.get('historyPreserved') is True:return {**value,'applied':True}
                    raise ValueError('Unconfirmed native bundle activation result')
                result=await BundleCommands(self.config,session.cwd,identity).perform(operation,args,activate)
                # Exact duplicate recovery is independent of mounted state; do
                # not warm a retired runtime merely to project a stored result.
                if session.worker and not session.worker.closed:await session.refresh_options()
                return result
            await session.start()
            if operation=='configuration.snapshot':
                if session.lock.locked():raise RpcError(-32000,'Configuration snapshot requires an idle session.')
                result=await session.worker.control(operation,{'bundle':session.report.get('bundle'),'historyCwd':session.cwd})
                return result
            if operation=='capabilities':
                return {'version':1,'agentKind':'amplifier-core-foundation','report':session.report,'hostBridge':HOST in self.extensions,'hostCapabilities':self.host_info if HOST in self.extensions else {},
                    'controls':NATIVE_CONTROLS,'workerOperations':['worker.followup','worker.interrupt']}
            if operation not in NATIVE_CONTROLS:raise RpcError(-32601,'Unknown native operation.')
            if operation=='configuration.cancel':return session.cancel_configuration(args)
            if operation=='configuration.apply':result=await session.apply_configuration(args)
            elif operation in {'configuration.toggle','bundle.switch','provider.select','provider.reset','mode.set','mode.clear'}:result=await session.configuration_control(operation,args)
            else:
                try:result=await session.worker.control(operation,args)
                except NativeError:
                    # Discovery failure must not conceal the existing effective pin.
                    # Publish cache availability without retrying discovery/effects.
                    if operation=='configuration.providerModels':await session.refresh_options()
                    raise
            if operation=='configuration.inspect':result={**result,'pendingConfiguration':session.pending_configuration()}
            if operation=='configuration.apply' and isinstance(result.get('configuration'),dict):result={**result,'configuration':{**result['configuration'],'pendingConfiguration':None}}
            if operation in {'configuration.apply','bundle.switch','provider.select','provider.reset','mode.set','mode.clear','configuration.providerModels'}:
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
            if hasattr(self,'transfers'):await self.transfers.close()
            if hasattr(self,'admin'):await self.admin.close()
            # A lost client means cancellation, never automatic replay on reconnect.
            for session in list(self.sessions.values()):await session.close()
            for future in self.pending.values():
                if not future.done():future.cancel()
            for task in self.tasks:task.cancel()
            await asyncio.gather(*self.tasks,return_exceptions=True)

NATIVE_CONTROLS=['session.relocate','session.relocation.inspect','configuration.snapshot','configuration.inspect','configuration.exportResources','configuration.providers','configuration.providerModels',
    'configuration.providerTest','configuration.providerLogin','configuration.providerLoginStatus','configuration.providerLoginCancel','configuration.cancel','configuration.apply','configuration.toggle','catalog.inspect','usage.inspect',
    'context.clear.review','context.clear','goals.get','goals.set','goals.clear','budget.get','budget.set','provider.reset','provider.select','tool.invoke',
    'voice.transcript.record','voice.delivery.record','mode.list','mode.set','mode.clear','native.status','native.compact','history.snapshot','history.revise','bundle.preview','bundle.switch',
    'session.naming','session.naming.generate','steer.submit','question.submit','schedule.submit','observation.submit','peer.submit','peer.steer','memory.consolidate','operations.submit','operations.cancel','operations.write',
    'kernels.create','kernels.execute','kernels.interrupt','kernels.reset','kernels.close','capacity.get','capacity.set','capacity.quota',
    'task.get','task.receipt','task.history','task.create','task.update','task.pause','task.resume','task.block','task.complete']

def main():
    parser=argparse.ArgumentParser(description='Modular Amplifier ACP v1 agent')
    parser.add_argument('--config',type=Path)
    args=parser.parse_args();config=json.loads(args.config.read_text()) if args.config else {}
    if not isinstance(config,dict):parser.error('config must be a JSON object')
    # Reserve wire output before any native libraries can print to stdout.
    wire=os.fdopen(os.dup(sys.stdout.fileno()),'w',buffering=1)
    os.dup2(sys.stderr.fileno(),sys.stdout.fileno());sys.stdout=sys.stderr
    asyncio.run(Server(config,wire).run())
