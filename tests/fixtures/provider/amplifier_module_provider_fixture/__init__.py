"""Offline fixture provider. Every turn still crosses actual Core/Foundation/loop-live."""
import asyncio
from amplifier_core.models import ProviderInfo, ToolResult
from amplifier_core.message_models import ChatResponse,TextBlock,Usage,ToolCall
class FixtureProvider:
    name="fixture"
    def parse_tool_calls(self,response):return response.tool_calls or []
    def __init__(self,config=None):self.config=config or {}
    def get_info(self):return ProviderInfo(id='fixture',display_name='Offline fixture',defaults={'model':'fixture-model','max_tokens':4096},capabilities=['tools'])
    async def list_models(self):return [{'id':'fixture-model'}]
    async def login(self,print_fn=None):
        if print_fn:print_fn('Offline fixture sign-in')
        await asyncio.sleep(60)
        return True
    async def complete(self,request,**kwargs):
        prompt=next((m.content for m in reversed(request.messages) if m.role=='user'),'')
        if isinstance(prompt,list):prompt=' '.join(getattr(b,'text','') for b in prompt)
        if 'slow' in str(prompt):await asyncio.sleep(60)
        if 'permission' in str(prompt) and request.messages[-1].role!='tool':
            return ChatResponse(content=[TextBlock(text='Before permission tool.')],tool_calls=[ToolCall(id='permit-call',name='fixture_permit',arguments={})],finish_reason='tool_calls')
        return ChatResponse(content=[TextBlock(text='Native graph reply: '+str(prompt))],finish_reason='stop',usage=Usage(input_tokens=8,output_tokens=5,total_tokens=13))
async def mount(coordinator,config=None):
    await coordinator.mount('providers',FixtureProvider(config),name='fixture')
    class Permit:
        name='fixture_permit';description='Ask the fixture approval surface';input_schema={'type':'object','properties':{}}
        async def execute(self,args):
            decision=await coordinator.approval_system.request_approval('Fixture permission',options=['allow','deny'])
            return ToolResult(success=True,output={'decision':decision})
    await coordinator.mount('tools',Permit(),name='fixture_permit')
