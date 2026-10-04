"""Offline fixture provider. Every turn still crosses actual Core/Foundation/loop-live."""
import asyncio
from pathlib import Path

async def gate(config,name):
    directory=Path(config["gateDirectory"]);directory.mkdir(parents=True,exist_ok=True)
    (directory/(name+".entered")).touch()
    async with asyncio.timeout(30):
        while not (directory/(name+".release")).exists():await asyncio.sleep(.01)

from amplifier_core.models import ProviderInfo, ToolResult
from amplifier_core.message_models import ChatResponse,TextBlock,Usage,ToolCall
class FixtureProvider:
    name="fixture"
    def parse_tool_calls(self,response):return response.tool_calls or []
    def __init__(self,config=None):self.config=config or {}
    def get_info(self):return ProviderInfo(id='fixture',display_name='Offline fixture',defaults={'model':self.config.get('fixtureDefaultModel','fixture-model'),'max_tokens':4096},config_fields=self.config.get('fixtureConfigFields',[]),capabilities=['tools','completion:single_attempt:v2'])
    async def close(self):pass
    async def list_models(self):
        if self.config.get('modelDiscoveryAudit'):
            with Path(self.config['modelDiscoveryAudit']).open('a') as output:output.write(self.config.get('fixtureLabel','fixture')+'\n')
        if self.config.get('fixtureModelsFile'):
            import json
            result=json.loads(Path(self.config['fixtureModelsFile']).read_text())
            if isinstance(result,dict):raise RuntimeError('fixture private discovery error')
            return result
        if self.config.get('modelDiscoveryError'):raise RuntimeError('fixture private detail must not be returned')
        return self.config.get('fixtureModels',[{'id':'fixture-model'}])
    async def login(self,print_fn=None):
        if print_fn:print_fn('Offline fixture sign-in')
        await asyncio.sleep(60)
        return True
    async def complete(self,request,**kwargs):
        if self.config.get('requestSelectionAudit'):
            import json
            with Path(self.config['requestSelectionAudit']).open('a') as output:output.write(json.dumps({'instance':self.config.get('fixtureLabel'),'model':request.model,'effort':request.reasoning_effort})+'\n')
        if self.config.get("selectedModelAudit"):
            with Path(self.config["selectedModelAudit"]).open("a") as output:output.write(str(request.model)+"\n")
        if (kwargs.get('request_options') or {}).get('single_attempt'):
            import hashlib,json
            prompt=[{'role':'user','content':[{'type':'input_text','text':'Reply with OK.'}]}]
            audit=self.config.get('readinessAudit')
            if audit:
                with Path(audit).open('a') as output:output.write('one offline request\n')
            receipt={'version':2,'model':request.model,'reasoning_effort':request.reasoning_effort,'max_output_tokens':request.max_output_tokens,'timeout_seconds':request.timeout,'native_count_requests':1,'generation_requests':1,'retries':0,'continuations':0,'closed':True,'native_input_tokens':4,'input_sha256':hashlib.sha256(json.dumps(prompt,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'request_sha256':hashlib.sha256(request.model_dump_json().encode()).hexdigest()}
            return ChatResponse(content=[TextBlock(text='OK from offline readiness fixture')],finish_reason='stop',metadata={'openai:status':'completed','openai:single_attempt':receipt})
        if self.config.get('rewindGate') and not getattr(self,'rewindWrapped',False):
            self.rewindWrapped=True;context=self.coordinator.get('context');original_set=context.set_messages
            async def paused_set(messages):
                await original_set(messages)
                await gate(self.config,'rewind')
            context.set_messages=paused_set
        if self.config.get('steeringUnavailable'):self.coordinator.register_capability('live.steering',{'version':1,'mode':'unavailable'})
        if self.config.get('injectionGate') and not getattr(self,'injectionWrapped',False):
            self.injectionWrapped=True
            context=self.coordinator.get('context');original=context.add_message
            async def paused_add(message):
                await original(message)
                if message.get('metadata',{}).get('amplifier_input',{}).get('id')==self.config['injectionGate']:await gate(self.config,'injection')
            context.add_message=paused_add
        prompt=next((m.content for m in reversed(request.messages) if m.role=='user'),'')
        if isinstance(prompt,list):prompt=' '.join(getattr(b,'text','') for b in prompt)
        if self.config.get('inputOutcomeFailure') and 'fail-outcome' in str(prompt):
            from amplifier_core import InvalidRequestError
            if self.config.get('inputOutcomeAfterTool') and request.messages[-1].role != 'tool':
                return ChatResponse(content=[TextBlock(text='Before fixture tool effect.')],tool_calls=[ToolCall(id='permit-call',name='fixture_permit',arguments={})],finish_reason='tool_calls')
            if self.config.get('inputOutcomeCheckpoint'):
                original = self.coordinator.get_capability('live.checkpoint')
                async def checkpoint(*args, **kwargs):
                    if self.config['inputOutcomeCheckpoint'] == 'fail': raise OSError('private checkpoint detail')
                    await gate(self.config, 'outcome-checkpoint')
                    return await original(*args, **kwargs)
                self.coordinator.register_capability('live.checkpoint', checkpoint)
            if self.config.get('inputOutcomeProviderGate'): await gate(self.config, 'outcome-provider')
            raise InvalidRequestError('private token/password must never be projected', provider='fixture')
        if self.config.get('coordinationFixture') and 'spawn-persistent' in str(prompt) and not getattr(self,'coordinationSpawned',False):
            self.coordinationSpawned=True
            return ChatResponse(content=[TextBlock(text='Starting persistent fixture worker.')],tool_calls=[ToolCall(id='delegate-fixture',name='delegate',arguments={'agent':'fixture-child','instruction':'child-initial','persistent':True})],finish_reason='tool_calls')
        if 'steering-late' in str(prompt):
            checkpoint=self.coordinator.get_capability('live.checkpoint')
            async def paused_checkpoint(*args,**kwargs):
                await gate(self.config,'checkpoint')
                return await checkpoint(*args,**kwargs)
            self.coordinator.register_capability('live.checkpoint',paused_checkpoint)
        if 'steering-apply' in str(prompt) and request.messages[-1].role!='tool':
            return ChatResponse(content=[TextBlock(text='Before deterministic steering boundary.')],tool_calls=[ToolCall(id='wait-call',name='fixture_wait',arguments={})],finish_reason='tool_calls')
        if 'slow' in str(prompt):await asyncio.sleep(60)
        if 'permission' in str(prompt) and request.messages[-1].role!='tool':
            return ChatResponse(content=[TextBlock(text='Before permission tool.')],tool_calls=[ToolCall(id='permit-call',name='fixture_permit',arguments={})],finish_reason='tool_calls')
        return ChatResponse(content=[TextBlock(text='Native graph reply: '+str(prompt))],finish_reason='stop',usage=Usage(input_tokens=8,output_tokens=5,total_tokens=13))
async def mount(coordinator,config=None):
    if config and config.get('contextClearAudit'):
        from pathlib import Path
        from amplifier_acp.native import history_revision
        original_copy=history_revision._copy_segment
        def audit_copy(source,destination,**kwargs):
            root=Path(config['contextClearAudit']);root.mkdir(exist_ok=True)
            (root/source.name).write_bytes(source.read_bytes())
            return original_copy(source,destination,**kwargs)
        history_revision._copy_segment=audit_copy
    if config and config.get('contextClearCrash'):
        # Owned test-only crash points around actual native/Core checkpoints.
        import os,signal
        from amplifier_acp.native import history_revision
        original_write=history_revision._write
        def crash_write(path,value):
            phase=config['contextClearCrash']
            if phase=='canonical' and path.name=='context-head.json':os.kill(os.getpid(),signal.SIGKILL)
            original_write(path,value)
            if phase=='preserved' and path.name=='segment.json' or phase=='head' and path.name=='context-head.json':os.kill(os.getpid(),signal.SIGKILL)
        history_revision._write=crash_write
    if config and config.get('inputOutcomeCrashAfterReceipt'):
        from amplifier_acp.input_outcome import Outcomes
        import os,signal
        original = Outcomes.failed
        def crash(owner, identity, event):
            original(owner, identity, event)
            os.kill(os.getpid(), signal.SIGKILL)
        Outcomes.failed = crash
    provider=FixtureProvider(config);provider.coordinator=coordinator
    await coordinator.mount('providers',provider,name='fixture')
    class Permit:
        name='fixture_permit';description='Ask the fixture approval surface';input_schema={'type':'object','properties':{}}
        async def execute(self,args):
            decision=await coordinator.approval_system.request_approval('Fixture permission',options=['allow','deny'])
            return ToolResult(success=True,output={'decision':decision})
    await coordinator.mount('tools',Permit(),name='fixture_permit')

    class Wait:
        name='fixture_wait';description='Wait for an isolated fixture test gate';input_schema={'type':'object','properties':{}}
        async def execute(self,args):
            await gate(config,'tool')
            return ToolResult(success=True,output={'released':True})
    if config and config.get('gateDirectory'):await coordinator.mount('tools',Wait(),name='fixture_wait')

    if config and config.get('coordinationFixture'):
        class Delegate:
            name='delegate';description='Run the actual native child fixture';input_schema={'type':'object','properties':{'agent':{'type':'string'},'instruction':{'type':'string'}},'required':['agent','instruction']}
            async def execute(self,args):
                result=await coordinator.get_capability('live.children').spawn(args['agent'],args['instruction'],coordinator.get_capability('live.children').sessions[coordinator.session_id])
                return ToolResult(success=True,output=result)
        await coordinator.mount('tools',Delegate(),name='delegate')
