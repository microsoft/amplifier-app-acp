"""Passive exact provider recording reads; no runtime imports or inference."""
import hashlib, re, stat
from pathlib import Path

def read(home, params):
    if set(params)-{'sessionId','cwd','callId','field','sha256'}:
        raise ValueError('Unexpected model recording fields')
    sid, call, field, digest = (params.get(k) for k in ('sessionId','callId','field','sha256'))
    if not isinstance(sid,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,200}',sid):
        raise ValueError('Exact native session required')
    if not isinstance(call,str) or not re.fullmatch(r'llm:[0-9a-f-]{36}',call) or field not in {'request','response'} or not isinstance(digest,str) or not re.fullmatch(r'[0-9a-f]{64}',digest):
        raise ValueError('Exact recorded call and digest required')
    path=Path(home)/'sessions'/sid/'model-calls'/(call[4:]+'.'+field+'.json')
    for parent in (path.parent,path.parent.parent,path.parent.parent.parent):
        if parent.is_symlink():raise ValueError('Recording directories cannot be symbolic links')
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size>1000000:
        raise ValueError('Recording unavailable or oversized')
    body=path.read_bytes()
    if len(body)>1000000 or hashlib.sha256(body).hexdigest()!=digest:
        raise ValueError('Recording changed; refresh the model call before reading')
    return {'callId':call,'field':field,'sha256':digest,'text':body.decode()}
