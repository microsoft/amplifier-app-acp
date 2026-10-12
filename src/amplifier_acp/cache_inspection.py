"""Cold source facts and exactly reconstructible bytecode. No resolver or model."""
from __future__ import annotations
import hashlib,importlib.util,json,marshal,os,re,stat,subprocess,sys,tempfile,time
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit
from .maintenance_gate import checked
from .maintenance import file_stamp

LIMIT=2*1024*1024

def read(path,limit=LIMIT):
    path=checked(path);before=file_stamp(path)
    if before[2]>limit:raise ValueError('Cache metadata exceeds its explicit read bound')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    try:
        raw=os.read(fd,limit+1)
        if len(raw)>limit or file_stamp(path)!=before:raise ValueError('Cache evidence changed while reading')
        return raw
    finally:os.close(fd)

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def public_source(value):
    if not isinstance(value,str) or len(value)>4096:return None
    try:
        parsed=urlsplit(value.removeprefix('git+'))
        if parsed.scheme not in {'https','http','ssh','git','file'}:return '[local or unrecognized source]'
        if parsed.scheme=='file':return '[local source]'
        return urlunsplit((parsed.scheme,parsed.hostname or '',parsed.path,'',''))
    except ValueError:return '[unrecognized source]'

def source_key(value):
    """Conservative repository match across credentials, refs and transports.

    This key may protect extra downloads, but never authorizes their removal.
    It is deliberately separate from the redacted display label.
    """
    if not isinstance(value,str) or len(value)>8192:return None
    value=value.removeprefix('git+')
    if '://' not in value:
        match=re.fullmatch(r'(?:[^/@:]+@)?([^/:]+):(.+)',value)
        if not match:return None
        value='ssh://'+match[1]+'/'+match[2]
    try:
        parsed=urlsplit(value)
        if parsed.scheme not in {'https','http','ssh','git'} or not parsed.hostname:return None
        path=parsed.path.rsplit('@',1)[0].rstrip('/').removesuffix('.git')
        return (parsed.hostname.lower(),path)
    except ValueError:return None

