"""Shared standard ACP text/tool projection; never admission or accounting."""
import json

async def project(session, row, is_root=True, *, tool_namespace=None):
    kind = row.get("type")
    if kind=='assistant.delta' and is_root:
        session.streamed=True;session.had_deltas=True
        await session.update('agent_message_chunk',content={'type':'text','text':row.get('text','')})
    elif kind=='assistant.message' and is_root and not session.had_deltas:
        text=row.get('text','')
        if text:await session.update('agent_message_chunk',content={'type':'text','text':text});session.streamed=True
    elif kind=='execution.event':
        event=row.get('event',{})
        if event.get('kind')=='llm' and event.get('phase')=='running' and event.get('lifecycle')!='background' and event.get('sessionId')==session.identity and event.get('id')!=session.last_model_id:
            session.last_model_id=event.get('id');session.streamed=False;session.had_deltas=False
        if event.get('kind')=='tool':
            identity=event.get('toolCallId') or event.get('id')
            if not identity:return
            # Child IDs are scoped so concurrent delegates cannot collide.
            identity=str(event.get('sessionId') or session.identity)+':'+str(identity)
            if tool_namespace is not None:
                identity=str(tool_namespace)+':'+identity
            status={'running':'in_progress','completed':'completed','error':'failed','cancelled':'failed'}.get(event.get('phase'),'pending')
            data={'toolCallId':identity,'status':status}
            if 'input' in event:data['rawInput']=event['input']
            if 'output' in event:
                data['rawOutput']=event['output']
                output=event['output']
                text=output if isinstance(output,str) else json.dumps(output,ensure_ascii=True,separators=(',',':'))
                data['content']=[{'type':'content','content':{'type':'text','text':text}}]
            if identity not in session.tools:
                session.tools.add(identity);data.update(title=event.get('label','Tool'),kind='other')
                await session.update('tool_call',**data)
            else:
                await session.update('tool_call_update',**data)
