"""Canonical native history revision followed by one explicitly admitted input."""
import hashlib,json
from pathlib import Path
from .session_lifecycle import canonical,bound,message_id,regular
from .history import visible,stamp
from .relocation import write

def require_own_input(row):
    """External inputs remain readable, but cannot be rewritten as human speech."""
    metadata=row.get('metadata') or {}
    origin=metadata.get('inputOrigin',row.get('inputOrigin'))
    via=metadata.get('via',row.get('via'))
    if (isinstance(origin,str) and origin in {'agent','coordination','peer','schedule','scheduled','scheduler','observation'}
            or isinstance(via,str) and via in {'peer','schedule','observation'}
            or metadata.get('scheduledRunId',row.get('scheduledRunId'))):
        raise ValueError('Choose one of your own messages to edit.')
    marker=metadata.get('amplifier_input')
    if marker is not None and (not isinstance(marker,dict)
            or marker.get('kind','user') not in ('user','steer')
            or marker.get('source','user')!='user'
            or 'version' in marker and (type(marker['version']) is not int or marker['version']!=1)
            or marker.get('kind')=='steer' and marker.get('version')!=1):
        raise ValueError('The saved input identity is unavailable or unsupported; refresh history before editing.')

def path(home,session,operation):
    from .configuration_clone import location
    if not isinstance(operation,str) or not 1<=len(operation)<=200:raise ValueError('Bounded history operationId required')
    return location(home,session)/'history-edits'/(hashlib.sha256(operation.encode()).hexdigest()+'.json')

def receipt(server,params):
    from .relocation import home
    cwd=params.get('cwd','');identity=params.get('sessionId')
    if not Path(cwd).is_absolute() or not Path(cwd).is_dir() or not server.directory(cwd,identity).is_dir():raise ValueError('Exact native history workspace required')
    file=path(home(server),identity,params.get('operationId'))
    if not file.exists():return {'receipt':None}
    value=json.loads(regular(file))
    if value['historyCwd']!=str(Path(cwd).resolve()):raise ValueError('Native edit receipt belongs to another workspace')
    if value['status']=='preparing':value={**value,'status':'unknown'}
    return {'receipt':value}

