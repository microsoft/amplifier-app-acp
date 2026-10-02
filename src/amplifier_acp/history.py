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
    if primary.exists():return primary,[]
    if backup.exists():return backup,[{'code':'backup_read','message':'Primary transcript is missing; reading preserved backup without changing it.'}]
    raise HistoryError('Native history does not exist.')

def visible(row):
    metadata=row.get('metadata') or {}
    marker=metadata.get('amplifier_input') or {}
    return (row.get('role') in {'user','assistant','tool'} and metadata.get('ephemeral') is not True
            and marker.get('kind','user') in {'user','steer'} and marker.get('source','user')=='user')

def content_updates(row,position):
    """Public transcript projection; hidden reasoning and system prompts stay private."""
    role=row.get('role');blocks=row.get('content','');updates=[];diagnostics=[]
    meta={'amplifier.dev/history':{'position':position}}
    if not visible(row):return updates,diagnostics
    if role=='tool':
        identity=row.get('tool_call_id')
        if identity:
            updates.append({'sessionUpdate':'tool_call_update','toolCallId':identity,'status':'completed','rawOutput':blocks,'_meta':meta})
        else:diagnostics.append({'code':'unassociated_tool_result','position':position})
        return updates,diagnostics
    kind='user_message_chunk' if role=='user' else 'agent_message_chunk'
    if isinstance(blocks,str):blocks=[{'type':'text','text':blocks}]
    if not isinstance(blocks,list):raise HistoryError('Malformed native message content.')
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
            if identity:updates.append({'sessionUpdate':'tool_call','toolCallId':identity,'title':block.get('name','Tool'),'status':'completed','rawInput':block.get('arguments',block.get('input',{})),'_meta':meta})
        elif tag in {'thinking','redacted_thinking','reasoning'}:continue
        else:diagnostics.append({'code':'unsupported_content_type','type':tag,'position':position})
    for call in row.get('tool_calls') or []:
        fn=call.get('function') or call
        arguments=fn.get('arguments',{})
        if isinstance(arguments,str):
            try:arguments=json.loads(arguments)
            except ValueError:pass
        if call.get('id') and not any(u.get('toolCallId')==call['id'] for u in updates):
            updates.append({'sessionUpdate':'tool_call','toolCallId':call['id'],'title':fn.get('name','Tool'),'status':'completed','rawInput':arguments,'_meta':meta})
    return updates,diagnostics

def _cursor(revision,offset):
    return base64.urlsafe_b64encode(json.dumps({'revision':revision,'offset':offset},separators=(',',':')).encode()).decode().rstrip('=')

def page(directory,*,cursor=None,limit=50,max_bytes=2_000_000):
    if type(limit) is not int or not 1<=limit<=200:raise HistoryError('History turn limit must be an integer from 1 to 200.')
    path,diagnostics=source(Path(directory));revision,size=stamp(path);end=size
    if cursor:
        if not isinstance(cursor,str) or len(cursor)>512:raise HistoryError('Invalid history cursor.')
        try:
            value=json.loads(base64.urlsafe_b64decode(cursor+'='*(-len(cursor)%4)))
            if value['revision']!=revision:raise HistoryError('History changed; discard this cursor and load a fresh page.')
            end=value['offset']
            if type(end) is not int or not 0<=end<=size:raise ValueError()
        except HistoryError:raise
        except (ValueError,KeyError,TypeError):raise HistoryError('Invalid history cursor.')
    rows=[];buffer=b'';offset=end;consumed=end;read_bytes=0;turns=0
    with path.open('rb') as stream:
        while turns<limit and (offset or buffer):
            if offset:
                amount=min(offset,65536);offset-=amount;stream.seek(offset);chunk=stream.read(amount)
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
                rows.append((position,row));turns+=int(row.get("role")=="user" and visible(row));consumed=position;buffer=clean[:split+1] if split>=0 else b''
                if not buffer:break
            if offset==0 and not buffer:break
    if stamp(path)[0]!=revision:raise HistoryError('Native history changed during read; retry the read with a fresh cursor.')
    updates=[];groups=[];current=None
    for position,row in reversed(rows):
        values,issues=content_updates(row,position);diagnostics.extend(issues)
        if not values:continue
        marker=(row.get('metadata') or {}).get('amplifier_input') or {}
        digest=hashlib.sha256((str(position)+':'+json.dumps(row,sort_keys=True,separators=(',',':'))).encode()).hexdigest()[:24]
        if row.get('role')=='user' or current is None:
            current={'id':marker.get('id') or 'native:'+digest,'updates':[],'state':'unknown'}
            groups.append(current)
        for value in values:
            value['_meta']['amplifier.dev/history'].update(turnId=current['id'],messageId='native:'+digest)
        current['updates'].extend(values);updates.extend(values)
    return {'updates':updates,'turnGroups':groups,'nextCursor':_cursor(revision,consumed) if consumed>0 else None,'revision':revision,'diagnostics':diagnostics}

def forward(directory):
    path,diagnostics=source(Path(directory));revision,_=stamp(path)
    with path.open('rb') as stream:
        while True:
            position=stream.tell();raw=stream.readline(48_000_001)
            if not raw:break
            if len(raw)>48_000_000:raise HistoryError('Native history record exceeds the ACP transport limit.')
            try:row=json.loads(raw)
            except ValueError:raise HistoryError('Native transcript is malformed; history was preserved.')
            if not isinstance(row,dict):raise HistoryError('Native transcript record is malformed.')
            values,issues=content_updates(row,position)
            if issues:raise HistoryError('Native history contains content without an ACP mapping: '+json.dumps(issues))
            for update in values:yield update
    if stamp(path)[0]!=revision:raise HistoryError('Native history changed during replay; resubscribe to obtain a consistent view.')