def git(root,*args):
    environment={k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
    environment.update(GIT_CONFIG_GLOBAL=os.devnull,GIT_CONFIG_NOSYSTEM='1',GIT_TERMINAL_PROMPT='0',GIT_OPTIONAL_LOCKS='0')
    command=['git','--no-optional-locks','-c','core.fsmonitor=false','-c','core.hooksPath='+os.devnull,*args]
    with tempfile.TemporaryFile() as output,tempfile.TemporaryFile() as errors:
        process=subprocess.run(command,cwd=root,env=environment,stdout=output,stderr=errors,timeout=15,check=False)
        if process.returncode or output.tell()>LIMIT:raise ValueError('Git evidence is unavailable or exceeds its bounded output')
        output.seek(0);return output.read()

def bytecode(path):
    """Compile source without executing it; no guessed __pycache__ deletion."""
    path=checked(path)
    match=re.fullmatch(r'(.+)\.'+re.escape(sys.implementation.cache_tag)+r'(?:\.opt-([12]))?\.pyc',path.name)
    if path.parent.name!='__pycache__' or not match:raise ValueError('Bytecode format is not owned by the current interpreter')
    source=checked(path.parent.parent/(match[1]+'.py'));raw=read(path);text=read(source)
    if len(raw)<16 or raw[:4]!=importlib.util.MAGIC_NUMBER:raise ValueError('Unknown bytecode version')
    flags=int.from_bytes(raw[4:8],'little')
    if flags not in {0,1,3}:raise ValueError('Unknown bytecode flags')
    if flags&1:
        if raw[8:16]!=importlib.util.source_hash(text):raise ValueError('Bytecode source hash changed')
    elif int.from_bytes(raw[8:12],'little')!=int(source.stat().st_mtime)&0xffffffff or int.from_bytes(raw[12:16],'little')!=len(text)&0xffffffff:raise ValueError('Bytecode source timestamp changed')
    code=compile(text,str(source),'exec',dont_inherit=True,optimize=int(match[2] or 0))
    if marshal.dumps(code)!=raw[16:]:raise ValueError('Bytecode is not exactly reconstructible from the retained source')
    return {'path':str(path),'stamp':file_stamp(path),'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'sourcePath':str(source),'sourceStamp':file_stamp(source),'sourceSha256':hashlib.sha256(text).hexdigest()}

def inspect_git(root,check=lambda:None):
    check()
    if not (root/'.git').is_dir() or (root/'.git').is_symlink():raise ValueError('Linked or unavailable Git authority')
    head=git(root,'rev-parse','HEAD').decode().strip()
    if not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}',head):raise ValueError('Invalid Git head')
    dirty=bool(git(root,'status','--porcelain','--untracked-files=no').strip())
    extras=git(root,'ls-files','--others','-z').split(b'\0')
    unknown=[];derived=[]
    for raw in extras:
        check()
        if not raw or raw==b'.amplifier_cache_meta.json':continue
        name=os.fsdecode(raw)
        if len(name)>8192 or '..' in Path(name).parts or Path(name).is_absolute():raise ValueError('Unrecognized Git extra')
        candidate=root/name
        try:
            proof=bytecode(candidate)
            derived.append(proof)
        except (ValueError,OSError,SyntaxError):unknown.append(name)
        if len(unknown)+len(derived)>10000:raise ValueError('Git extra inventory exceeds10000')
    # No network fetch: local remote refs are evidence, never a proof that every
    # commit is published. Detached/unconfigured upstream remains unknown.
    try:
        upstream=git(root,'rev-parse','--symbolic-full-name','@{upstream}').decode().strip()
        ahead=int(git(root,'rev-list','--count',upstream+'..HEAD'))
        unpushed='present' if ahead else 'none-local-proof'
    except (ValueError,subprocess.SubprocessError):unpushed='unknown'
    return {'head':head,'dirty':dirty,'untracked':bool(unknown),'unknownExtras':len(unknown),'unpushed':unpushed},derived

class Bounds:
    def __init__(self,check,seconds=300):self.check=check;self.deadline=time.monotonic()+seconds;self.entries=0
    def __call__(self):
        self.check()
        if time.monotonic()>self.deadline:raise ValueError('Cold cache inventory deadline exceeded')
    def children(self,path):
        self();path=checked(path)
        if not path.exists():return
        if not path.is_dir():raise ValueError('Cache directory is not a directory')
        with os.scandir(path) as entries:
            for entry in entries:
                self();self.entries+=1
                if self.entries>100000:raise ValueError('Cold cache inventory exceeds100000entries')
                yield Path(entry.path)

def tree_proof(root, bounds):
    """Bind a quarantine to exact app-owned bytes, including Git metadata."""
    root=checked(root); rows=[]; total=0
    def visit(path):
        nonlocal total
        bounds(); info=path.lstat()
        if stat.S_ISLNK(info.st_mode):raise ValueError('Linked cache content is preserved')
        relative=str(path.relative_to(root))
        if stat.S_ISDIR(info.st_mode):
            rows.append([relative,'directory',info.st_mode])
            for child in sorted(bounds.children(path)):visit(child)
        elif stat.S_ISREG(info.st_mode) and info.st_nlink==1:
            before=file_stamp(path); hashed=hashlib.sha256()
            with path.open('rb') as stream:
                while chunk:=stream.read(1024*1024):
                    bounds();total+=len(chunk)
                    if total>2*1024**3:raise ValueError('Cache review exceeds 2 GiB')
                    hashed.update(chunk)
            if file_stamp(path)!=before:raise ValueError('Cache changed during review')
            rows.append([relative,'file',before,hashed.hexdigest()])
        else:raise ValueError('Linked or special cache content is preserved')
    visit(root)
    return {'digest':digest(rows),'bytes':total,'entries':len(rows)}
