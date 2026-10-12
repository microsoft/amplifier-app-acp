"""Extracted native application runtime; provenance in extraction.json."""
from __future__ import annotations


import json
import os


from pathlib import Path


import re


def active_release(home):
    path = Path(home) / 'updates' / 'active.json'
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError:
        value = {}
    for field in ('current','previous'):
        identity = value.get(field)
        if identity is not None and (not isinstance(identity,str) or not re.fullmatch(r'[a-f0-9]{32}', identity)):
            raise ValueError('Invalid ecosystem release identity')
    return value


def selected_release(home):
    # Workers capture their source generation at spawn; the serving host follows
    # the current pointer. Never change a running worker's resolver underneath it.
    selected = os.environ.get('AMPLIFIER_UNIFIED_RELEASE')
    identity = (selected or None) if selected is not None else active_release(home).get('current')
    if identity is not None and not re.fullmatch(r'[a-f0-9]{32}', identity):
        raise ValueError('Invalid worker source generation')
    return identity

def foundation_home(home):
    home = Path(home)
    identity = selected_release(home)
    return home / 'updates' / 'releases' / identity / 'foundation' if identity else home / 'foundation'

import asyncio, hashlib, stat, time, tomllib
from urllib.parse import urlsplit
def safe_label(url):
    parsed = urlsplit(url)
    return (parsed.hostname or 'Git source') + '/' + parsed.path.strip('/').removesuffix('.git')

def group_sources(items):
    """One repository/ref/revision/status per display row, with every role kept.

    Installation consumes the private inventory, never these presentation rows.
    Distinct refs, revisions, tiers and protected states must remain distinct.
    """
    groups = {}
    for item in items:
        row = {k: v for k, v in item.items() if k not in {'path', 'url', 'eligible'}}
        if row.get('kind') not in {'bundle / module', 'runtime dependency', 'source group'} or not row.get('ref'):
            groups[('id', row['id'])] = row
            continue
        key = tuple(row.get(k) for k in ('label', 'ref', 'current', 'latest', 'status', 'updateTier'))
        members = row.get('members') or [row]
        if key not in groups:
            groups[key] = {**row, 'members': []}
        target = groups[key]
        known = {member['id'] for member in target['members']}
        target['members'].extend(member for member in members if member['id'] not in known)
    result = []
    for key, row in groups.items():
        members = row.get('members', [])
        if len(members) > 1:
            row['id'] = 'source:' + hashlib.sha256(json.dumps(key).encode()).hexdigest()[:20]
            row['kind'] = 'source group'
            row['cacheCopies'] = sum(member.get('cacheCopies', 1) for member in members if member.get('kind') == 'bundle / module')
            row['packageCount'] = len({member['package'] for member in members if member.get('package')})
            row['usage'] = 'configured' if any(member.get('usage') == 'configured' for member in members) else 'unknown'
            row['usageEvidence'] = sorted({evidence for member in members for evidence in member.get('usageEvidence', [])})
            row['members'] = sorted(members, key=lambda member: (member.get('package', ''), member['id']))
        elif members:
            row = {**members[0]}
        result.append(row)
    return result

def pinned(ref):
    return bool(re.fullmatch(r'[0-9a-fA-F]{7,40}', ref) or re.match(r'^(refs/tags/|v?\d+\.)', ref))

def source_key(url, ref):
    """Compare repository/ref identity, never infer that main and master agree."""
    parsed = urlsplit(url)
    if parsed.scheme not in {'https', 'http', 'ssh'} or not parsed.hostname or not isinstance(ref, str) or not ref:
        raise ValueError('Unsupported Git source')
    return (parsed._replace(path=parsed.path.rstrip('/').removesuffix('.git'), fragment='').geturl(),
            ref.removeprefix('refs/heads/'))

