"""Bounded persisted task authority. A read never mounts or starts a runtime."""
import hashlib,json,os,stat
from pathlib import Path

def inspect(server,params):
    cwd=Path(params.get('cwd','')).expanduser()
    if not cwd.is_absolute() or not cwd.is_dir():raise ValueError('Task cwd must be an existing absolute directory')
    native=server.directory(str(cwd.resolve()),params.get('sessionId'))
    if not native.is_dir() or not (native/'metadata.json').is_file():raise ValueError('Native session does not exist in the authorized history workspace')
    from .relocation import home
    path=home(server)/'sessions'/params['sessionId']/'control-state.json'
    for parent in (path,*path.parents):
        if parent==home(server):break
        if parent.is_symlink():raise ValueError('Native task state cannot traverse symbolic links')
    try:fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    except FileNotFoundError:return {'available':False,'revision':None,'task':None,'source':'persisted-native-controls'}
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size>512*1024:raise ValueError('Native task state is not a bounded regular file')
        data=os.read(fd,512*1024+1)
        if len(data)>512*1024 or os.fstat(fd).st_mtime_ns!=info.st_mtime_ns:raise ValueError('Native task state changed while reading')
    finally:os.close(fd)
    saved=json.loads(data);task=saved.get('task')
    if task is not None and (not isinstance(task,dict) or not isinstance(task.get('id'),str) or type(task.get('revision'))is not int or task.get('status') not in {'active','paused','blocked','completed'} or len(json.dumps(task).encode())>32768):raise ValueError('Native task record is unsupported or exceeds its bound')
    revision=hashlib.sha256(data).hexdigest()
    if params.get('expectedRevision') is not None and params['expectedRevision']!=revision:raise ValueError('Native task state changed')
    return {'available':True,'revision':revision,'task':task,'source':'persisted-native-controls'}
