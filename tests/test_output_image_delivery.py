import json
from types import SimpleNamespace
import pytest
from amplifier_core.message_models import ChatRequest,Message,ToolSpec
from amplifier_acp.native.app_guidance import install_app_access


class Coordinator:
    def __init__(self):
        self.capabilities={};self.tools={};self.callbacks={}
        self.hooks=SimpleNamespace(register=lambda event,handler,**kwargs:self.callbacks.__setitem__(event,handler))
    def get_capability(self,key):return self.capabilities.get(key)
    def register_capability(self,key,value):self.capabilities[key]=value
    async def mount(self,kind,tool,name):self.tools[name]=tool


@pytest.mark.asyncio
async def test_advertised_output_inspection_delivers_pixels_only_for_retained_receipt_and_current_input():
    coordinator=Coordinator();epoch=['first'];calls=[];available=True
    image={'id':'saved','sha256':'a'*64,'title':'Exact image','mimeType':'image/png','bytes':3}
    async def bridge(operation,args):
        calls.append((operation,args))
        if operation=='context.manifest':return {'inputIds':epoch.copy(),'surfaces':[]}
        if operation=='dispatch':return {'accepted':True,'result':image.copy()}
        if operation=='outputs.image.read':
            assert args=={'id':'saved','sha256':'a'*64}
            if not available:raise ValueError('Saved body changed')
            return {**image,'_image':'YWJj'}
        raise AssertionError(operation)
    await install_app_access(coordinator,bridge,host_info={'features':{'outputs':True},'appControl':{'operations':['dispatch']}})
    delivery=coordinator.get_capability('web.surface_delivery');provider=SimpleNamespace(get_info=lambda:{'capabilities':['vision']})
    tools=[ToolSpec(name='app_control',parameters={})]
    empty=ChatRequest(messages=[Message(role='user',content='Inspect the saved image')],tools=tools)
    await delivery.prepare(empty,provider)
    result=await coordinator.tools['app_control'].execute({'operation':'dispatch','args':{'action':'outputs.image','args':{'id':'saved','sha256':'a'*64}}})
    request=ChatRequest(messages=[*empty.messages,Message(role='tool',name='app_control',tool_call_id='inspection',content=json.dumps(result.output))],tools=tools)
    prepared=await delivery.prepare(request,provider)
    assert prepared.messages[-1].metadata['ephemeral'] is True
    assert prepared.messages[-1].content[-1].type=='image'
    assert prepared.messages[-1].content[-1].source['data']=='YWJj'
    assert all(operation!='context.read' for operation,_ in calls)
    assert len((await delivery.prepare(empty,provider)).messages)==len(empty.messages)
    epoch[:]=['second']
    checked=await delivery.revalidate(prepared)
    assert isinstance(checked.messages[-1].content,str) and 'no pixels' in checked.messages[-1].content
    assert len((await delivery.prepare(request,provider)).messages)==len(request.messages)


@pytest.mark.asyncio
async def test_unadvertised_output_pixels_are_unavailable():
    coordinator=Coordinator();calls=[]
    async def bridge(operation,args):
        calls.append(operation)
        return {'accepted':True,'result':{'id':'saved','sha256':'a'*64}}
    await install_app_access(coordinator,bridge,host_info={'appControl':{'operations':['dispatch']}})
    tool=coordinator.tools['app_control'];result=await tool.execute({'operation':'dispatch','args':{'action':'outputs.image','args':{}}})
    request=ChatRequest(messages=[Message(role='tool',name='app_control',content=json.dumps(result.output))],tools=[ToolSpec(name='app_control',parameters={})])
    delivery=coordinator.get_capability('web.surface_delivery')
    prepared=await delivery.prepare(request,SimpleNamespace(get_info=lambda:{'capabilities':['vision']}))
    assert calls==['dispatch']
    assert all(block.type!='image' for message in prepared.messages for block in message.content if isinstance(message.content,list))
