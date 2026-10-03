"""Bounded selected managed-project evidence under an exclusive native writer gate.

This is an explicit disposal check, never a hot catalog or transcript scan.
Canonical metadata is independently compared with the product's reviewed family.
"""
import hashlib,json,os,stat,time
from .native.session_files import validate_id

MAX_SESSIONS=101
MAX_METADATA=32768
MAX_TOTAL=2*1024*1024


def verify(owner,hints):
    from .managed_disposal import Refused,open_directory,stamp
    if not isinstance(hints,list) or len(hints)>=MAX_SESSIONS:
        raise Refused('Managed family exceeds the complete metadata bound')
    expected={}
    for row in hints:
        if not isinstance(row,dict) or set(row)!={'sessionId','cwd','parentSessionId'} or row['cwd']!=owner.cwd:
            raise Refused('Managed family requires exact same-allocation identities')
        sid=validate_id(row['sessionId']);validate_id(row['parentSessionId'])
        if sid in expected or sid==owner.identity:raise Refused('Managed family contains duplicate identities')
        expected[sid]=row['parentSessionId']
    found={};locks=set();observations=[];total=0;scanned=0;deadline=time.monotonic()+5
    with open_directory(owner.directory.parent) as directory_fd:
        before=stamp(os.fstat(directory_fd))
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                scanned+=1
                if time.monotonic()>deadline or scanned>MAX_SESSIONS*2:
                    raise Refused('Selected managed project exceeds bounded complete-family inspection')
                # SessionHistoryStore keeps its per-session metadata lock beside
                # session directories. It is retained authority, not a child.
                if entry.name.startswith('.') and entry.name.endswith('.metadata.lock'):
                    locked=validate_id(entry.name[1:-len('.metadata.lock')]);info=entry.stat(follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>4096:raise Refused('Native metadata lock is not a bounded private regular file')
                    locks.add(locked);continue
                if len(found)>=MAX_SESSIONS:raise Refused('Selected managed project exceeds bounded complete-family inspection')
                try:sid=validate_id(entry.name)
                except ValueError:raise Refused('Selected managed project contains an unrecognized history identity') from None
                info=entry.stat(follow_symlinks=False)
                if not stat.S_ISDIR(info.st_mode):raise Refused('Selected managed history must be a private directory')
                fd=os.open(entry.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory_fd)
                try:
                    if (info.st_dev,info.st_ino)!=(os.fstat(fd).st_dev,os.fstat(fd).st_ino):raise Refused('Selected managed history changed during inspection')
                    mfd=os.open('metadata.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
                    try:
                        observed=os.fstat(mfd)
                        if not stat.S_ISREG(observed.st_mode) or observed.st_nlink!=1 or observed.st_size>MAX_METADATA:
                            raise Refused('Selected managed metadata is unsafe or oversized')
                        raw=os.read(mfd,MAX_METADATA+1);total+=len(raw)
                        if len(raw)!=observed.st_size or len(raw)>MAX_METADATA or total>MAX_TOTAL or stamp(observed)!=stamp(os.fstat(mfd)):
                            raise Refused('Selected managed metadata changed or exceeds its budget')
                        if stamp(observed)!=stamp(os.stat('metadata.json',dir_fd=fd,follow_symlinks=False)):
                            raise Refused('Selected managed metadata was replaced during inspection')
                    finally:os.close(mfd)
                finally:os.close(fd)
                try:value=json.loads(raw)
                except (ValueError,RecursionError):raise Refused('Selected managed metadata is incomplete') from None
                if not isinstance(value,dict) or value.get('session_id')!=sid or value.get('working_dir',value.get('cwd',owner.cwd))!=owner.cwd:
                    raise Refused('Selected managed metadata identity differs from its allocation')
                parent=value.get('parent_id')
                if parent is not None:validate_id(parent)
                if sid==owner.identity:
                    if parent is not None:raise Refused('Managed allocation root unexpectedly has a parent')
                elif parent is None:raise Refused('Independent native history shares this allocation; file disposal is protected')
                found[sid]=parent
                observations.append((sid,stamp(observed),hashlib.sha256(raw).hexdigest()))
        if before!=stamp(os.fstat(directory_fd)):raise Refused('Selected managed project changed during family inspection')
    if owner.identity not in found:raise Refused('Managed root metadata is missing')
    if not locks.issubset(found):raise Refused('Managed project retains an unresolved native creation lock')
    actual={sid:parent for sid,parent in found.items() if sid!=owner.identity}
    if actual!=expected:raise Refused('Canonical managed family differs from reviewed descendants; refresh discovery before disposal')
    for sid in actual:
        visited=set();current=sid
        while current!=owner.identity:
            if current in visited or current not in actual:raise Refused('Managed family has a cycle or missing parent')
            visited.add(current);current=actual[current]
    return {'coverage':'complete-selected-managed-project-metadata','revision':hashlib.sha256(json.dumps(sorted(observations),sort_keys=True).encode()).hexdigest(),'metadataRecords':len(found),'metadataBytes':total,'nativeHomeWritersExcluded':True,'externalWriterCoverage':False}
