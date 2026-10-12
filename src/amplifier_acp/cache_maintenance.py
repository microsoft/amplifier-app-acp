"""Disk-indexed cache review; unknown usage never grants source retirement."""
from __future__ import annotations
import hashlib,json,os,sqlite3,time,uuid
from pathlib import Path
import yaml
from .cache_inspection import Bounds,bytecode,digest,inspect_git,public_source,read,source_key,tree_proof
from .maintenance_gate import Gate,checked
from .private_store_snapshot import require_admin

OPERATIONS={'maintenance.cache.scan','maintenance.cache.page','maintenance.cache.preview','maintenance.cache.clear'}
FIELDS={'maintenance.cache.scan':{'commandId'},'maintenance.cache.page':{'scanId','revision','cursor','limit'},'maintenance.cache.preview':{'scanId','revision','ids','reviewed'},'maintenance.cache.clear':{'commandId','previewHash'}}

class CacheRefused(ValueError):
    def __init__(self,message):super().__init__(message);self.data={'executed':False,'replayed':False,'reason':'native-cache-review-refused'}

def safeid(value,size=32):
    if not isinstance(value,str) or len(value)!=size or any(c not in '0123456789abcdef' for c in value):raise CacheRefused('Exact native cache identity required')
    return value

class Caches:
    def __init__(self,owner):
        self.owner=owner
        with owner.db(namespace='cache'):pass

    def references(self,workspace,bounds):
        owner=self.owner;refs=[];configured=[];stamps=[];issues=[];generation_rows=[]
        def document(path,kind):
            bounds()
            if not path.exists():return
            raw=read(path);stamps.append([str(path),hashlib.sha256(raw).hexdigest()])
            try:value=json.loads(raw) if path.suffix=='.json' else yaml.safe_load(raw)
            except (ValueError,yaml.YAMLError):issues.append('unreadable-'+kind);return
            def strings(v,depth=0):
                if depth>40:raise ValueError('Source reference nesting exceeds40')
                if isinstance(v,str):yield v
                elif isinstance(v,dict):
                    for item in v.values():yield from strings(item,depth+1)
                elif isinstance(v,list):
                    for item in v:yield from strings(item,depth+1)
            values=list(strings(value))
            if len(values)>20000:raise ValueError('Source reference record exceeds20000strings')
            refs.append((kind,values))
            if kind.startswith('configured'):
                for v in values:
                    if source_key(v) is not None or v.startswith(('file://','/')):
                        configured.append((kind,v))
        for path,kind in [(owner.home/'settings.yaml','configured-shared'),(owner.app/'admin-settings.yaml','configured-native'),(Path(workspace)/'.amplifier/settings.yaml','configured-project'),(Path(workspace)/'.amplifier/settings.local.yaml','configured-local'),(owner.app/'foundation/registry.json','retained-registry'),(owner.app/'updates/active.json','generation-pointers')]:document(path,kind)
        if owner.config.get('runtimeManifest'):document(Path(owner.config['runtimeManifest']),'configured-runtime-manifest')
        if owner.config.get('bundle'):configured.append(('configured-launcher',owner.config['bundle']))
        config=owner.app/'config'
        for path in bounds.children(config):
            if path.suffix in {'.yaml','.yml','.json'}:document(path,'configured-native')
            else:issues.append('unrecognized-configuration-entry')
        for lease in bounds.children(owner.app/'updates/worker-leases'):
            if lease.suffix=='.json':document(lease,'worker-generation-lease')
            else:issues.append('unrecognized-worker-lease')
        baseline=owner.app/'updates/baseline-runtime'
        if baseline.exists():generation_rows.append(('baseline',baseline))
        for path in bounds.children(owner.app/'updates/releases'):
            if not path.is_dir() or path.is_symlink():issues.append('unrecognized-generation');continue
            generation_rows.append((path.name,path))
        if len(generation_rows)>5000:raise ValueError('Retained generation inventory exceeds5000')
        for gen,path in generation_rows:
            for name in ('runtime-installed.json','runtime-sources.json','runtime-project.json','validated.json','runtime-repaired.json'):
                document(path/name,'retained-runtime:'+gen)
            for child in bounds.children(path/'foundation/cache/.source-bindings'):
                if child.suffix=='.json':document(child,'retained-binding:'+gen)
        basis={'stamps':sorted(stamps),'configured':configured,'issues':sorted(set(issues)),'generations':[(name,str(path)) for name,path in generation_rows]}
        return {'facts':refs,'configured':configured,'stamps':sorted(stamps),'issues':sorted(set(issues)),'generations':generation_rows,'revision':digest(basis)}

    def roots(self,refs):
        owner=self.owner;values=[('native-app',owner.app/'foundation/cache','legacy',True),('native-shared',owner.home/'cache','shared',False)]
        registry=Path(owner.config.get('registryHome') or owner.app/'foundation')/'cache'
        if registry!=owner.app/'foundation/cache':values.append(('configured-registry',registry,'external',False))
        store=Path(os.environ.get('AMPLIFIER_SOURCE_STORE') or owner.app/'source-store').expanduser().absolute()
        values.extend([('shared-objects',store/'objects','objects',False),('shared-builds',store/'builds','builds',False)])
        for gen,path in refs['generations']:
            values.append(('generation:'+gen,path/'foundation/cache','retained',False))
            values.append(('generation-shared:'+gen,path/'shared-config/cache','retained',False))
        seen=set()
        for label,path,kind,owned in values:
            if str(path) in seen:continue
            seen.add(str(path));yield label,path,kind,owned

    def candidates(self,path,bounds,depth=0):
        if depth>8:yield path,'unknown';return
        for child in bounds.children(path):
            bounds()
            if child.is_symlink():yield child,'unknown';continue
            if child.is_file():
                if child.name.endswith('.lock'):continue
                yield child,'binding' if child.parent.name=='.source-bindings' and child.suffix=='.json' else 'unknown';continue
            if not child.is_dir():yield child,'unknown';continue
            if (child/'.amplifier_cache_meta.json').exists() or (child/'.git').exists():yield child,'git';continue
            if child.name in {'bundles','modules','skills','.source-bindings'} or len(child.name)==64 and all(c in '0123456789abcdef' for c in child.name):yield from self.candidates(child,bounds,depth+1)
            else:yield child,'unknown'

    def inspect(self,path,label,kind,owned,refs,bounds):
        identity=digest(str(path))[:32];base={'id':identity,'kind':'cached-source','label':path.name,'location':label,'usage':'unknown','references':[],'clearable':False,'reason':'Usage is unknown; source retirement is not authorized'}
        if kind!='git':return [{**base,'kind':'source-binding' if kind=='binding' else 'unrecognized-cache','reason':'Retained binding is source authority' if kind=='binding' else 'Unrecognized cache format or linked content; preserved'}]
        try:
            metadata=json.loads(read(path/'.amplifier_cache_meta.json'));url=metadata.get('git_url');revision=metadata.get('commit');ref=metadata.get('ref');public=public_source(url);repository=source_key(url)
            if not isinstance(url,str) or not isinstance(revision,str):raise ValueError('Missing source identity')
            facts,derived=inspect_git(path,bounds)
            if facts['head']!=revision:raise ValueError('Cache metadata disagrees with Git identity')
            base.update(source={'url':public,'ref':ref if isinstance(ref,str) and len(ref)<=200 else None,'revision':revision},git=facts)
            evidence=[]
            for origin,values in refs['facts']:
                if any(v==str(path) or v.startswith(str(path)+'/') or v==url or v.startswith('git+'+url+'@') or repository is not None and source_key(v)==repository or v==revision for v in values):evidence.append(origin)
            if label.startswith('generation'):evidence.append('retained-generation')
            if metadata.get('immutable'):evidence.append('shared-immutable-source')
            if metadata.get('buildInput'):evidence.append('shared-build-input')
            if any(v==str(path) or v==url or v.startswith('git+'+url+'@') or repository is not None and source_key(v)==repository for _,v in refs['configured']):evidence.append('configured-source')
            base.update(references=sorted(set(evidence))[:20],referencesTruncated=len(set(evidence))>20,usage='configured' if 'configured-source' in evidence else 'referenced' if evidence else 'unknown')
            base['reason']='Source roots remain retained; global consumer retirement is unproven'
            if facts['dirty'] or facts['untracked']:base['reason']='Local modifications or untracked/ignored content are preserved'
            if facts['unpushed']=='present':base['reason']='Local commits not reachable from the local upstream are preserved'
            rows=[base]
            frozen=any(e.startswith('retained-runtime') or e in {'retained-generation','shared-immutable-source','shared-build-input'} for e in evidence)
            # Quarantine only unreferenced, clean app-owned downloads. This is
            # reversible cache eviction, never shared source-store retirement.
            if owned and repository is not None and not evidence and not refs['issues'] and not facts['dirty'] and not facts['untracked'] and facts['unpushed']=='none-local-proof':
                proof=tree_proof(path,bounds)
                rows[0]={**base,'kind':'app-source-cache','clearable':True,'bytes':proof['bytes'],'reason':'App-owned downloaded copy; clearing moves it intact to a private recovery folder','_private':{'sourceRoot':str(path),'treeProof':proof,'git':facts}}
            for proof in derived:
                bounds();relative=str(Path(proof['path']).relative_to(path));clearable=owned and not frozen and not refs['issues'] and not facts['dirty'] and not facts['untracked'] and facts['unpushed']=='none-local-proof'
                rows.append({'id':digest(proof['path'])[:32],'kind':'python-bytecode','label':relative,'location':label+'/'+path.name+'/'+relative,'usage':'derived','references':base['references'],'clearable':clearable,'reason':'Exactly reconstructible from unchanged retained Python source' if clearable else 'Referenced, modified, externally owned, or unverified source; derived bytes retained','bytes':proof['bytes'],'_private':{'proof':proof,'sourceRoot':str(path),'rootLabel':label,'sourceIdentity':base.get('source'),'git':facts}})
            return rows
        except Exception as error:
            # Do not forward Git diagnostics, metadata values or source URLs.
            return [{**base,'reason':'Source identity, working tree, or bounded Git evidence could not be verified','diagnostic':type(error).__name__}]

    def scan(self,workspace,args):
        owner=self.owner;scan=uuid.uuid4().hex;bounds=Bounds(owner.ensure_open);created=time.time();count=0;counts={};hasher=hashlib.sha256()
        with Gate(owner.config):
            refs=self.references(workspace,bounds)
            with owner.db() as db:
                for label,root,category,owned in self.roots(refs):
                    if root.is_symlink():
                        entries=[(root,'unknown')]
                    else:entries=self.candidates(root,bounds)
                    for path,kind in entries:
                        for row in self.inspect(path,label,kind,owned,refs,bounds):
                            row['_private']={**row.get('_private',{}),'path':str(path)}
                            raw=json.dumps(row,sort_keys=True,separators=(',',':'));count+=1
                            if count>100000:raise ValueError('Cold cache index exceeds100000rows')
                            db.execute('INSERT INTO cache_entries VALUES(?,?,?)',(scan,row['id'],raw));hasher.update(raw.encode()+b'\n');counts[row['kind']]=counts.get(row['kind'],0)+1
                # Configured sources without an inspected clone still belong in
                # the cold inventory; they are not an assertion of cache coverage.
                for origin,value in refs['configured']:
                    identity=digest([origin,value])[:32];row={'id':identity,'kind':'configured-source','label':public_source(value),'location':origin,'usage':'configured','references':[origin],'clearable':False,'reason':'Configuration is source authority; no cached-copy ownership is inferred'}
                    raw=json.dumps(row,sort_keys=True,separators=(',',':'));db.execute('INSERT OR IGNORE INTO cache_entries VALUES(?,?,?)',(scan,identity,raw));hasher.update(raw.encode()+b'\n')
                after=self.references(workspace,bounds)
                if after['revision']!=refs['revision']:raise ValueError('Source reference authority changed during the cold scan')
                bounds()
                body={'scanId':scan,'revision':hasher.hexdigest(),'createdAt':created,'expiresAt':created+900,'counts':{k:n for k,n in db.execute("SELECT json_extract(body,'$.kind'),count(*) FROM cache_entries WHERE scan=? GROUP BY json_extract(body,'$.kind')",(scan,))},'coverage':{'sourceRetirement':False,'canonicalHistoryScanned':False,'externalUsage':'unknown','referenceIssues':refs['issues'][:50],'referenceIssuesTruncated':len(refs['issues'])>50,'clearKinds':['python-bytecode','app-source-cache'],'retainedGenerations':len(refs['generations'])},'referenceRevision':refs['revision']}
                db.execute('INSERT INTO cache_scans VALUES(?,?,?)',(scan,workspace,json.dumps(body)))
        return body

    def indexed(self,workspace,args):
        scan=safeid(args.get('scanId'));revision=safeid(args.get('revision'),64)
        with self.owner.db() as db:row=db.execute('SELECT workspace,body FROM cache_scans WHERE id=?',(scan,)).fetchone()
        if not row or row[0]!=workspace:raise CacheRefused('Completed cache inventory belongs to another workspace or is unavailable')
        body=json.loads(row[1])
        if body['revision']!=revision:raise CacheRefused('Cache inventory revision changed')
        return body

    def page(self,workspace,args):
        body=self.indexed(workspace,args);limit=args.get('limit',50);cursor=args.get('cursor','')
        if type(limit)is not int or not 1<=limit<=50 or not isinstance(cursor,str) or cursor and safeid(cursor)!=cursor:raise CacheRefused('Bounded cache page required')
        with self.owner.db() as db:rows=db.execute('SELECT id,body FROM cache_entries WHERE scan=? AND id>? ORDER BY id LIMIT ?',(body['scanId'],cursor,limit+1)).fetchall()
        items=[{k:v for k,v in json.loads(row[1]).items() if k!='_private'} for row in rows[:limit]]
        return {**body,'items':items,'nextCursor':rows[limit-1][0] if len(rows)>limit else None}

    def preview(self,workspace,args):
        body=self.indexed(workspace,args);ids=args.get('ids')
        if args.get('reviewed') is not True or not isinstance(ids,list) or not 1<=len(ids)<=50 or len(set(ids))!=len(ids):raise CacheRefused('Review1–50exact cache entries')
        if time.time()>=body['expiresAt']:raise CacheRefused('Cache inventory expired; scan and review again')
        selected=[]
        with self.owner.db() as db:
            for identity in sorted(ids):
                row=db.execute('SELECT body FROM cache_entries WHERE scan=? AND id=?',(body['scanId'],safeid(identity))).fetchone()
                if not row:raise CacheRefused('Selected cache entry is unavailable')
                value=json.loads(row[0])
                if not value['clearable']:raise CacheRefused('Selected cache entry is not proven disposable; original retained')
                selected.append(value)
            quarantines=[r for r in selected if r['kind']=='app-source-cache']
            for cached in quarantines:
                if any(r['kind']=='python-bytecode' and r['_private']['sourceRoot']==cached['_private']['sourceRoot'] for r in selected):raise CacheRefused('Select a cache folder or its generated files, not both')
            preview={'scanId':body['scanId'],'revision':body['revision'],'referenceRevision':body['referenceRevision'],'expiresAt':min(body['expiresAt'],time.time()+300),'items':selected,'bytes':sum(r['bytes'] for r in selected),'coverage':'verified-native-owned-cache-quarantine' if quarantines else 'verified-native-owned-bytecode-only','sourceDirectoriesRemoved':0,'canonicalFilesChanged':0}
            hashed=digest(preview);db.execute('INSERT OR IGNORE INTO cache_previews VALUES(?,?,?)',(hashed,workspace,json.dumps(preview)))
        return {'previewHash':hashed,**{k:v for k,v in preview.items() if k!='items'},'items':[{k:v for k,v in row.items() if k!='_private'} for row in selected]}

    def clear(self,workspace,args):
        owner=self.owner
        try:authority=require_admin(owner)
        except ValueError:raise CacheRefused('Cache clearing requires the exact held recovery administration lease') from None
        if owner.config.get('maintenanceExternalWriters')!='stopped':raise CacheRefused('Cache clearing requires launcher-reviewed stopped external writers')
        with owner.db() as db:row=db.execute('SELECT workspace,body FROM cache_previews WHERE hash=?',(safeid(args.get('previewHash'),64),)).fetchone()
        if not row or row[0]!=workspace:raise CacheRefused('Exact reviewed cache preview is unavailable')
        body=json.loads(row[1])
        if digest(body)!=args['previewHash'] or time.time()>=body['expiresAt']:raise CacheRefused('Cache review expired or changed')
        removed=[];effect_attempted=False;bounds=Bounds(owner.ensure_open,120)
        with Gate(owner.config,exclusive=True):
            try:
                if require_admin(owner)!=authority:raise CacheRefused('Native administration lease changed')
                refs=self.references(workspace,bounds)
                if refs['revision']!=body['referenceRevision']:raise CacheRefused('Reference authority changed after review')
                for value in body['items']:
                    bounds();private=value['_private'];root=Path(private['sourceRoot'])
                    if value['kind']=='app-source-cache':
                        if not root.is_relative_to(checked(owner.app/'foundation/cache')) or root==owner.app/'foundation/cache':raise CacheRefused('Cache is outside the app-owned download directory')
                        if tree_proof(root,bounds)!=private['treeProof']:raise CacheRefused('Downloaded cache changed after review')
                        continue
                    path=Path(private['proof']['path'])
                    if bytecode(path)!=private['proof']:raise CacheRefused('Selected derived file or source changed after review')
                    facts,derived=inspect_git(root,bounds)
                    if facts!=private['git']:raise CacheRefused('Source Git identity changed after review')
                    if not any(proof==private['proof'] for proof in derived):raise CacheRefused('Selected derived bytes are no longer untracked')
                # Only after every file and reference preflights successfully do
                # any bytes disappear. Parent receipt was persisted beforehand.
                for value in body['items']:
                    bounds();private=value['_private']
                    if value['kind']=='app-source-cache':
                        root=checked(Path(private['sourceRoot']))
                        if tree_proof(root,bounds)!=private['treeProof']:raise ValueError('Downloaded cache changed during clearing')
                        recovery=checked(owner.app/'maintenance/cache-recovery'/digest(args['commandId']))
                        recovery.mkdir(parents=True,mode=0o700,exist_ok=True)
                        target=checked(recovery/value['id'])
                        if target.exists():raise ValueError('Original cache recovery entry already exists; inspect without replay')
                        # Same-filesystem rename preserves all bytes and permissions.
                        # Its destination is derivable even after a lost reply.
                        effect_attempted=True
                        root.rename(target);removed.append(value['id'])
                        for directory in (root.parent,recovery):
                            fd=os.open(directory,os.O_RDONLY)
                            try:os.fsync(fd)
                            finally:os.close(fd)
                        continue
                    path=Path(private['proof']['path'])
                    if bytecode(path)!=private['proof']:raise ValueError('Derived bytes changed during clearing')
                    effect_attempted=True
                    path.unlink();removed.append(value['id'])
                    fd=os.open(path.parent,os.O_RDONLY)
                    try:os.fsync(fd)
                    finally:os.close(fd)
            except Exception as error:
                if not effect_attempted:
                    if isinstance(error,CacheRefused):raise
                    raise CacheRefused('Cache preflight refused; original files retained') from None
                raise
        result={'removedIds':removed,'removedBytes':body['bytes'],'sourceDirectoriesRemoved':0,'canonicalFilesChanged':0,'coverage':body['coverage'],'replayed':False}
        if body['coverage']=='verified-native-owned-cache-quarantine':
            result['recoveryFolder']=str(owner.app/'maintenance/cache-recovery'/digest(args['commandId']))
            result['quarantined']=[{'id':r['id'],'originalPath':r['_private']['sourceRoot'],'savedName':r['id']} for r in body['items'] if r['kind']=='app-source-cache']
        return result
