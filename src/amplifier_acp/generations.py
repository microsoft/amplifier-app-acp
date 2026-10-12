"""Native-owned generation qualification and receipts, independent of any UI host."""
from __future__ import annotations
import asyncio,hashlib,json,os,re,shutil,sqlite3,time,uuid
from pathlib import Path
from contextlib import contextmanager
from filelock import AsyncFileLock
from .native.host.config import write_private
from .native.updates import active_release,foundation_home,process,pinned,safe_label,source_key,cache_changes
from .native import runtime_environment as environments

OPERATIONS={'generations.current','generations.inspect','generations.check','generations.prepare','generations.promote','generations.rollback','generations.receipt','generations.launcher','generations.ensure','generations.repair.preview','generations.repair'}

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def identity(value):
 if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{32}',value):raise ValueError('A native generation identity is required')
 return value

class Diagnostics:
 def __init__(self,owner):self.owner=owner;self.state={}
 def record(self,phase,status,**facts):
  from .native.update_diagnostics import exception_type
  safe={k:v for k,v in facts.items() if k in {'errorType','exitCode','durationMs','stdoutBytes','stderrBytes','timedOut','probe','anonymousRetry','commandId'}}
  with self.owner.db() as db:db.execute('INSERT INTO diagnostics(body) VALUES (?)',(json.dumps({'id':uuid.uuid4().hex,'attemptId':self.state.get('attemptId'),'phase':phase,'status':status,'at':time.time(),**safe}),))
  self.owner.changed()
 async def run(self,phase,function,*args,**kwargs):
  phase_id=uuid.uuid4().hex;started=time.monotonic()
  self.record(phase,'started',commandId=phase_id)
  try:
   value=await function(*args,**kwargs);self.record(phase,'succeeded',commandId=phase_id,**{**getattr(value,'diagnostic_facts',{}),'durationMs':round((time.monotonic()-started)*1000)});return value
  except BaseException as error:
   from .native.update_diagnostics import exception_type
   self.record(phase,'interrupted' if isinstance(error,asyncio.CancelledError) else 'failed',commandId=phase_id,**{'errorType':exception_type(error),**getattr(error,'diagnostic_facts',{}),'durationMs':round((time.monotonic()-started)*1000)});raise

