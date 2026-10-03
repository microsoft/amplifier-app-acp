"""Explicit maintenance owner images at a pre-seal cutoff, never current output.

Receipt and plan databases use SQLite backup. Other private records and optional
historical archive bodies stream to disk. The current plan is excluded before
walking any tree, so its newly created output cannot recursively enter itself.
"""
from __future__ import annotations
import hashlib,json,os,time
from pathlib import Path
from .maintenance import CHUNK,file_stamp
from .maintenance_gate import checked

RECORDS='native-maintenance-records'
BODIES='native-retained-archives'
PARTS={RECORDS,BODIES}

def prepare(owner,plan,parts,directory,bounded,remaining,backup,command_id):
    selected=set(parts)&PARTS
    if not selected:return []
    if BODIES in selected and RECORDS not in selected:raise ValueError('Retained archive bodies require their maintenance records selection')
    roots={part:checked(directory/part) for part in selected}
    for root in roots.values():root.mkdir(mode=0o700)
    info={part:{'bytes':0,'files':0,'databaseImages':0,'digest':hashlib.sha256()} for part in selected}
    count=0;cutoff=time.time()
    def copy(part,label,path,database=False):
        nonlocal remaining,count
        bounded();path=checked(path)
        if not path.exists():return
        stamp=file_stamp(path);count+=1
        if count>owner.archives().max_entries or stamp[2]>remaining:raise ValueError('Maintenance owner snapshot exceeds configured entry/byte limits')
        target=checked(roots[part]/label);target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        if database:
            backup(path,target,remaining,bounded)
        else:
            fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
            try:
                with os.fdopen(fd,'rb',closefd=False) as source,target.open('xb') as output:
                    os.chmod(target,0o600);opened=os.fstat(fd)
                    if [opened.st_dev,opened.st_ino,opened.st_size,opened.st_mtime_ns,opened.st_ctime_ns]!=stamp:raise ValueError('Maintenance source changed while opening')
                    while block:=source.read(CHUNK):bounded();output.write(block)
                    output.flush();os.fsync(output.fileno())
            finally:os.close(fd)
            if file_stamp(path)!=stamp:raise ValueError('Maintenance source changed during cutoff snapshot')
        size=file_stamp(target)[2]
        if size>remaining:raise ValueError('Maintenance owner image exceeds remaining byte budget')
        remaining-=size;digest=hashlib.sha256()
        with target.open('rb') as stream:
            while block:=stream.read(CHUNK):bounded();digest.update(block)
        row={'path':label,'bytes':size,'sha256':digest.hexdigest(),'format':'sqlite-backup-image' if database else 'file'}
        info[part]['digest'].update(json.dumps(row,sort_keys=True,separators=(',',':')).encode()+b'\n')
        info[part]['bytes']+=size;info[part]['files']+=1;info[part]['databaseImages']+=int(database)

    def walk(part,label,path,depth=0):
        nonlocal count
        bounded();path=checked(path)
        if not path.exists():return
        if depth>64:raise ValueError('Maintenance owner snapshot directory nesting exceeds64')
        if path.is_dir():
            count+=1
            if count>owner.archives().max_entries:raise ValueError('Maintenance owner directory count exceeds configured entry limit')
            with os.scandir(path) as entries:
                for entry in entries:walk(part,label+'/'+entry.name,Path(entry.path),depth+1)
        elif path.name.endswith(('-shm','-wal','-journal')):
            database=Path(str(path).rsplit('-',1)[0])
            if not checked(database).is_file() or path.name.endswith('-journal'):raise ValueError('Unresolved SQLite sidecar requires owner inspection before backup')
            return
        elif path.name.endswith('.lock'):return
        else:copy(part,label,path,database=path.suffix=='.sqlite3')

    def children(path):
        nonlocal count
        bounded();path=checked(path)
        if not path.exists():return
        if not path.is_dir():raise ValueError('Maintenance owner source directory is unavailable')
        with os.scandir(path) as entries:
            for entry in entries:
                bounded();count+=1
                if count>owner.archives().max_entries:raise ValueError('Maintenance owner inspection exceeds configured entry limit')
                yield checked(Path(entry.path))

    def known(path,names):
        for entry in children(path):
            if entry.name not in names:raise ValueError('Unknown maintenance-owner entry requires explicit coverage before backup')

    # Only known owner roots. Unknown additions require a deliberate versioned
    # policy; do not sweep arbitrary app-home files into a native archive.
    known(owner.root,{'receipts.sqlite3','receipts.sqlite3-wal','receipts.sqlite3-shm','plans','previews','retained','artifacts'})
    copy(RECORDS,'receipts.sqlite3',owner.root/'receipts.sqlite3',True)
    walk(RECORDS,'previews',owner.root/'previews')
    walk(RECORDS,'retained',owner.root/'retained')
    for source in children(owner.root/'plans'):
        if source.name==plan:continue
        from .maintenance import identity
        identity(source.name)
        if not source.is_dir():raise ValueError('Invalid maintenance plan directory')
        known(source,{'manifest.sqlite3','manifest.sqlite3-wal','manifest.sqlite3-shm','sealed.json','native-stores'})
        copy(RECORDS,'plans/'+source.name+'/manifest.sqlite3',source/'manifest.sqlite3',True)
        copy(RECORDS,'plans/'+source.name+'/sealed.json',source/'sealed.json')
        if BODIES in selected:walk(BODIES,'plans/'+source.name+'/native-stores',source/'native-stores')
    for source in children(owner.root/'artifacts'):
        from .maintenance import identity
        identity(source.name)
        if not source.is_dir():raise ValueError('Invalid retained maintenance artifact directory')
        known(source,{'description.json','manifest.json','snapshot.tar','snapshot.partial.tar'})
        for name in ('description.json','manifest.json'):copy(RECORDS,'artifacts/'+source.name+'/'+name,source/name)
        if BODIES in selected:
            for name in ('snapshot.tar','snapshot.partial.tar'):copy(BODIES,'artifacts/'+source.name+'/'+name,source/name)
    result=[]
    for part in sorted(selected):
        row=info[part]
        # Artifact/member hashes are in the paged plan. This compact root digest
        # identifies the immutable owner image without returning its file list.
        result.append({'part':part,'owner':'native-maintenance','sourceRevision':row['digest'].hexdigest(),'scope':'pre-existing-records-in-configured-native-app-home','format':'owner-snapshot-tree','status':'included','bytes':row['bytes'],'files':row['files'],'databaseImages':row['databaseImages'],'cutoff':{'capturedAt':cutoff,'phase':'before-current-plan-seal','excludedPlanId':plan,'prepareCommandId':command_id,'currentPrepareReceipt':'running-is-uncertain-never-replay','artifactBodies':'included' if BODIES in selected else 'not-selected'}})
    # Persist generated directory entries as well as file contents. Tree depth
    # and entry/byte bounds were enforced while copying; no hot file inventory.
    def sync(root):
        bounded()
        with os.scandir(root) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):sync(Path(entry.path))
        fd=os.open(root,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    for root in roots.values():sync(root)
    return result
