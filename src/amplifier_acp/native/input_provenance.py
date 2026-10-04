"""Adapter-owned input provenance, persisted before admission without module patches."""
import json
from contextlib import closing
from amplifier_acp.journal_schema import connect
from pathlib import Path

class Context:
    """Delegate the public context contract, annotating only exact admitted IDs."""
    def __init__(self,previous,path):self.previous=previous;self.path=Path(path)
    def __getattr__(self,name):return getattr(self.previous,name)
    async def add_message(self,row):
        identity=(row.get('metadata') or {}).get('amplifier_input',{}).get('id')
        if identity:
            db=connect(self.path,'inputs')
            if db is not None:
                with closing(db):found=db.execute('SELECT body FROM provenance WHERE id=?',(identity,)).fetchone()
                if found:row={**row,'metadata':{**(row.get('metadata') or {}),**json.loads(found[0])}}
        if identity and row.get('role') == 'user':
            from amplifier_acp.message_metadata import input_quote
            quote = input_quote(self.path.with_name('message-interactions.sqlite3'), identity)
            if quote:
                row = {**row, 'metadata': {**(row.get('metadata') or {}), 'replyTo': quote}}
        return await self.previous.add_message(row)

def reserve(path,identity,metadata):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if path.is_symlink():raise ValueError('Input provenance storage cannot be a symlink')
    body=json.dumps(metadata,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
    if len(body.encode())>16384:raise ValueError('Input provenance exceeds its bound')
    with closing(connect(path,'inputs',write=True)) as db:
        with db:
            if db.execute('SELECT 1 FROM provenance WHERE id=?',(identity,)).fetchone():raise ValueError('This input was already reserved; uncertain work is not replayed')
            db.execute('INSERT INTO provenance VALUES (?,?)',(identity,body))


def reserved(path,identity):
    db=connect(path,'inputs')
    if db is None:return False
    with closing(db):return db.execute('SELECT 1 FROM provenance WHERE id=?',(identity,)).fetchone() is not None
