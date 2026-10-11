"""Passive byte-window native history reader. Never starts or imports an engine."""
from __future__ import annotations
import base64
import hashlib
import json
import os
from pathlib import Path
import stat

class HistoryError(ValueError):
    def __init__(self,message):
        super().__init__(message)
        reason='history_changed' if 'changed' in message else 'invalid_cursor' if 'cursor' in message else 'oversized_turn' if 'oversized' in message else 'unsupported_history'
        self.data={'reason':reason,'replayed':False}


def stamp(path):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode):raise HistoryError('Native transcript is not a regular file.')
    return hashlib.sha256(f'{info.st_dev}:{info.st_ino}:{info.st_size}:{info.st_mtime_ns}:{info.st_ctime_ns}'.encode()).hexdigest(),info.st_size

def source(directory):
    primary=directory/'transcript.jsonl';backup=directory/'transcript.jsonl.backup'
    if primary.exists() or primary.is_symlink():return primary,[]
    if backup.exists() or backup.is_symlink():return backup,[{'code':'backup_read','message':'Primary transcript is missing; reading preserved backup without changing it.'}]
    raise HistoryError('Native history does not exist.')

def inspect(directory):
    """Cheap revision only: regular-file stat, no transcript parse or engine."""
    directory=Path(directory)
    if not any(path.exists() or path.is_symlink() for path in (directory/'transcript.jsonl',directory/'transcript.jsonl.backup')):
        return {'available':False,'revision':None,'diagnostics':[{'code':'missing_native_history'}]}
    path,diagnostics=source(directory);revision,_=stamp(path)
    return {'available':True,'revision':revision,'diagnostics':diagnostics}


def _exact_segments(directory, app_home, history_cwd):
    """Select only this session's completed, scoped context-clear chain."""
    from .native.history_revision import context_head, guard_context_clear, segment, _directory, _read, _command
    head = context_head(app_home, directory.name, history_cwd) if app_home is not None else None
    result = [(directory, head, None)]
    guards = []
    if app_home is None:
        return head, result, guards
    guard_context_clear(app_home, directory.name)
    archive = head
    seen = set()
    while archive:
        if history_cwd is None or archive in seen or len(seen) >= 32:
            raise HistoryError('Preserved source chain is unavailable or ambiguous')
        seen.add(archive)
        selected, descriptor = segment(app_home, directory.name, archive)
        descriptor_path = selected / 'segment.json'
        receipt_path = _directory(app_home, directory.name) / ('context-' + archive + '.json')
        before = (stamp(descriptor_path)[0], stamp(receipt_path)[0])
        receipt = _read(receipt_path)
        scope = {'sessionId': directory.name, 'historyCwd': str(Path(history_cwd).resolve())}
        if (receipt.get('status') != 'succeeded' or receipt.get('operation') != 'context.clear'
                or receipt.get('scope') != scope or _command(receipt.get('commandId')) != archive
                or receipt.get('result', {}).get('archiveId') != archive
                or any(descriptor.get(key) != value for key, value in scope.items())
                or descriptor.get('sourceEpoch') != descriptor.get('previousArchive')
                or _read(descriptor_path) != descriptor):
            raise HistoryError('Preserved source has no completed exact context-clear receipt')
        guards.extend([(descriptor_path, before[0]), (receipt_path, before[1])])
        result.append((selected, descriptor['sourceEpoch'], descriptor))
        archive = descriptor['previousArchive']
    return head, result, guards