class Generations:
 def __init__(self,home,config,on_change=None):
  self.on_change=on_change;self.notification_pending=False
  self.home=Path(home).resolve();self.config=config;self.directory=self.home/'updates';self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
  self.path=self.directory/'generation-owner.sqlite3';self.diagnostics=Diagnostics(self);self.inventory=[]
  if self.path.is_symlink():raise ValueError('Generation receipts cannot be symlinks')
  with self.db(write=False):pass
 def changed(self):
  if self.on_change is None or self.notification_pending:return
  self.notification_pending=True
  def notify():
   self.notification_pending=False
   try:self.on_change()
   except Exception:pass  # Advisory metadata never changes a saved result.
  asyncio.get_running_loop().call_soon(notify)
 @contextmanager
 def db(self,write=True):
  from .journal_schema import connect
  db=connect(self.path,'generations',write=write)
  if db is None:yield None;return
  try:
   with db:yield db
  finally:db.close()
 def state(self):
  with self.db(write=False) as db:row=db.execute("SELECT body FROM state WHERE id='current'").fetchone() if db else None
  return json.loads(row[0]) if row else {'phase':'idle'}
 async def publish(self,**values):
  state={**self.state(),**values}
  with self.db() as db:db.execute("INSERT OR REPLACE INTO state VALUES ('current',?)",(json.dumps(state),))
  self.changed()
  return state
 def receipt(self,command):
  with self.db(write=False) as db:row=db.execute('SELECT body FROM commands WHERE id=?',(command,)).fetchone() if db else None
  return json.loads(row[0]) if row else None
 def save(self,row,signature):
  with self.db() as db:db.execute('INSERT OR REPLACE INTO commands VALUES (?,?,?)',(row['commandId'],signature,json.dumps(row)))
 def inspect(self,args):
  limit=args.get('limit',50);cursor=args.get('cursor','')
  if type(limit)is not int or not 1<=limit<=100 or not isinstance(cursor,str) or len(cursor)>200:raise ValueError('Bounded generation page required')
  with self.db(write=False) as db:
   rows=db.execute('SELECT id,body FROM inventory WHERE id>? ORDER BY id LIMIT ?',(cursor,limit+1)).fetchall() if db else []
   diagnostics=[json.loads(r[0]) for r in db.execute('SELECT body FROM diagnostics ORDER BY sequence DESC LIMIT 50')] if db else []
   failure=db.execute("SELECT body FROM diagnostics WHERE json_extract(body,'$.status') IN ('failed','interrupted','rejected') ORDER BY sequence DESC LIMIT 1").fetchone() if db else None
  from .native.updates import group_sources
  items=group_sources([json.loads(r[1]) for r in rows[:limit]])
  return {**self.state(),'pointer':active_release(self.home),'items':items,'nextCursor':rows[limit-1][0] if len(rows)>limit else None,'diagnostics':{'events':diagnostics[::-1],'attemptId':self.state().get('attemptId'),'lastFailure':json.loads(failure[0]) if failure else None},'scope':'native-runtime','applicationUpdateSupported':False}
 async def perform(self,workspace,operation,args):
  if operation not in OPERATIONS:raise ValueError('Unknown generation operation')
  if operation=='generations.inspect':return self.inspect(args)
  if operation=='generations.current':
   from .runtime_inspection import current
   return await asyncio.to_thread(current,self.home,args,self.state())
  if operation=='generations.repair.preview':
   from .generation_repair import preview
   return await asyncio.to_thread(preview,self.home,args)
  if operation=='generations.receipt':
   result=self.receipt(args.get('commandId'));return {'receipt':result}
  if operation=='generations.launcher':return await launcher(self.home,args.get('generation') or active_release(self.home).get('current'))
  command=args.get('commandId')
  if not isinstance(command,str) or not 1<=len(command)<=200:raise ValueError('Durable commandId required')
  signature=digest({'operation':operation,'args':args,'workspace':workspace})
  async with AsyncFileLock(self.directory/'generation-owner.lock'):
   previous=self.receipt(command)
   if previous:
    with self.db(write=False) as db:known=db.execute('SELECT signature FROM commands WHERE id=?',(command,)).fetchone()[0]
    if known!=signature:raise ValueError('Generation command identity conflicts with its saved request')
    if previous['state']=='running':
     previous={**previous,'state':'unknown','detail':'Owner exited before settlement. Inspect receipts; no work was replayed.'};self.save(previous,signature)
    return previous
   row={'commandId':command,'operation':operation,'state':'running','createdAt':time.time()}
   if operation=='generations.repair':row['candidateGeneration']=uuid.uuid4().hex
   self.save(row,signature)
   self.diagnostics.state={'attemptId':command}
   await self.publish(attemptId=command)
   phase='runtime-'+operation.split('.')[-1];started=time.monotonic()
   self.diagnostics.record(phase,'started',commandId=command)
   try:
    if operation=='generations.check':result=await self.check()
    elif operation=='generations.prepare':result=await self.prepare(args)
    elif operation=='generations.repair':
     from .generation_repair import rebuild
     result=await rebuild(self,args,row['candidateGeneration'])
    elif operation in {'generations.promote','generations.rollback'}:result=await self.promote(args,rollback=operation.endswith('rollback'))
    elif operation=='generations.ensure':
     from .native.runtime_profiles import ensure
     sid=args.get('nativeSessionId')
     from .native.session_files import validate_id
     validate_id(sid)
     generation=identity(args.get('generation') or active_release(self.home).get('current'))
     selected=await ensure(self.home,generation,{'id':sid,'workspace':workspace,'bundle':args.get('bundle')},shared_home=self.config.get('home'))
     result=await launcher(self.home,selected)
    row={**row,'state':'succeeded','result':result,'settledAt':time.time()};self.save(row,signature);self.diagnostics.record(phase,'succeeded',commandId=command,durationMs=round((time.monotonic()-started)*1000));return row
   except BaseException as error:
    from .native.update_diagnostics import exception_type
    row={**row,'state':'unknown' if isinstance(error,asyncio.CancelledError) else 'failed','errorType':exception_type(error),'detail':'Generation operation failed; inspect sanitized diagnostics.','settledAt':time.time()};self.save(row,signature)
    self.diagnostics.record(phase,'interrupted' if row['state']=='unknown' else 'failed',commandId=command,**{'errorType':row['errorType'],**getattr(error,'diagnostic_facts',{}),'durationMs':round((time.monotonic()-started)*1000)})
    await self.publish(phase='error',error=row['detail']);
    if not isinstance(error,Exception):raise
    return row
 async def check(self):
  started=time.monotonic();started_at=time.time()
  await self.publish(phase='checking',error=None)
  generation=active_release(self.home).get('current');base=(self.directory/'releases'/generation/'foundation') if generation else self.home/'foundation'
  rows=await environments.update_inventory(self.home)
  if not (environments.project_path(self.home,generation)/'.venv').is_dir():
   rows.append({'id':'runtime:environment','kind':'runtime environment','label':'Qualified native runtime','status':'update','eligible':True,'detail':'Prepare an isolated qualified runtime for future workers.'})
  from .native.update_sources import bindings
  rows.extend(await bindings(self.home,base,{}))
  for meta in sorted((base/'cache').rglob('.amplifier_cache_meta.json')):
   root=meta.parent
   if meta.is_symlink() or root.is_symlink() or not root.resolve().is_relative_to(base.resolve()):continue
   try:
    data=json.loads(meta.read_text());url=data['git_url'];ref=data.get('ref') or 'HEAD';source_key(url,ref)
    current=await process('git','rev-parse','HEAD',cwd=root,timeout=10);dirty,_=await cache_changes(root)
    rows.append({'id':hashlib.sha256(str(root.relative_to(base)).encode()).hexdigest()[:20],'label':safe_label(url),'ref':ref,'current':str(current),'status':'local_changes' if dirty else 'pinned' if pinned(ref) else 'not_checked','path':str(root.relative_to(base)),'url':url,'kind':'bundle / module','eligible':not dirty and not pinned(ref)})
   except (ValueError,OSError,RuntimeError,KeyError):rows.append({'id':digest(str(meta))[:20],'label':'Unrecognized native source','status':'check_failed','eligible':False})
   if len(rows)>5000:raise ValueError('Native source inventory exceeds the explicit maintenance bound')
  semaphore=asyncio.Semaphore(8)
  async def update(row):
   if not row.get('eligible') or not row.get('url'):return
   async with semaphore:
    try:
     value=await process('git','ls-remote',row['url'],row['ref'],timeout=90);matches={l.split()[0] for l in value.splitlines() if l.split()}
     if len(matches)!=1:raise ValueError('Source did not resolve uniquely')
     row['latest']=next(iter(matches));row['status']='current' if row['latest']==row.get('current') else 'update'
    except (ValueError,RuntimeError,TimeoutError):row['status']='check_failed'
  await asyncio.gather(*(update(row) for row in rows))
  if active_release(self.home).get('current')!=generation:raise ValueError('Selected generation changed during check')
  with self.db() as db:
   db.execute('DELETE FROM inventory');db.executemany('INSERT INTO inventory VALUES (?,?)',[(r['id'],json.dumps(r)) for r in rows])
  check=digest({'generation':generation,'rows':rows});available=sum(r.get('status')=='update' for r in rows)
  timing={'startedAt':started_at,'finishedAt':time.time(),'elapsedMs':round((time.monotonic()-started)*1000),'requests':sum(bool(r.get('eligible') and r.get('url')) for r in rows),'cached':0}
  await self.publish(phase='available' if available else 'checked',checkId=check,checkedGeneration=generation,lastCheck=time.time(),available=available,checkTiming=timing,recentChecks=[*self.state().get('recentChecks',[])[-4:],timing])
  return {'checkId':check,'available':available,'sourceCount':len(rows)}
 async def prepare(self,args):
  state=self.state()
  if (args.get('checkId') is not None and args['checkId']!=state.get('checkId')) or 'checkId' not in state:raise ValueError('Prepare requires the exact current source check')
  if active_release(self.home).get('current')!=state.get('checkedGeneration'):raise ValueError('Active generation changed since check')
  with self.db(write=False) as db:self.inventory=[json.loads(r[0]) for r in db.execute('SELECT body FROM inventory ORDER BY id')] if db else []
  if any(r.get('status') in {'check_failed','local_changes'} for r in self.inventory):raise ValueError('Resolve protected or unavailable sources before preparing')
  candidates=[r for r in self.inventory if r.get('eligible') and r.get('status')=='update']
  generation=uuid.uuid4().hex;stage=self.directory/'releases'/generation;stage.mkdir(parents=True,mode=0o700)
  await self.publish(phase='staging',pendingRelease=None,preparingRelease=generation,error=None)
  from .native.update_storage import copy_snapshot
  previous=state.get('checkedGeneration');source=(self.directory/'releases'/previous/'foundation') if previous else self.home/'foundation'
  if source.exists():await asyncio.to_thread(copy_snapshot,source,stage/'foundation')
  else:(stage/'foundation').mkdir()
  for name in ('config','routing'):
   if (self.home/name).exists():await asyncio.to_thread(copy_snapshot,self.home/name,stage/name)
  from .native.session_files import amplifier_home
  shared=stage/'shared-config';shared.mkdir(mode=0o700)
  for name in ('settings.yaml','keys.env','routing'):
   origin=amplifier_home()/name
   if origin.is_dir():await asyncio.to_thread(copy_snapshot,origin,shared/name)
   elif origin.is_file():await asyncio.to_thread(shutil.copy2,origin,shared/name)
  for file in [*(stage/'config').rglob('*.yaml'),shared/'settings.yaml',stage/'foundation/registry.json']:
   if file.exists():write_private(file,file.read_text().replace(str(source),str(stage/'foundation')))
  cache_rows=[]
  for row in candidates:
   if row.get('kind') in {'runtime dependency','runtime environment'}:continue
   if row.get('sharedSource'):
    from .native.update_sources import stage_binding
    await self.diagnostics.run('source-stage',stage_binding,self,stage,row)
   else:cache_rows.append(row)
  await self.stage_cache_sources(stage,cache_rows)
  await self.validate(stage,generation)
  from amplifier_acp import __version__
  write_private(stage/'validated.json',json.dumps({'generationSchema':1,'workerProtocol':1,'hostVersion':__version__,'createdAt':time.time(),'sources':len(candidates),'commandId':args['commandId']}))
  await self.publish(phase='staged',pendingRelease=generation,preparingRelease=None)
  return {'generation':generation,'qualified':True}
 async def stage_cache_sources(self,stage,rows):
  semaphore=asyncio.Semaphore(4);targets={}
  async def prepare(row):
   target=stage/'foundation'/row['path']
   if not target.resolve().is_relative_to((stage/'foundation').resolve()):raise ValueError('Source path escaped generation')
   current=await process('git','rev-parse','HEAD',cwd=target);dirty,artifacts=await cache_changes(target)
   metadata=json.loads((target/'.amplifier_cache_meta.json').read_text())
   if dirty or current!=row['current'] or source_key(metadata['git_url'],metadata.get('ref') or 'HEAD')!=source_key(row['url'],row['ref']):raise ValueError('Source changed after check')
   if artifacts:await process('git','--literal-pathspecs','-c','core.hooksPath=/dev/null','restore','--source=HEAD','--worktree','--',*artifacts,cwd=target)
   await self.diagnostics.run('source-fetch',process,'git','-c','core.hooksPath=/dev/null','fetch','--depth=1',row['url'],row['latest'],cwd=target)
   await self.diagnostics.run('source-checkout',process,'git','-c','core.hooksPath=/dev/null','checkout','--detach',row['latest'],cwd=target)
   metadata['commit']=row['latest'];write_private(target/'.amplifier_cache_meta.json',json.dumps(metadata))
  async def one(row):
   target=(stage/'foundation'/row['path']).resolve();lock=targets.setdefault(target,asyncio.Lock())
   async with semaphore,lock:await prepare(row)
  tasks=[asyncio.create_task(one(row)) for row in rows]
  try:await asyncio.gather(*tasks)
  except BaseException:
   for task in tasks:
    if not task.done():task.cancel()
   await asyncio.gather(*tasks,return_exceptions=True);raise
 async def promote(self,args,rollback=False):
  pointer=active_release(self.home)
  if args.get('expectedCurrent')!=pointer.get('current'):raise ValueError('Selected runtime changed before promotion')
  target=pointer.get('previous') if rollback else args.get('generation')
  if target is None:raise ValueError('A qualified rollback or promotion generation is required')
  target=identity(target);launch=await launcher(self.home,target)
  from .native.runtime_qualification import verify_recorded
  await asyncio.to_thread(verify_recorded,environments.project_path(self.home,target),self.directory/'releases'/target)
  new={'current':target,'previous':pointer.get('current'),'commandId':args['commandId'],'activatedAt':time.time()}
  write_private(self.directory/'active.json',json.dumps(new))
  await self.publish(phase='installed',pendingRelease=None,canRollback=bool(new['previous']),error=None)
  return {'pointer':new,'appliesTo':'future-workers','runningWorkersChanged':False}

 async def validate(self,stage,release,*,profiles=None):
     from .native.runtime_environment import stage as stage_runtime, receipt_directory
     from .native.runtime_qualification import freeze, prepare_overrides, verify_recorded
     receipt=receipt_directory(self.home,release)
     fresh=not (receipt/'runtime.lock').exists()
     project=await stage_runtime(self, release, [row for row in self.inventory if row.get('eligible') and (row.get('status') == 'update' or
         (row.get('kind') == 'runtime dependency' and row.get('status') == 'current'))], finalize=not fresh)
     # Profile preparation follows the current app offering. Saved chats and
     # workspace-specific overlays qualify lazily when explicitly resumed.
     # Scanning historical workspace/bundle pairs made a catalog-only update
     # run dozens of identical dependency installers. Do not restore that loop.
     from .native.bundles import offered_profiles
     from .native.host.config import read_config
     workspace = stage / 'qualification-workspace'
     workspace.mkdir(exist_ok=True)
     config = read_config(workspace, home=stage, shared_home=stage/'shared-config', global_only=True)
     profiles = profiles if profiles is not None else self.config.get('generationProfiles') or offered_profiles(config)
     if not profiles:
         raise ValueError('No app-level conversation profiles are configured')
     configs = [(str(workspace), profile) for profile in profiles]
     profiles_file = stage / 'profiles.json'
     write_private(profiles_file, json.dumps(profiles))
     from .native.update_sources import store_environment, worker_supports_shared, adopt_clean_sources
     if fresh:
         from .preparation_policy import staging_environment
         # Materialize the candidate lock once, rather than uv-syncing before
         # every workspace probe. The serving environment is never targeted.
         await self.diagnostics.run('ecosystem-runtime-prepare', process, shutil.which('uv'), 'sync', '--locked',
             '--project', str(project), '--python', '3.13',
             env=staging_environment(os.environ,project,self.home/'runtime',prepared=False), timeout=900)
     shared = store_environment(self.home) if worker_supports_shared(project) else {}
     if fresh and shared:
         await self.diagnostics.run('ecosystem-source-sharing', adopt_clean_sources, self, stage)
     env={**os.environ,**shared,'AMPLIFIER_WEB_HOME':str(stage),'AMPLIFIER_HOME':str(stage/'shared-config'),'AMPLIFIER_UNIFIED_RELEASE':'','AMPLIFIER_INSTALL_PREPARATION':uuid.uuid4().hex}
     if not shared:
         env.pop('AMPLIFIER_SOURCE_STORE', None)  # Preserve pinned/older workers' legacy source contract.
     async def probe(project, *, refresh=False):
         qualified=fresh or (receipt/'runtime-installed.json').exists()
         overrides=await self.diagnostics.run('ecosystem-runtime-policy',prepare_overrides,project,receipt/'runtime-install-overrides.txt') if qualified else Path(__file__).parent/'native/runtime_deps/compatibility.txt'
         command=[shutil.which('uv'),'run','--locked','--no-sync','--project',str(project),'--python','3.13','python',str(Path(__file__).parent/'native/update_probe.py')]
         flags=['--install-overrides',str(overrides)]
         flags.append('--global-only')
         if refresh:
             from .preparation_policy import staging_environment
             staging_env=staging_environment(env,project,self.home/'runtime')
             # One collection pass and one resolver transaction for the shared
             # graph, followed by fresh read-only compatibility processes.
             await self.diagnostics.run('ecosystem-prepare', process, *command, str(workspace), profiles[0],
                 *flags, '--profiles', str(profiles_file), env=staging_env, timeout=900)
             return
         completed=0
         progress={'attemptId':getattr(self.diagnostics,'state',{}).get('attemptId'),
                   'phase':'prepare' if refresh else 'compatibility','total':len(configs),
                   'completed':0,'startedAt':time.time()}
         await self.publish(probeProgress=progress)
         from .preparation_policy import staging_environment
         readonly_env=staging_environment(env,project,self.home/'runtime',readonly=True)
         async def run_one(workspace, bundle):
             nonlocal completed
             async with semaphore:
                 await self.diagnostics.run('ecosystem-prepare' if refresh else 'ecosystem-compatibility',process,*command,workspace,bundle,*flags,
                     env=readonly_env,timeout=900)
                 completed+=1
                 await self.publish(probeProgress={**progress,'completed':completed,'lastCompletedAt':time.time()})
         # Installers remain serial. After freezing, mount isolated workers
         # without uv sync or Foundation dependency installation.
         if not refresh:
             flags.append('--read-only')
         semaphore = asyncio.Semaphore(1 if refresh else 4)
         tasks = [asyncio.create_task(run_one(*config)) for config in sorted(configs)]
         try:
             await asyncio.gather(*tasks)
         finally:
             for task in tasks:
                 if not task.done(): task.cancel()
             await asyncio.gather(*tasks, return_exceptions=True)
     if fresh:
         await self.publish(detail='Preparing changed dependencies in an isolated runtime…')
         # The refresh activator dies with each short-lived probe process.
         # Capture after dynamic module installation, then recreate an
         # ordinary resolver against the frozen graph before activation.
         await probe(project,refresh=True)
         await self.publish(detail='Recording and verifying the exact worker dependencies…',probeProgress=None)
         project=await freeze(self,release,project)
     if (project/'.venv').exists():
         await asyncio.to_thread(verify_recorded,project,receipt)
     await self.publish(detail=f'Checking {len(configs)} offered conversation profiles…')
     await probe(project)
     await asyncio.to_thread(verify_recorded,project,receipt)
     from .native.runtime_profiles import configuration_key
     write_private(stage/'profiles-qualified.json', json.dumps({'profiles': profiles,
         'configuration': configuration_key(config.settings, self.home, stage)}))

async def launcher(home,generation):
 if generation is None:return {'runtimeGeneration':None,'workerCommand':None,'qualified':False}
 generation=identity(generation);home=Path(home);receipt=environments.receipt_directory(home,generation);project=environments.project_path(home,generation)
 if not (receipt/'validated.json').is_file() or not (receipt/'profiles-qualified.json').is_file():raise ValueError('Native generation has no complete qualification receipt')
 from .native.runtime_qualification import verify_recorded,verify_install_overrides
 if not (receipt/'runtime-installed.json').is_file() or (project/'uv.lock').read_bytes()!=(receipt/'runtime.lock').read_bytes():raise ValueError('Native generation lock or installed evidence is missing or changed')
 await asyncio.to_thread(verify_recorded,project,receipt);verify_install_overrides(project,receipt/'runtime-install-overrides.txt')
 python=project/'.venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
 if not python.is_file():raise ValueError('Qualified worker interpreter is missing')
 return {'runtimeGeneration':generation,'workerCommand':[str(python),str(Path(__file__).parent/'native/runtime_worker.py')],'qualified':True}