async def revise(worker,args,publish):
    required={'inputId','operationId','targetMessageId','expectedHistoryRevision','text'}
    if not isinstance(args,dict) or set(args)-{'public_message','prompt'}!=required:raise ValueError('Exact history revision arguments required')
    if 'public_message' in args:
        from .public_message import validate as validate_public
        validate_public(args['public_message'],args.get('inputId'))
    for key in required-{'text'}:
        if not isinstance(args[key],str) or not 1<=len(args[key])<=200:raise ValueError('Bounded native history revision identities required')
    if not isinstance(args['text'],str) or not args['text'].strip() or len(args['text'])>worker.runtime.max_input_chars:raise ValueError('Bounded replacement text required')
    try:
        worker.controls.require_idle()
        if worker.approvals or worker.bridges or not worker.runtime.inbox.empty():raise ValueError('Finish pending interactions before editing history')
    except ValueError as error:return {'accepted':False,'executed':False,'reason':str(error)}
    from .native.host.config import app_home
    from .native.session_files import sessions_dir
    identity=worker.session.session_id;cwd=str(worker.workspace)
    file=path(app_home(),identity,args['operationId']);fence=file.parent.parent/'history-edit-pending.json'
    if file.exists() or fence.exists():raise ValueError('This native edit was already attempted or is unresolved; inspect its receipt, never replay')
    directory=sessions_dir(cwd)/identity
    try:
        rows,_,revision=canonical(directory,args['expectedHistoryRevision'],app_home=app_home())
        target=bound(rows,args['targetMessageId'],user=True);position,original=rows[target]
        require_own_input(original)
        marker=(original.get('metadata') or {}).get('amplifier_input',{})
    except ValueError as error:return {'accepted':False,'executed':False,'reason':str(error)}
    # Resolve only the selected canonical row, under the same revision that is
    # rechecked by rewind. The caller may replace text, not its saved context.
    from .public_message import row_envelope, supports_context
    from .native.message_interactions import prepare_input
    from .message_metadata import digest
    saved = row_envelope(original)
    public = args.get('public_message')
    cards = []
    if saved is not None:
        cards = [block for block in saved['blocks'] if block['type'] == 'attachment']
        if public is None and not cards:
            public = {**saved, 'inputId': args['inputId'], 'blocks': [{'type': 'text', 'text': args['text']}]}
        # Literal public text may differ from expanded execution text. Only the
        # immutable descriptors/capture must match the canonical source row.
        if (public is None or public['capture'] != saved['capture']
                or [block for block in public['blocks'] if block['type'] == 'attachment'] != cards):
            return {'accepted': False, 'executed': False, 'reason': 'The edit must preserve the original attachment descriptors.'}
        if not supports_context(worker.session.coordinator.get('context')):
            return {'accepted': False, 'executed': False, 'reason': 'The mounted context cannot preserve the original message.'}
    elif (original.get('attachments') or (original.get('metadata') or {}).get('attachments')
          or isinstance(original.get('content'), list) and any(not isinstance(block, dict) or block.get('type') != 'text' for block in original['content'])):
        return {'accepted': False, 'executed': False, 'reason': 'This legacy input has no qualified attachment context for editing.'}
    prompt = args.get('prompt', [{'type': 'text', 'text': args['text']}])
    if (not isinstance(prompt, list) or not prompt or prompt[0] != {'type': 'text', 'text': args['text']}
            or len(prompt) > 257 or len(json.dumps(prompt).encode()) > 8*1024*1024
            or cards and len(prompt) < 1 + len(cards)
            or not cards and len(prompt) != 1):
        return {'accepted': False, 'executed': False, 'reason': 'A qualified replacement prompt preserving original context is required.'}
    from .content import prompt_content
    from .server import RpcError
    try:
        value, attachments = prompt_content({'appHome': str(app_home())}, prompt)
    except (ValueError, RpcError) as error:
        return {'accepted': False, 'executed': False, 'reason': str(error)}
    quote = (original.get('metadata') or {}).get('replyTo')
    if quote is not None:
        quote = {**quote, 'inputId': args['inputId']}
        quote.pop('id', None)
        quote['id'] = digest(quote)
    try:
        value = await prepare_input(worker.session.coordinator, value, quote, max_chars=worker.runtime.max_input_chars)
    except ValueError as error:
        return {'accepted': False, 'executed': False, 'reason': str(error)}
    provenance = {'inputOrigin':'history-edit','editedMessageId':message_id(original,position,getattr(rows,'epoch',None)),
                  'historyOperationId':args['operationId'], **({'replyTo':quote} if quote is not None else {})}
    if len(json.dumps(provenance,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()) > 16384:
        return {'accepted': False, 'executed': False, 'reason': 'The saved edit context exceeds the input provenance bound.'}
    from .native.session_store import text_content
    displayed=[]
    for index,(offset,row) in enumerate(rows):
        if row.get('role') not in {'user','assistant'} or not visible(row):continue
        item={'id':message_id(row,offset,getattr(rows,'epoch',None)),'role':row['role'],'text':text_content(row),'nativeIndex':index}
        row_input=(row.get('metadata') or {}).get('amplifier_input',{})
        if row_input.get('kind') in ('user','steer') and row_input.get('id'):item['inputId']=row_input['id']
        displayed.append(item)
    target_id=message_id(original,position,getattr(rows,'epoch',None));before_turn=marker.get('id') or target_id
    retained_turn=None
    from .history import content_updates,starts_turn
    for offset,row in rows[:target]:
        # A steering correction belongs to the original turn. Use the same
        # grouping rule as passive history, so the Host can prove the prefix.
        if content_updates(row,offset)[0] and starts_turn(row,retained_turn):
            retained_turn=(row.get('metadata') or {}).get('amplifier_input',{}).get('id') or message_id(row,offset,getattr(rows,'epoch',None))
    record={'operationId':args['operationId'],'inputId':args['inputId'],'targetMessageId':args['targetMessageId'],'beforeMessageId':target_id,'beforeTurnId':before_turn,'retainedThroughTurnId':retained_turn,'previousRevision':revision,'historyCwd':cwd,'status':'preparing','requestHash':hashlib.sha256(json.dumps(args,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'eventsPreserved':True,'replayed':False}
    write(file,record);write(fence,{'operationId':args['operationId'],'inputId':args['inputId']})
    from .native.history_revision import rewind,receipt_path
    try:
        from .native.input_provenance import reserve
        reserve(worker.input_provenance_path,args['inputId'],provenance)
        await rewind(worker.controls,{'operationId':args['operationId'],'source':{'id':identity,'workspace':cwd,'status':'idle','messages':displayed},'messageId':target_id})
        proof={**record,'status':'rewritten','historyRevision':stamp(directory/'transcript.jsonl')[0]}
        write(file,proof)
        publish({'type':'history.revised',**{key:proof[key] for key in ('operationId','inputId','targetMessageId','beforeMessageId','beforeTurnId','retainedThroughTurnId','previousRevision','historyRevision','eventsPreserved')}})
        from amplifier_module_loop_live.runtime import Input
        if public is not None:worker.stage_original(args['inputId'],public)
        try:
            accepted=await worker.runtime.submit(Input('user',value,id=args['inputId'],attachments=tuple(attachments),activation=worker.activation))
        except BaseException:
            if public is not None:worker.public_originals.discard(args['inputId'])
            raise
        write(file,{**proof,'status':'admitted','accepted':True});fence.unlink()
        return {'accepted':True,'inputId':accepted,'historyRevision':proof['historyRevision'],'operationId':args['operationId']}
    except BaseException as error:
        journal=receipt_path(app_home(),identity,args['operationId'])
        aborted=False
        if journal.exists():
            # This is a selected explicit edit journal, never a catalog read.
            try:aborted=json.loads(regular(journal,4*64*1024*1024)).get('phase')=='aborted'
            except Exception:pass
        write(file,{**record,'status':'aborted' if aborted else 'unknown','accepted':False})
        if aborted:
            fence.unlink(missing_ok=True)
            return {'accepted':False,'executed':False,'reason':'Native history revision was rolled back; replacement input was not admitted.'}
        raise


def before(server,params):
    from . import history
    cwd=params.get('cwd','');identity=params.get('sessionId')
    if not Path(cwd).is_absolute():raise ValueError('Exact native history workspace required')
    if not Path(cwd).is_dir():
        from .managed_session import history_scope
        if not history_scope(cwd,identity,server.config):raise ValueError('Exact native history workspace required')
    directory=server.directory(cwd,identity)
    from .relocation import home
    rows,_,revision=canonical(directory,params.get('expectedRevision'),app_home=home(server))
    target=bound(rows,params.get('beforeTurnId'),user=True);offset=rows[target][0]
    result = ({'updates': [], 'turnGroups': [], 'nextCursor': None, 'revision': revision, 'diagnostics': []}
              if not offset else history.page(directory,cursor=history._cursor(revision,offset),limit=params.get('limit',50),expected_revision=revision,app_home=home(server)))
    if params.get('includeEditContext') is True:
        from .public_message import row_envelope
        original = rows[target][1]
        require_own_input(original)
        result['editContext'] = {'version': 1, 'targetMessageId': params['beforeTurnId'],
                                 'publicMessage': row_envelope(original)}
    return result
