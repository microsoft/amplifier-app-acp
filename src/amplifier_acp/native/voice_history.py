"""Durable spoken history updates; recording never submits model input."""
import copy
import hashlib
import json
from datetime import UTC, datetime
from .host.config import app_home, write_private
from .history_revision import receipt_path

async def record(controls, args):
    required={'callId','itemId','role','text','append','commandId'}
    if not required<=set(args) or set(args)-required-{'createdAt'} or any(not isinstance(args.get(k),str) or not 0<len(args[k])<=200 for k in ('callId','itemId','commandId')):
        raise ValueError('Voice history requires bounded callId, itemId and commandId identities.')
    if args['role'] not in {'user','assistant'} or not isinstance(args['text'],str) or len(args['text'].encode())>1_000_000 or type(args['append']) is not bool:
        raise ValueError('Voice history requires a role, bounded text and boolean append.')
    if args.get('createdAt') is not None:
        if not isinstance(args['createdAt'],str) or len(args['createdAt'])>64:raise ValueError('Invalid voice timestamp.')
        datetime.fromisoformat(args['createdAt'].replace('Z','+00:00'))
    digest=hashlib.sha256(json.dumps(args,sort_keys=True).encode()).hexdigest()
    path=receipt_path(app_home(),controls.session.session_id,'voice:'+args['commandId'])
    if path.exists():
        saved=json.loads(path.read_text())
        if saved.get('requestHash')!=digest:raise ValueError('Voice command identity already belongs to a different update.')
        return {**saved['result'],'duplicate':True}
    try:controls.require_idle()  # No mutation of a running model/tool context.
    except ValueError:return {"recorded":False,"reason":"busy","retryable":True,"executed":False}
    context=controls.coordinator.get('context')
    before=copy.deepcopy(await context.get_messages())
    after=copy.deepcopy(before)
    matching=[row for row in after if (row.get('metadata') or {}).get('amplifier_voice',{}).get('callId')==args['callId'] and (row.get('metadata') or {}).get('amplifier_voice',{}).get('itemId')==args['itemId']]
    if len(matching)>1:raise ValueError('Duplicate native voice item identities; history preserved for inspection.')
    row=matching[0] if matching else None
    if row and row['role']!=args['role']:raise ValueError('Voice item role cannot change.')
    marker=(row.get('metadata') or {}).get('amplifier_voice',{}) if row else {}
    already=marker.get('commandId')==args['commandId']
    if already and marker.get('requestHash')!=digest:raise ValueError('Voice command identity already belongs to a different update.')
    if not already:
        if row is None:
            row={'role':args['role'],'content':'','metadata':{'timestamp':args.get('createdAt') or datetime.now(UTC).isoformat()}}
            after.append(row)
        row['content']=(row['content'] if args['append'] else '')+args['text']
        if len(row['content'].encode())>1_000_000:raise ValueError('Recorded voice item exceeds 1 MB.')
        row.setdefault('metadata',{})['amplifier_voice']={'version':1,'callId':args['callId'],'itemId':args['itemId'],'commandId':args['commandId'],'requestHash':digest,'recordedOnly':True}
        try:
            await context.set_messages(after)
            await controls.checkpoint()  # Existing writer guard and Foundation durability.
        except BaseException:
            await context.set_messages(before)
            raise
    from .history_revision import context_head
    from amplifier_acp.history import message_id
    epoch=context_head(app_home(),controls.session.session_id)
    result={'historyTurnId':message_id(row,0,epoch),'nativeMessageId':'voice:'+hashlib.sha256((controls.session.session_id+'\0'+(epoch+'\0' if epoch else '')+args['callId']+'\0'+args['itemId']).encode()).hexdigest()[:32],'text':row['content'],'via':'call','revision':digest,'recorded':True,'callId':args['callId'],'itemId':args['itemId'],'role':args['role'],'commandId':args['commandId'],'executed':False}
    write_private(path,json.dumps({'requestHash':digest,'result':result}))
    return {**result,**({'duplicate':True} if already else {})}

async def bind_delivery(controls, args):
    """Associate a completed typed answer with its call before voicing it."""
    if set(args) != {'callId','inputId','commandId'} or any(not isinstance(v,str) or not 0<len(v)<=200 for v in args.values()):
        raise ValueError('Exact call, input and command identities required.')
    digest=hashlib.sha256(json.dumps(args,sort_keys=True).encode()).hexdigest()
    path=receipt_path(app_home(),controls.session.session_id,'voice-delivery:'+args['commandId'])
    if path.exists():
        saved=json.loads(path.read_text())
        if saved['requestHash']!=digest:raise ValueError('Voice delivery identity changed.')
        return saved['result']
    try:controls.require_idle()
    except ValueError:return {'recorded':False,'reason':'busy','retryable':True,'executed':False}
    context=controls.coordinator.get('context')
    before=copy.deepcopy(await context.get_messages());after=copy.deepcopy(before)
    found=False;active=False
    for row in after:
        meta=row.get('metadata') or {};marker=meta.get('amplifier_input') or {}
        if row.get('role')=='user' and marker.get('id'):
            active=marker['id']==args['inputId'];found=found or active
        if active and row.get('role') in {'user','assistant'} and not meta.get('amplifier_voice') and not meta.get('ephemeral'):
            row.setdefault('metadata',{})['voiceDelivery']={'version':1,'callId':args['callId'],'source':'chat','sourceItemIds':[],'inputId':args['inputId']}
    if not found:return {'recorded':False,'reason':'input-unavailable','executed':False}
    try:
        await context.set_messages(after)
        await controls.checkpoint()
    except BaseException:
        await context.set_messages(before)
        raise
    result={'recorded':True,'callId':args['callId'],'inputId':args['inputId'],'executed':False}
    write_private(path,json.dumps({'requestHash':digest,'result':result}))
    return result