async def process(*args, cwd=None, env=None, timeout=90, raw=False):
    from ..repository_access import repository_environment
    env=repository_environment(env);env.pop('VIRTUAL_ENV',None)
    started=time.monotonic()
    proc = await asyncio.create_subprocess_exec(*map(str,args), cwd=cwd, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=os.name != 'nt')
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except BaseException as error:
        if proc.returncode is None:
            try:
                if os.name != 'nt':
                    import signal
                    os.killpg(proc.pid, signal.SIGTERM)
                else: proc.terminate()
            except ProcessLookupError:
                pass
            try: await asyncio.wait_for(proc.wait(), 5)
            except TimeoutError:
                try:
                    if os.name != 'nt': os.killpg(proc.pid, signal.SIGKILL)
                    else: proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
        if isinstance(error,TimeoutError):
            from .update_diagnostics import CommandTimeout
            raise CommandTimeout(time.monotonic()-started) from None
        raise
    if proc.returncode:
        from .update_diagnostics import CommandFailure,command_facts
        raise CommandFailure(command_facts(stdout,stderr,proc.returncode,time.monotonic()-started))
    if raw:return stdout
    from .update_diagnostics import CommandOutput,command_facts
    return CommandOutput(stdout.decode(errors='replace').strip(),command_facts(stdout,stderr,proc.returncode,time.monotonic()-started))

_WIKI_VERSION_HOOK_SHA256 = 'd0733aa7fecaadf307fdeb42dd202c4fec1a8255e56b24bb23777cbd003620f3'

_WIKI_VERSION_HEADER = b'''"""Single source of truth for the wiki-weaver package version.

Kept as a leaf module with no imports so that any submodule can safely
import __version__ without risk of triggering a circular import through
the wiki_weaver package __init__.

This value is baked in at wheel-build time by the git-version hatchling
build hook (see hatch_build.py at the repo root) -- it is the commit date
+ short SHA of the exact commit this wheel was built from, not the build
date. Do not hand-edit; it is overwritten on the next build.
"""

'''

