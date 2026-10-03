"""Hold actual native ownership for bounded known families during lifecycle work.

Catalog hints are identities, not permission to bypass an owner. Native worker
relationships are included independently. Undiscovered external writers are not
covered by a derived index and must never be described as absent.
"""
from contextlib import ExitStack,contextmanager
from pathlib import Path
import json
from .coordination import Store
from .relocation import home
MAX_FAMILY=100

class OwnershipBusy(ValueError):
    def __init__(self,descendant=False):
        self.data={'executed':False,'replayed':False,'reason':'native-descendant-owned' if descendant else 'native-session-owned'}
        super().__init__('A native descendant is owned by another active process; wait for that work to settle before copying.' if descendant else 'This native session is owned by another active process; wait for that work to settle before changing its lifecycle.')


@contextmanager
def own_descendants(owner,hints,check=None):
    from amplifier_foundation.session.shared_state import SharedSessionStore,SessionBusyError
    from .native.session_files import validate_id
    from .session_lifecycle import record,guard_copy_source
    if not isinstance(hints,list) or len(hints)>MAX_FAMILY:raise ValueError('Selected family exceeds the 100-session ownership bound')
    known={owner.identity:{'sessionId':owner.identity,'cwd':owner.cwd,'parentSessionId':None}}
    def add(value):
        if not isinstance(value,dict) or set(value)!={'sessionId','cwd','parentSessionId'}:raise ValueError('Exact native descendant identity required')
        sid=validate_id(value['sessionId']);parent=validate_id(value['parentSessionId']);cwd=Path(value['cwd'])
        if not cwd.is_absolute() or not cwd.is_dir() or str(cwd.resolve())!=str(cwd):raise ValueError('Canonical existing descendant history workspace required')
        checked={'sessionId':sid,'cwd':str(cwd),'parentSessionId':parent}
        if sid in known and known[sid]!=checked:raise ValueError('Conflicting or cyclic selected family identity')
        known[sid]=checked
        if len(known)-1>MAX_FAMILY:raise ValueError('Selected family exceeds the 100-session ownership bound')
    for hint in hints:add(hint)
    visited=set()
    with ExitStack() as leases:
        while len(visited)<len(known):
            ready=sorted(sid for sid,row in known.items() if sid not in visited and (sid==owner.identity or row['parentSessionId'] in visited))
            if not ready:raise ValueError('Selected family has a cycle or unavailable parent')
            for sid in ready:
                row=known[sid]
                if sid!=owner.identity:
                    try:handle=SharedSessionStore(row['cwd'],sid).acquire(app='amplifier-acp-lifecycle-family')
                    except SessionBusyError:raise OwnershipBusy(descendant=True) from None
                    leases.callback(handle.release)
                    metadata=record(owner.server.directory(row['cwd'],sid)/'metadata.json')
                    if metadata.get('session_id')!=sid or metadata.get('parent_id')!=row['parentSessionId']:raise ValueError('Native descendant relationship differs from indexed identity')
                    guard_copy_source(owner.server,sid)
                if check is not None:check(sid,row['cwd'])
                visited.add(sid)
                with Store(home(owner.server),sid).db() as db:
                    workers=db.execute("SELECT body FROM workers WHERE json_extract(body,'$.kind')='session' LIMIT ?",(MAX_FAMILY+1,)).fetchall() if db else []
                if len(workers)>MAX_FAMILY:raise ValueError('Native family index exceeds the ownership bound')
                for (raw,) in workers:
                    child=json.loads(raw);child_id=child.get('sessionId') or child.get('workerId')
                    if child_id==sid:raise ValueError('Native worker index contains a self cycle')
                    if child_id in known:continue
                    add({'sessionId':child_id,'cwd':row['cwd'],'parentSessionId':sid})
        yield {'count':len(known)-1,'coverage':'indexed-and-native-worker-families','externalWriterCoverage':False,'ownersHeld':True}
