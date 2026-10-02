"""Native-owned, immutable configuration references. Never clone execution state."""
import copy
import hashlib
import json
import os
from pathlib import Path
import uuid

MAX_BYTES=4*1024*1024

def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

def read(path):
    if path.is_symlink() or path.stat().st_size>MAX_BYTES:raise ValueError('Unsafe or oversized native configuration; preserved for inspection.')
    return json.loads(path.read_text())

def location(home,identity):
    from .native.session_files import validate_id
    root=Path(home)/'sessions'/validate_id(identity)
    for p in [Path(home),Path(home)/'sessions',root]:
        if p.is_symlink():raise ValueError('Native configuration directories cannot be symbolic links.')
    return root

def capture(home,identity,cwd,bundle):
    directory=location(home,identity)
    plan=read(directory/'effective-configuration.json')
    override=directory/'configuration.json'
    if override.exists():
        # Preserve disabled module entries omitted by the mounted graph.
        saved=read(override)
        for section in ('providers','tools','hooks'):
            ids={r.get('instance_id') or r.get('id') or r.get('module') for r in plan.get(section,[])}
            plan.setdefault(section,[]).extend(r for r in saved.get(section,[]) if (r.get('instance_id') or r.get('id') or r.get('module')) not in ids)
    state=read(directory/'control-state.json')
    controls={k:copy.deepcopy(state[k]) for k in ('selection','configurator','mode','budget') if k in state}
    if 'capacity' in state:controls['capacity']={**copy.deepcopy(state['capacity']),'revision':0}
    value={'workspace':str(Path(cwd).resolve()),'bundle':bundle,'plan':plan,'controls':controls}
    if not isinstance(bundle,str) or not bundle:raise ValueError('Source bundle identity is unavailable.')
    if len(json.dumps(value).encode())>MAX_BYTES:raise ValueError('Native configuration exceeds clone bound.')
    return value

def snapshot(home,identity,cwd,bundle):
    value=capture(home,identity,cwd,bundle);digest=fingerprint(value)
    from .native.host.config import write_private
    directory=location(home,identity)/'configuration-snapshots'
    write_private(directory/(digest+'.json'),json.dumps(value))
    return {'version':1,'sourceSessionId':identity,'sourceCwd':value['workspace'],'configurationHash':digest}

def prepare(server,params):
    identity=params.get('sessionId');command=params.get('commandId');source=params.get('source')
    if not isinstance(identity,str) or str(uuid.UUID(identity))!=identity:raise ValueError('A canonical reserved session UUID is required.')
    if not isinstance(command,str) or not command or len(command)>200:raise ValueError('A bounded creation commandId is required.')
    if not isinstance(source,dict) or source.get('version')!=1:raise ValueError('A native configuration reference is required.')
    cwd=Path(params.get('cwd','')).expanduser()
    if not cwd.is_absolute() or not cwd.is_dir():raise ValueError('cwd must be an existing absolute directory.')
    cwd=str(cwd.resolve())
    if source.get('sourceCwd')!=cwd:raise ValueError('Cloning across workspaces requires an explicit workspace owner.')
    digest=source.get('configurationHash','')
    if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise ValueError('Invalid configuration hash.')
    home=Path(server.config.get('appHome') or os.environ.get('AMPLIFIER_WEB_HOME') or Path.home()/'.amplifier-unified').expanduser().resolve()
    original=location(home,source.get('sourceSessionId'))
    saved=read(original/'configuration-snapshots'/(digest+'.json'))
    if fingerprint(saved)!=digest or fingerprint(capture(home,source['sourceSessionId'],cwd,saved['bundle']))!=digest:
        raise ValueError('Reviewed native configuration changed; preview again.')
    target=location(home,identity)
    native=server.directory(cwd,identity)
    if identity in server.sessions or target.exists() or native.exists():raise ValueError('Reserved identity already exists; inspect the original creation receipt. No work replayed.')
    if len(server.sessions)>=server.config.get('maxSessions',32):raise ValueError('Connection session capacity reached.')
    from .mcp import validate_servers
    validate_servers(params.get('mcpServers',[]))
    # Exclusive reservation comes before any runtime mount. Failure remains inspectable.
    target.mkdir(parents=True,exist_ok=False)
    from .native.host.config import write_private
    write_private(target/'creation-receipt.json',json.dumps({'commandId':command,'source':source,'state':'reserved'}))
    write_private(target/'configuration.json',json.dumps(saved['plan']))
    write_private(target/'control-state.json',json.dumps(saved['controls']))
    return identity,saved['bundle']

def confirmed(server,identity):
    home=Path(server.config.get('appHome') or os.environ.get('AMPLIFIER_WEB_HOME') or Path.home()/'.amplifier-unified').expanduser().resolve()
    path=location(home,identity)/'creation-receipt.json'
    value=read(path);value['state']='confirmed'
    from .native.host.config import write_private
    write_private(path,json.dumps(value))