async def cache_changes(root):
    """Return protected changes and proven cache artifacts, without editing files.

    Imported caches used to flatten symlinks, and some upstreams track generated
    bytecode. Wiki-weaver's reviewed build hook also bakes a version into source.
    Only unstaged, exactly verified generated forms may be restored later,
    inside an isolated staging copy. Untracked files are never removed.
    """
    root = Path(root).resolve()
    records = (await process('git','--no-optional-locks','status','--porcelain=v1','-z','--untracked-files=no',cwd=root,timeout=10,raw=True)).split(b'\0')
    protected, artifacts = [], []

    async def head_entry(path, revision='HEAD'):
        value = await process('git','--literal-pathspecs','ls-tree','-z',revision,'--',path,cwd=root,timeout=10,raw=True)
        entries = value.split(b'\0')
        if len(entries)!=2 or not entries[0]:return None
        metadata, name = entries[0].split(b'\t',1)
        mode, kind, identity = metadata.decode('ascii').split()
        return (mode,identity) if kind=='blob' and os.fsdecode(name)==path else None

    def regular(path, mode=None):
        candidate = root/path
        try:
            if not candidate.resolve(strict=True).is_relative_to(root):return False
            if any(parent.is_symlink() for parent in [candidate,*candidate.parents] if parent!=root and parent.is_relative_to(root)):return False
            current = candidate.stat().st_mode
            return stat.S_ISREG(current) and (mode is None or bool(current&stat.S_IXUSR)==(mode=='100755'))
        except (OSError,ValueError):return False

    def bytecode_header(value):
        return len(value)>=16 and value[2:4]==b'\r\n' and int.from_bytes(value[4:8],'little')&~3==0

    async def wiki_generated_version():
        # Never execute/import a cached hook or evaluate Python source. Verify its
        # exact reviewed bytes and reproduce only that implementation's output.
        version_path = 'wiki_weaver/_version.py'
        originals = {}
        try:
            captured = await process('git','rev-parse','--verify','HEAD^{commit}',cwd=root,timeout=10,raw=True)
            if not re.fullmatch(rb'(?:[0-9a-f]{40}|[0-9a-f]{64})\n',captured):return False
            revision = captured[:-1].decode('ascii')
            for name in (version_path, 'hatch_build.py', 'pyproject.toml'):
                entry = await head_entry(name, revision)
                if not entry or entry[0] not in {'100644','100755'} or not regular(name,entry[0]):return False
                mode, identity = entry
                indexed = await process('git','--literal-pathspecs','ls-files','--stage','-z','--',name,cwd=root,timeout=10,raw=True)
                if indexed != f'{mode} {identity} 0\t{name}\0'.encode():return False
                original = await process('git','cat-file','blob',identity,cwd=root,timeout=10,raw=True)
                if name != version_path and (root/name).read_bytes() != original:return False
                originals[name] = original
            if hashlib.sha256(originals['hatch_build.py']).hexdigest() != _WIKI_VERSION_HOOK_SHA256:return False
            project = tomllib.loads(originals['pyproject.toml'].decode('utf-8'))
            if project['project']['name'] != 'wiki-weaver':return False
            if project['build-system'] != {'requires':['hatchling'], 'build-backend':'hatchling.build'}:return False
            if project['tool']['hatch']['build']['hooks']['custom'] != {'path':'hatch_build.py'}:return False
            original = originals[version_path]
            if not original.startswith(_WIKI_VERSION_HEADER):return False
            if not re.fullmatch(rb'__version__ = "[0-9]{4}\.[0-9]{2}\.[0-9]{2}-[0-9a-f]{4,64}"\n',original[len(_WIKI_VERSION_HEADER):]):return False
            # Match the hook's actual Git semantics (including core.abbrev), not
            # an assumed seven-character prefix or a wall-clock/author date.
            # Bind all awaited reads to one commit; a moving HEAD cannot mix
            # one commit's tree/date with another commit's abbreviation.
            date = await process('git','log','-1','--format=%cd','--date=format:%Y.%m.%d',revision,cwd=root,timeout=10,raw=True)
            sha = await process('git','rev-parse','--short',revision,cwd=root,timeout=10,raw=True)
            if not re.fullmatch(rb'[0-9]{4}\.[0-9]{2}\.[0-9]{2}\n',date):return False
            if not re.fullmatch(rb'[0-9a-f]{4,64}\n',sha) or not captured[:-1].startswith(sha[:-1]):return False
            expected = _WIKI_VERSION_HEADER + b'__version__ = "' + date[:-1] + b'-' + sha[:-1] + b'"\n'
            if (root/version_path).read_bytes() != expected:return False
            return await process('git','rev-parse','--verify','HEAD^{commit}',cwd=root,timeout=10,raw=True) == captured
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, TimeoutError):
            return False

    index = 0
    while index<len(records):
        record=records[index];index+=1
        if not record:continue
        change=record[:2].decode('ascii');path=os.fsdecode(record[3:])
        # Renames/copies have a second NUL-delimited name, even with whitespace.
        if 'R' in change or 'C' in change:index+=1
        if change not in {' M',' T'}:
            protected.append(path);continue
        if path=='wiki_weaver/_version.py':
            generated=change==' M' and await wiki_generated_version()
            (artifacts if generated else protected).append(path)
            continue
        entry=await head_entry(path)
        if not entry or not regular(path):
            protected.append(path);continue
        mode,identity=entry
        generated=False
        if change==' M' and mode in {'100644','100755'} and regular(path,mode) and '__pycache__' in Path(path).parts and re.fullmatch(r'.+\.cpython-\d+(?:\.opt-\d+)?\.pyc',Path(path).name):
            original=await process('git','cat-file','blob',identity,cwd=root,timeout=10,raw=True)
            with (root/path).open('rb') as file:header=file.read(16)
            generated=bytecode_header(original) and bytecode_header(header)
        elif change==' T' and mode=='120000':
            link=os.fsdecode(await process('git','cat-file','blob',identity,cwd=root,timeout=10,raw=True))
            # A direct, tracked regular target only: no outside paths or chains.
            target=Path(os.path.normpath(str(Path(path).parent/link)))
            if not Path(link).is_absolute() and '..' not in target.parts and regular(target):
                target_entry=await head_entry(target.as_posix())
                if target_entry and target_entry[0] in {'100644','100755'} and regular(target,target_entry[0]) and regular(path,target_entry[0]):
                    hashes=(await process('git','hash-object','--no-filters','--',path,str(target),cwd=root,timeout=10)).splitlines()
                    generated=hashes==[target_entry[1],target_entry[1]]
        (artifacts if generated else protected).append(path)
    return protected,artifacts
