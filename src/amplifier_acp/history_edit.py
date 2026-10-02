"""Canonical native history revision followed by one explicitly admitted input."""
import hashlib,json
from pathlib import Path
from .session_lifecycle import canonical,bound,message_id,regular
from .history import visible,stamp
from .relocation import write

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
    if not isinstance(args,dict) or set(args)!=required:raise ValueError('Exact history revision arguments required')
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
        rows,_,revision=canonical(directory,args['expectedHistoryRevision'])
        target=bound(rows,args['targetMessageId'],user=True);position,original=rows[target]
        marker=(original.get('metadata') or {}).get('amplifier_input',{})
        if marker.get('kind','user')!='user':raise ValueError('Choose an original user input, not an injected steering correction')
    except ValueError as error:return {'accepted':False,'executed':False,'reason':str(error)}
    from .native.session_store import text_content
    displayed=[]
    for index,(offset,row) in enumerate(rows):
        if row.get('role') not in {'user','assistant'} or not visible(row):continue
        item={'id':message_id(row,offset),'role':row['role'],'text':text_content(row),'nativeIndex':index}
        row_input=(row.get('metadata') or {}).get('amplifier_input',{})
        if row_input.get('kind')=='user' and row_input.get('id'):item['inputId']=row_input['id']
        displayed.append(item)
    target_id=message_id(original,position);before_turn=marker.get('id') or target_id
    retained_turn=None
    for offset,row in rows[:target]:
        if row.get('role')=='user' and visible(row):retained_turn=(row.get('metadata') or {}).get('amplifier_input',{}).get('id') or message_id(row,offset)
    record={'operationId':args['operationId'],'inputId':args['inputId'],'targetMessageId':args['targetMessageId'],'beforeMessageId':target_id,'beforeTurnId':before_turn,'retainedThroughTurnId':retained_turn,'previousRevision':revision,'historyCwd':cwd,'status':'preparing','requestHash':hashlib.sha256(json.dumps(args,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'eventsPreserved':True,'replayed':False}
    write(file,record);write(fence,{'operationId':args['operationId'],'inputId':args['inputId']})
    from .native.history_revision import rewind,receipt_path
    try:
        from .native.input_provenance import reserve
        reserve(worker.input_provenance_path,args['inputId'],{'inputOrigin':'history-edit','editedMessageId':target_id,'historyOperationId':args['operationId']})
        await rewind(worker.controls,{'operationId':args['operationId'],'source':{'id':identity,'workspace':cwd,'status':'idle','messages':displayed},'messageId':target_id})
        proof={**record,'status':'rewritten','historyRevision':stamp(directory/'transcript.jsonl')[0]}
        write(file,proof)
        publish({'type':'history.revised',**{key:proof[key] for key in ('operationId','inputId','targetMessageId','beforeMessageId','beforeTurnId','retainedThroughTurnId','previousRevision','historyRevision','eventsPreserved')}})
        from amplifier_module_loop_live.runtime import Input
        accepted=await worker.runtime.submit(Input('user',args['text'],id=args['inputId'],activation=worker.activation))
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
    if not Path(cwd).is_absolute() or not Path(cwd).is_dir():raise ValueError('Exact native history workspace required')
    directory=server.directory(cwd,identity)
    rows,_,revision=canonical(directory,params.get('expectedRevision'))
    target=bound(rows,params.get('beforeTurnId'),user=True);offset=rows[target][0]
    if not offset:return {'updates':[],'turnGroups':[],'nextCursor':None,'revision':revision,'diagnostics':[]}
    return history.page(directory,cursor=history._cursor(revision,offset),limit=params.get('limit',50),expected_revision=revision)