def exact_message(directory, identity, expected_revision, *, app_home=None, history_cwd=None):
    """Read one original source across current and preserved context, without execution.

    Original IDs remain source references. Archive UI IDs never gain mutation or
    new-input authority. Scan every selected segment to refuse duplicate IDs.
    """
    if not isinstance(identity, str) or not identity or len(identity) > 512:
        raise HistoryError('Exact historical message identity required')
    if identity.startswith('archive:'):
        raise HistoryError('Archived view identity is not an original source identity')
    from .native.history_revision import context_head, guard_context_clear
    from .session_lifecycle import MAX_HISTORY, record
    directory=Path(directory);path,diagnostics=source(directory);revision,_=stamp(path)
    if diagnostics:raise HistoryError('Recovered backup is not a complete current source; repair the source first')
    if revision!=expected_revision:raise HistoryError('Native history changed; inspect a fresh revision')
    head, segments, guards = _exact_segments(directory, app_home, history_cwd)
    selected=None;matches=0
    for location, epoch, descriptor in segments:
        selected_path, diagnostics = source(location)
        if diagnostics:raise HistoryError('Preserved source transcript is unavailable')
        for part in (selected_path,*selected_path.parents):
            if part.is_symlink():raise HistoryError('Native source files cannot traverse symbolic links')
        before, _ = stamp(selected_path)
        guards.append((selected_path, before))
        info=selected_path.lstat();fd=os.open(selected_path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
        position=0;checksum=hashlib.sha256()
        try:
            opened=os.fstat(fd)
            if (opened.st_dev,opened.st_ino)!=(info.st_dev,info.st_ino) or not stat.S_ISREG(opened.st_mode):
                raise HistoryError('Native history changed during open')
            with os.fdopen(fd,'rb',closefd=False) as stream:
                while line:=stream.readline(MAX_HISTORY+1):
                    if len(line)>MAX_HISTORY:raise HistoryError('Native history contains an oversized record; files preserved')
                    checksum.update(line)
                    row=json.loads(line)
                    if not isinstance(row,dict) or not isinstance(row.get('role'),str):
                        raise HistoryError('Malformed native history; files preserved')
                    marker=(row.get('metadata') or {}).get('amplifier_input',{})
                    if marker.get('id')==identity or identity.startswith('native:') and message_id(row,position,epoch)==identity:
                        matches+=1;selected=(position,row,descriptor)
                    position+=len(line)
        finally:os.close(fd)
        if descriptor and checksum.hexdigest() != descriptor.get('sha256'):
            raise HistoryError('Preserved source bytes changed')
    if (any(stamp(file)[0] != saved for file, saved in guards) or stamp(path)[0]!=revision
            or (app_home is not None and context_head(app_home,directory.name,history_cwd)!=head)):
        raise HistoryError('Native history changed during source capture')
    if app_home is not None:guard_context_clear(app_home,directory.name)
    record(directory/'metadata.json')
    if matches!=1:raise HistoryError('The selected native message boundary is unavailable or ambiguous')
    position,row,descriptor=selected
    if not visible(row) or row.get('role')!='user':raise HistoryError('Choose a visible native user message boundary')
    updates, diagnostics = content_updates(row, position)
    if diagnostics or any(update.get('sessionUpdate') != 'user_message_chunk' for update in updates):
        raise HistoryError('Complete historical user message projection is unavailable')
    text = ''.join(update['content']['text'] for update in updates if update['content']['type'] == 'text')
    metadata = provenance(row)
    if descriptor:
        metadata.update(historicalOnly=True, mutationAuthority='none', preservedSource={
            'kind':'context-clear','archiveId':descriptor['archiveId'],'sha256':descriptor['sha256'],'complete':True})
    message = {'id': identity, 'role': 'user', 'text': text,
               '_meta': {'amplifier.dev/history': metadata}}
    result = {'revision': revision, 'selection': 'exact-message', 'message': message,
              'complete': True, 'execution': False}
    if len(json.dumps(result, ensure_ascii=False).encode()) > 65536:
        raise HistoryError('Historical message exceeds the complete-source byte bound')
    return result


def provenance(row):
    metadata=row.get('metadata') or {};marker=metadata.get('amplifier_input') or {}
    result={'authorization':'unverified-native-history','inputOrigin':'unknown'}
    # Only recorded facts. Input(kind=user) is not proof of current human consent.
    for name in ('inputOrigin','activeInputId','questionId','observationId','scheduledRunId','voiceId','voiceItemId','createdAt'):
        value=metadata.get(name,row.get(name))
        if value is None and name=='createdAt':value=metadata.get('timestamp')
        if isinstance(value,str) and len(value)<=8192:result[name]=value
        elif name=='createdAt' and type(value) in (int,float):result[name]=value
    delivery = metadata.get('voiceDelivery')
    peer = metadata.get('peerEnvelope')
    if metadata.get('inputOrigin') == 'peer' and isinstance(peer, dict) and len(json.dumps(peer).encode()) <= 12000:
        result['peerEnvelope'] = peer
    if isinstance(delivery, dict) and delivery.get('version') == 1:
        result['voiceDelivery'] = {key:value for key,value in delivery.items() if key != 'context'}
    if result.get('inputOrigin') == 'peer' and isinstance(peer, dict) and peer.get('mode') == 'notify' and metadata.get('passive') is True:
        result['recordedOnly'] = True
    feedback = metadata.get('amplifier_context_event')
    if isinstance(feedback, dict) and feedback.get('kind') == 'reaction' and len(json.dumps(feedback).encode()) <= 32768:
        result['feedbackEvent'] = feedback
        result['recordedOnly'] = True
    quote = metadata.get('replyTo')
    if isinstance(quote, dict) and len(json.dumps(quote).encode()) <= 32768:
        result['replyTo'] = quote
    if type(metadata.get('questionRevision')) is int:result['questionRevision']=metadata['questionRevision']
    if isinstance(metadata.get('answerProvenance'),dict) and len(json.dumps(metadata['answerProvenance']).encode())<=8192:result['answerProvenance']=metadata['answerProvenance']
    native={key:value for key,value in marker.items() if key in {'id','kind','source','call_id'} and isinstance(value,str) and len(value)<=8192}
    if type(marker.get('version')) is int and marker['version']==1:native['version']=marker['version']
    if native:result['nativeInput']=native
    return result

def steering_target(row):
    """Recorded association only; missing or conflicting facts remain unknown."""
    metadata=row.get('metadata') or {};marker=metadata.get('amplifier_input') or {}
    target=metadata.get('activeInputId')
    if (row.get('role')=='user' and marker.get('kind')=='steer'
            and (metadata.get('inputOrigin')=='steering' or
                 (metadata.get('inputOrigin')=='peer' and
                  isinstance(metadata.get('peerEnvelope'),dict) and
                  metadata['peerEnvelope'].get('mode')=='steer'))
            and isinstance(target,str) and 0<len(target)<=8192):
        return target
    return None


def passive_peer(row):
    metadata=row.get('metadata') or {};event=metadata.get('amplifier_context_event') or {}
    return (row.get('role')=='user' and metadata.get('inputOrigin')=='peer'
            and metadata.get('passive') is True and event.get('kind')=='peer-notification')


def starts_turn(row,current):
    if passive_peer(row):return current is None
    if current is None or (row.get('metadata') or {}).get('amplifier_voice') or (row.get('metadata') or {}).get('amplifier_context_event'):return True
    return row.get('role')=='user' and steering_target(row)!=current


def visible(row):
    metadata=row.get('metadata') or {}
    marker=metadata.get('amplifier_input') or {}
    return (row.get('role') in {'user','assistant','tool'} and (metadata.get('ephemeral') is not True or metadata.get('amplifier_context_event', {}).get('kind') in {'reaction', 'peer-notification'})
            and marker.get('kind','user') in {'user','steer'} and marker.get('source','user')=='user')

def content_updates(row,position):
    """Public transcript projection; hidden reasoning and system prompts stay private."""
    role=row.get('role');blocks=row.get('content','');updates=[];diagnostics=[]
    voice=(row.get('metadata') or {}).get('amplifier_voice')
    meta={'amplifier.dev/history':{'position':position,**provenance(row),**({'via':'call','callId':voice['callId'],'itemId':voice['itemId'],'recordedOnly':True} if voice else {})}}
    if not visible(row):return updates,diagnostics
    if role=='tool':
        identity=row.get('tool_call_id')
        if identity:
            updates.append({'sessionUpdate':'tool_call_update','toolCallId':identity,'status':'completed','rawOutput':blocks,'_meta':meta})
        else:diagnostics.append({'code':'unassociated_tool_result','position':position})
        return updates,diagnostics
    # starts_turn keeps passive notices inside the generation's original turn;
    # the chunk still preserves the canonical row role and peer attribution.
    kind='user_message_chunk' if role=='user' else 'agent_message_chunk'
    from .public_message import row_envelope
    try:original=row_envelope(row)
    except ValueError:
        return [],[{'code':'invalid_public_message','position':position}]
    if original is not None:
        for index,block in enumerate(original['blocks']):
            facts={**meta['amplifier.dev/history'],'originalBlockIndex':index,'publicProjectionVersion':1}
            public=({'type':'text','text':block['text']} if block['type']=='text' else
                    {'type':'resource_link','uri':block['reference']['id'],'name':block['name'],'mimeType':block['mimeType']})
            updates.append({'sessionUpdate':kind,'content':public,'_meta':{'amplifier.dev/history':facts}})
        return updates,diagnostics
    if isinstance(blocks,str):blocks=[{'type':'text','text':blocks}]
    if not isinstance(blocks,list):raise HistoryError('Malformed native message content.')
    # Calls carry inputs; only a recorded role:tool result completes them.
    for block in blocks:
        if not isinstance(block,dict):raise HistoryError('Malformed native content block.')
        tag=block.get('type')
        if tag in {'text','output_text','input_text'} and isinstance(block.get('text'),str):
            if block.get('visibility') in {'internal','developer'} or not block['text']:continue
            updates.append({'sessionUpdate':kind,'content':{'type':'text','text':block['text']},'_meta':meta})
        elif tag=='image':
            value=block.get('source') or {}
            if value.get('type')=='base64' and isinstance(value.get('data'),str):
                updates.append({'sessionUpdate':kind,'content':{'type':'image','data':value['data'],'mimeType':value.get('media_type','image/png')},'_meta':meta})
            elif value.get('url'):
                updates.append({'sessionUpdate':kind,'content':{'type':'resource_link','uri':value['url'],'name':'Image'},'_meta':meta})
            else:diagnostics.append({'code':'unsupported_image_encoding','position':position})
        elif tag=='image_url' and isinstance(block.get('image_url'),dict):
            updates.append({'sessionUpdate':kind,'content':{'type':'resource_link','uri':block['image_url']['url'],'name':'Image'},'_meta':meta})
        elif tag in {'tool_call','tool_use'}:
            identity=block.get('id') or block.get('tool_call_id')
            if identity:updates.append({'sessionUpdate':'tool_call','toolCallId':identity,'title':block.get('name') or block.get('tool') or 'Tool','status':'in_progress','rawInput':block.get('arguments',block.get('input',{})),'_meta':meta})
        elif tag in {'thinking','redacted_thinking','reasoning'}:continue
        else:diagnostics.append({'code':'unsupported_content_type','type':tag,'position':position})
    for call in row.get('tool_calls') or []:
        fn=call.get('function') or call
        arguments=fn.get('arguments',{})
        if isinstance(arguments,str):
            try:arguments=json.loads(arguments)
            except ValueError:pass
        if call.get('id') and not any(u.get('toolCallId')==call['id'] for u in updates):
            updates.append({'sessionUpdate':'tool_call','toolCallId':call['id'],'title':fn.get('name') or fn.get('tool') or 'Tool','status':'in_progress','rawInput':arguments,'_meta':meta})
    return updates,diagnostics

def _cursor(revision,offset):
    return base64.urlsafe_b64encode(json.dumps({'revision':revision,'offset':offset},separators=(',',':')).encode()).decode().rstrip('=')

def message_id(row, position, epoch=None):
    digest=hashlib.sha256((str(position)+':'+json.dumps(row,sort_keys=True,separators=(',',':'))).encode()).hexdigest()[:24]
    voice=(row.get('metadata') or {}).get('amplifier_voice')
    if voice:digest=hashlib.sha256((voice['callId']+'\0'+voice['itemId']).encode()).hexdigest()[:24]
    return 'native:'+(epoch+':' if epoch else '')+digest


def message_reference(directory, row, position, epoch, *, archive_id=None):
    # Streaming chunks carry no inferred locator. Only confirmed current native
    # history projection supplies this bounded reference and public-text digest.
    if archive_id or row.get('role') not in {'user','assistant'}:return None
    values,_=content_updates(row,position)
    text=''.join(value['content']['text'] for value in values if value.get('content',{}).get('type')=='text')
    if not text.strip():return None
    return {'sessionId':Path(directory).name,'messageId':message_id(row,position,epoch),
            'position':position,'historyEpoch':epoch,'sourceDigest':hashlib.sha256(text.encode()).hexdigest()}


class SegmentIntegrity:
    """Verify only touched immutable chunks; portable across exact-byte restore."""
    def __init__(self, directory, size, budget):
        self.path=Path(directory)/'transcript.jsonl.sha256';self.size=size
        self.budget=budget;self.used=0;self.blocks={};self.index_revision=stamp(self.path)[0]
    def read(self, stream, offset, length):
        result=[]
        for number in range(offset//65536,(offset+length+65535)//65536):
            if number not in self.blocks:
                start=number*65536;amount=min(65536,self.size-start)
                if self.used+amount>self.budget:raise HistoryError('History page contains oversized records; request fewer rows.')
                stream.seek(start);block=stream.read(amount);self.used+=len(block)
                with self.path.open('rb') as index:
                    index.seek(number*32);expected=index.read(32)
                if hashlib.sha256(block).digest()!=expected or stamp(self.path)[0]!=self.index_revision:
                    raise HistoryError('Preserved context segment changed; originals retained.')
                self.blocks[number]=block
            block=self.blocks[number];start=number*65536
            result.append(block[max(0,offset-start):min(len(block),offset+length-start)])
        return b''.join(result)


def _page(directory,*,cursor=None,limit=50,max_bytes=2_000_000,expected_revision=None,epoch=None,archive_id=None,include_public_rows=False,session_id=None):
    if type(limit) is not int or not 1<=limit<=200:raise HistoryError('History turn limit must be an integer from 1 to 200.')
    path,diagnostics=source(Path(directory));revision,size=stamp(path);end=size
    if expected_revision is not None and revision!=expected_revision:raise HistoryError('Native history changed since inspection; request a fresh revision.')
    if cursor:
        if not isinstance(cursor,str) or len(cursor)>512:raise HistoryError('Invalid history cursor.')
        try:
            value=json.loads(base64.urlsafe_b64decode(cursor+'='*(-len(cursor)%4)))
            if value['revision']!=revision:raise HistoryError('History changed; discard this cursor and load a fresh page.')
            end=value['offset']
            if type(end) is not int or not 0<=end<=size:raise ValueError()
        except HistoryError:raise
        except (ValueError,KeyError,TypeError):raise HistoryError('Invalid history cursor.')
    integrity=SegmentIntegrity(directory,size,max_bytes) if archive_id else None
    rows=[];buffer=b'';offset=end;consumed=end;read_bytes=0;turns=0
    with path.open('rb') as stream:
        while turns<limit and (offset or buffer):
            if offset:
                amount=min(offset,((offset-1)%65536)+1) if integrity else min(offset,65536)
                offset-=amount;stream.seek(offset);chunk=integrity.read(stream,offset,amount) if integrity else stream.read(amount)
                read_bytes+=len(chunk)
                if read_bytes>max_bytes:raise HistoryError('History page contains oversized records; request fewer rows or inspect the native artifact explicitly.')
                buffer=chunk+buffer
            # A complete newest line is available when preceded by a newline or byte zero.
            while turns<limit:
                clean=buffer.rstrip(b'\n\r')
                split=clean.rfind(b'\n')
                if split<0 and offset:break
                raw=clean[split+1:];position=offset+split+1
                if not raw:buffer=b'';break
                try:row=json.loads(raw)
                except ValueError:raise HistoryError('Native transcript contains a malformed record; history was preserved.')
                if not isinstance(row,dict) or not isinstance(row.get('role'),str):raise HistoryError('Native transcript record has no role.')
                rows.append((position,row));turns+=int(row.get("role")=="user" and visible(row) and not passive_peer(row) and steering_target(row) is None);consumed=position;buffer=clean[:split+1] if split>=0 else b''
                if not buffer:break
            if offset==0 and not buffer:break
    if stamp(path)[0]!=revision:raise HistoryError('Native history changed during read; retry the read with a fresh cursor.')
    updates=[];groups=[];group_positions=[];current=None;public_rows=[];plan_calls={}
    from .public_message import public_row,locator
    for position,row in reversed(rows):
        if include_public_rows:
            try:original=public_row(session_id or Path(directory).name,row,position,epoch)
            except ValueError:
                original=None
                diagnostics.append({'code':'invalid_public_row','position':position})
            if original is not None:public_rows.append(original)
        values,issues=content_updates(row,position);diagnostics.extend(issues)
        if not values:continue
        marker=(row.get('metadata') or {}).get('amplifier_input') or {}
        identity=message_id(row,position,epoch)
        voice=(row.get('metadata') or {}).get('amplifier_voice')
        if starts_turn(row,current['id'] if current else None):
            current={'id':marker.get('id') or identity,'updates':[],'state':'unknown',**({'recordedOnly':True} if voice or (row.get('metadata') or {}).get('amplifier_context_event') else {}),**({'archiveId':archive_id,'historicalOnly':True} if archive_id else {})}
            groups.append(current);group_positions.append(position)
            plan_calls.clear()
        from .plan import history_plans
        values.extend(history_plans(values, plan_calls))
        reference=message_reference(directory,row,position,epoch,archive_id=archive_id)
        for value in values:
            if include_public_rows and (row.get('metadata') or {}).get('amplifier_public_message') is not None:
                value['_meta']['amplifier.dev/history']['rowLocator']=locator(session_id or Path(directory).name,row,position,epoch)
            if reference:value['_meta']['amplifier.dev/history']['messageReference']=reference
            value['_meta']['amplifier.dev/history'].update(turnId=current['id'],messageId=('archive:'+archive_id+':' if archive_id else '')+identity,**({'archiveId':archive_id,'historicalOnly':True,'mutationAuthority':'none'} if archive_id else {}))
        current['updates'].extend(values);updates.extend(values)
    # The reverse window can include an orphan or mismatched steering row.
    # Keep the requested newest complete groups and leave exact older bytes for
    # the next cursor instead of exceeding the complete-turn page contract.
    if len(groups)>limit:
        groups=groups[-limit:];consumed=group_positions[-limit]
        updates=[value for group in groups for value in group['updates']]
        public_rows=[row for row in public_rows if row['locator']['position']>=consumed]
    return {'updates':updates,'turnGroups':groups,'nextCursor':_cursor(revision,consumed) if consumed>0 else None,'revision':revision,'diagnostics':diagnostics,**({'publicRows':public_rows,'publicRowsComplete':consumed==0 and not diagnostics} if include_public_rows else {})}

def page(directory,*,cursor=None,limit=50,max_bytes=2_000_000,expected_revision=None,app_home=None,history_cwd=None,include_public_rows=False):
    if app_home is None:
        return _page(directory,cursor=cursor,limit=limit,max_bytes=max_bytes,expected_revision=expected_revision,include_public_rows=include_public_rows)
    from .native.history_revision import context_head,segment,_directory,_read
    directory=Path(directory);path,_=source(directory);revision,_=stamp(path)
    head=context_head(app_home,directory.name,history_cwd);archive=None;inner=cursor
    if expected_revision is not None and revision!=expected_revision:raise HistoryError('Native history changed since inspection.')
    if cursor:
        if not isinstance(cursor,str) or len(cursor)>2048:raise HistoryError('Invalid history cursor.')
        try:value=json.loads(base64.urlsafe_b64decode(cursor+'='*(-len(cursor)%4)))
        except (ValueError,TypeError):raise HistoryError('Invalid history cursor.')
        if value.get('kind')=='context-history':
            if value.get('revision')!=revision or value.get('head')!=head or value.get('session')!=directory.name:raise HistoryError('History changed; discard this cursor.')
            archive=value.get('archive');inner=value.get('cursor')
    def continuation(selected,selected_cursor):
        value={'kind':'context-history','revision':revision,'head':head,'session':directory.name,'archive':selected,'cursor':selected_cursor}
        return base64.urlsafe_b64encode(json.dumps(value,separators=(',',':')).encode()).decode().rstrip('=')
    # At most one nonempty segment per page; empty segments are traversed with
    # a fixed budget, not a scan of all selected history on every read.
    for _ in range(32):
        selected=directory;epoch=head;previous=head
        if archive:
            selected,descriptor=segment(app_home,directory.name,archive)
            receipt=_read(_directory(app_home,directory.name)/('context-'+archive+'.json'))
            if receipt.get('status')!='succeeded':raise HistoryError('Context clear is unresolved; preserved originals require exact receipt inspection.')
            epoch=descriptor['sourceEpoch'];previous=descriptor['previousArchive']
        result=_page(selected,cursor=inner,limit=limit,max_bytes=max_bytes,epoch=epoch,archive_id=archive,include_public_rows=include_public_rows,session_id=directory.name)
        next_cursor=(continuation(archive,result['nextCursor']) if result['nextCursor'] else continuation(previous,None) if previous else None)
        result.update(revision=revision,nextCursor=next_cursor)
        if include_public_rows:result['publicRowsComplete']=next_cursor is None and not result['diagnostics']
        if stamp(path)[0]!=revision or context_head(app_home,directory.name,history_cwd)!=head:raise HistoryError('History changed during read.')
        if result['turnGroups'] or result.get('publicRows') or not next_cursor:return result
        archive=previous;inner=None
    return {'updates':[],'turnGroups':[],'revision':revision,'nextCursor':continuation(archive,inner),'diagnostics':[{'code':'empty_segment_page_bound'}],**({'publicRows':[],'publicRowsComplete':False} if include_public_rows else {})}


def _forward(directory,*,epoch=None,archive_id=None):
    path,diagnostics=source(Path(directory));revision,_=stamp(path)
    current=None
    integrity=SegmentIntegrity(directory,path.stat().st_size,48_065_536) if archive_id else None
    with path.open('rb') as stream:
        while True:
            position=stream.tell();raw=stream.readline(48_000_001)
            if not raw:break
            if len(raw)>48_000_000:raise HistoryError('Native history record exceeds the ACP transport limit.')
            if integrity:
                end=stream.tell();integrity.read(stream,position,len(raw));stream.seek(end)
                integrity.blocks={number:block for number,block in integrity.blocks.items() if (number+1)*65536>end}
                integrity.used=sum(map(len,integrity.blocks.values()))
            try:row=json.loads(raw)
            except ValueError:raise HistoryError('Native transcript is malformed; history was preserved.')
            if not isinstance(row,dict):raise HistoryError('Native transcript record is malformed.')
            values,issues=content_updates(row,position)
            if issues:raise HistoryError('Native history contains content without an ACP mapping: '+json.dumps(issues))
            if values:
                marker=(row.get('metadata') or {}).get('amplifier_input') or {}
                identity=message_id(row,position,epoch)
                if starts_turn(row,current):
                    current=marker.get('id') or identity
                reference=message_reference(directory,row,position,epoch,archive_id=archive_id)
                for update in values:
                    if reference:update['_meta']['amplifier.dev/history']['messageReference']=reference
                    update['_meta']['amplifier.dev/history'].update(turnId=current,messageId=('archive:'+archive_id+':' if archive_id else '')+identity,**({'archiveId':archive_id,'historicalOnly':True,'mutationAuthority':'none'} if archive_id else {}))
                    yield update
    if stamp(path)[0]!=revision:raise HistoryError('Native history changed during replay; resubscribe to obtain a consistent view.')


def forward(directory,*,app_home=None,history_cwd=None):
    if app_home is None:
        yield from _forward(directory)
        return
    from .native.history_revision import context_head,segment,_directory,_read
    directory=Path(directory);head=context_head(app_home,directory.name,history_cwd);archive=head;chain=[]
    while archive:
        if len(chain)>=10000 or archive in chain:raise HistoryError('History segment chain exceeds replay bound or is corrupt; use bounded paging.')
        selected,descriptor=segment(app_home,directory.name,archive)
        if _read(_directory(app_home,directory.name)/('context-'+archive+'.json')).get('status')!='succeeded':raise HistoryError('Context clear is unresolved; inspect its exact receipt.')
        chain.append(archive);archive=descriptor['previousArchive']
    for archive in reversed(chain):
        selected,descriptor=segment(app_home,directory.name,archive)
        yield from _forward(selected,epoch=descriptor['sourceEpoch'],archive_id=archive)
    yield from _forward(directory,epoch=head)
    if context_head(app_home,directory.name,history_cwd)!=head:raise HistoryError('History changed during replay.')
