"""Owner-made images of explicit adapter stores; never a raw WAL archive."""
from __future__ import annotations
import hashlib,json,os,sqlite3,time
from pathlib import Path
from .maintenance import CHUNK,file_stamp
from .maintenance_gate import checked

STORES={
 'native-import-records':{'owner':'native-history-import','path':('history-imports','imports.sqlite3'),'tables':('commands','uploads')},
 'native-preference-receipts':{'owner':'native-voice-preferences','path':('voice-preferences','receipts.sqlite3'),'tables':('commands',)},
}

def revision(path):
    path=checked(path);wal=checked(Path(str(path)+'-wal'))
    if not path.exists() and wal.exists():raise ValueError('Native store has an orphan WAL; inspect its owner before archiving')
    state={name:file_stamp(target) if target.exists() else None for name,target in [('database',path),('wal',wal)]}
    return hashlib.sha256(json.dumps(state,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def require_admin(owner):
    fence=owner.admin.lifecycle.fence
    if not fence or fence['purpose']!='recovery':raise ValueError('Native private-store snapshots require the exact held recovery administration lease')
    return {key:fence[key] for key in ('fenceId','commandId','leaseId','adminInstanceId')}

def prepare(owner,plan,parts,check):
    """Caller holds the exclusive native writer gate and the recovery admin lease."""
    authority=require_admin(owner);result=[];remaining=owner.maximum;deadline=time.monotonic()+120
    directory=checked(owner.root/'plans'/plan/'native-stores');directory.mkdir(mode=0o700,exist_ok=False)
    def bounded():
        check()
        if require_admin(owner)!=authority:raise ValueError('Native administration lease changed during private snapshot')
        if time.monotonic()>=deadline:raise ValueError('Native private-store snapshot deadline exceeded')
    for part in sorted(set(parts)&STORES.keys()):
        bounded();definition=STORES[part];path=checked(owner.app.joinpath(*definition['path']));before=revision(path)
        row={'part':part,'owner':definition['owner'],'sourceRevision':before,'scope':'all-records-in-configured-native-app-home','format':'sqlite-backup-image','status':'missing'}
        if not path.exists():result.append(row);continue
        if file_stamp(path)[2]>remaining:raise ValueError('Native private store exceeds remaining archive byte budget')
        image=checked(directory/(part+'.sqlite3'))
        # With both owner gates held, an absent WAL cannot acquire new writes.
        # Immutable read avoids SQLite creating an empty WAL/SHM on dormant DBs.
        uri=path.as_uri()+'?mode=ro'+('&immutable=1' if not Path(str(path)+'-wal').exists() else '')
        source=sqlite3.connect(uri,uri=True,timeout=0,isolation_level=None)
        try:
            source.set_progress_handler(lambda:(bounded(),0)[1],10000)
            source.execute('PRAGMA query_only=ON');source.execute('PRAGMA trusted_schema=OFF')
            for name in definition['tables']:
                if source.execute('SELECT type FROM sqlite_master WHERE name=?',(name,)).fetchone()!=('table',):raise ValueError('Native private store schema is unavailable or unsupported')
            page_size=source.execute('PRAGMA page_size').fetchone()[0]
            target=sqlite3.connect(image,timeout=0,isolation_level=None);os.chmod(image,0o600)
            try:
                def progress(status,remaining_pages,total_pages):
                    bounded()
                    if total_pages*page_size>remaining:raise ValueError('Native private-store image exceeds remaining archive byte budget')
                source.backup(target,pages=64,progress=progress,sleep=.01)
                target.execute('PRAGMA journal_mode=DELETE');target.execute('PRAGMA synchronous=FULL');target.execute('PRAGMA trusted_schema=OFF')
                target.set_progress_handler(lambda:(bounded(),0)[1],10000)
                if target.execute('PRAGMA quick_check').fetchone()!=('ok',):raise ValueError('Native private-store image failed integrity validation')
                counts={name:target.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in definition['tables']}
            finally:target.close()
        finally:source.close()
        bounded()
        if revision(path)!=before:raise ValueError('Native private store changed during snapshot preparation')
        size=file_stamp(image)[2]
        if size>remaining:raise ValueError('Native private-store image exceeds remaining archive byte budget')
        remaining-=size;digest=hashlib.sha256()
        with image.open('rb') as stream:
            while chunk:=stream.read(CHUNK):bounded();digest.update(chunk)
            os.fsync(stream.fileno())
        row.update(status='included',bytes=size,sha256=digest.hexdigest(),tables=counts);result.append(row)
    fd=os.open(directory,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
    return result

def verify(owner,rows):
    if rows:require_admin(owner)
    for row in rows:
        definition=STORES[row['part']];path=checked(owner.app.joinpath(*definition['path']))
        if revision(path)!=row['sourceRevision']:raise ValueError('Native private-store source changed after review; prepare a fresh explicit archive')

def coverage(parts,rows):
    selected=set(parts);included=[dict(row) for row in rows]
    omitted=[{'owner':definition['owner'],'part':part,'reason':'not-selected','preventsCompleteNativeCoverage':True} for part,definition in STORES.items() if part not in selected]
    omitted.append({'owner':'native-maintenance','reason':'maintenance commands, archive plans, retained artifacts and reset originals require a separate owner snapshot','preventsCompleteNativeCoverage':True})
    return {'included':included,'omitted':omitted,'completeNativeBackup':False,'completeProductBackup':False}
