"""Optional launcher-authorized administration. No Core session or execution worker."""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path

from .generations import OPERATIONS as GENERATION_OPERATIONS
from .maintenance import OPERATIONS as MAINTENANCE_OPERATIONS
from .history_import import OPERATIONS as HISTORY_IMPORT_OPERATIONS
from .voice_preferences import OPERATIONS as VOICE_PREFERENCE_OPERATIONS
from .permissions import OPERATIONS as PERMISSION_OPERATIONS

OPERATIONS=GENERATION_OPERATIONS | MAINTENANCE_OPERATIONS | HISTORY_IMPORT_OPERATIONS | VOICE_PREFERENCE_OPERATIONS | PERMISSION_OPERATIONS | {
 'voice.configuration','voice.credential','voice.configure',
 'providers.list','providers.schema','providers.models','providers.test','providers.testMessage','providers.credentials',
 'providers.save','providers.remove','providers.move','providers.reorder','providers.finishSetup','providers.login','providers.loginStatus','providers.loginCancel',
 'routing.list','routing.show','routing.save','routing.use','modules.list','modules.save','modules.remove','modules.validate',
 'sources.list','sources.save','sources.remove','sources.validate','bundles.list','bundles.add','bundles.remove','bundles.toggle',
 'bundles.reorder','bundles.move','bundle.discover','bundle.export','bundle.save','bundle.default','configuration.defaults'}

class Administration:
 def __init__(self,config,on_idle=None):
  self.on_idle=on_idle
  self.config=config;self.lock=asyncio.Lock();self.setup=None;self.bundles=None;self.registry=None;self.background_gates={}
  from .admin_lifecycle import AdminLifecycle
  self.lifecycle=AdminLifecycle(self);self.effects=set();self.requests=set()
  self.roots=[Path(p).expanduser().resolve(strict=True) for p in config.get('adminWorkspaceRoots',[])]
  if not self.roots or not all(p.is_dir() for p in self.roots):raise ValueError('Administration requires existing launcher workspace roots.')
 def workspace(self,cwd):
  if not isinstance(cwd,str) or not Path(cwd).is_absolute():raise ValueError('Administration cwd must be absolute.')
  path=Path(cwd).resolve(strict=True)
  from .managed_session import runtime_scope
  managed=runtime_scope(str(path),config=self.config)
  if not path.is_dir() or not managed and not any(path==root or root in path.parents for root in self.roots):raise ValueError('Administration workspace is outside launcher-authorized roots.')
  return str(path)
 def initialize(self):
  if self.setup:return
  # One process has one trusted native home. Worker children inherit it; no client selects it.
  if self.config.get('home'):os.environ['AMPLIFIER_HOME']=str(Path(self.config['home']).expanduser().resolve())
  if self.config.get('appHome'):os.environ['AMPLIFIER_WEB_HOME']=str(Path(self.config['appHome']).expanduser().resolve())
  if self.config.get('registryHome'):os.environ['AMPLIFIER_ACP_REGISTRY_HOME']=str(Path(self.config['registryHome']).expanduser().resolve())
  if self.config.get('runtimeManifest'):os.environ['AMPLIFIER_ACP_RUNTIME_MANIFEST']=str(Path(self.config['runtimeManifest']).resolve(strict=True))
  from .native.host.config import app_home
  from .native.setup import SetupManager
  from .native.registry import RegistryManager
  from .native.bundles import BundleManager
  self.home=app_home();self.setup=SetupManager(self.home);self.registry=RegistryManager(self.home);self.bundles=BundleManager(self.home)
 def app_bundle(self):
  from .native.shared_settings import read_yaml
  settings=read_yaml(self.home/'admin-settings.yaml').get('bundle',{})
  return settings.get('active') if 'active' in settings else self.config.get('bundle')
 async def perform(self,cwd,operation,args,*,fence=None):
  # Validate all request authority before imports, files, probes or network effects.
  workspace=self.workspace(cwd)
  if operation not in OPERATIONS:raise ValueError('Unknown native administration operation.')
  if operation in PERMISSION_OPERATIONS and self.config.get('adminPermissions') is not True:raise ValueError('File-access changes require explicit trusted launcher enablement.')
  if operation in VOICE_PREFERENCE_OPERATIONS and self.config.get('adminVoicePreferences') is not True:raise ValueError('Voice preference changes require explicit trusted launcher enablement.')
  if operation in HISTORY_IMPORT_OPERATIONS and self.config.get('adminHistoryImport') is not True:raise ValueError('History import requires explicit trusted launcher enablement.')
  if operation in MAINTENANCE_OPERATIONS and self.config.get('adminMaintenance') is not True:raise ValueError('Native private maintenance requires explicit trusted launcher enablement.')
  if operation in GENERATION_OPERATIONS and self.config.get('adminGenerations') is not True:raise ValueError('Native generation maintenance requires explicit trusted launcher enablement.')
  if operation in {'voice.credential','voice.configure'} and self.config.get('adminVoiceCredentials') is not True:raise ValueError('Voice credentials require explicit trusted launcher enablement.')
  if not isinstance(args,dict):raise ValueError('Administration args must be an object.')
  if any(key in args for key in ('home','appHome','sessionId','session_id')) or 'cwd' in args and operation!='maintenance.reset.preview':raise ValueError('Native homes and session identity are launcher-owned.')
  if args.get('workspace') and self.workspace(args['workspace'])!=workspace:raise ValueError('Workspace selector disagrees with authorized cwd.')
  settings_dir=Path(workspace)/'.amplifier'
  if settings_dir.exists() and not settings_dir.resolve().is_relative_to(Path(workspace)):
   raise ValueError('Workspace settings escape the authorized workspace.')
  if len(json.dumps(args).encode())>512*1024:raise ValueError('Administration request exceeds size limit.')
  ticket=self.lifecycle.enter(operation,fence);request=asyncio.current_task();self.requests.add(request)
  task=asyncio.create_task(self.execute(workspace,operation,args));self.effects.add(task)
  try:return await asyncio.shield(task)
  except asyncio.CancelledError:
   # Cancellation cannot free the writer gate while a thread/subprocess keeps
   # changing files. Join the owner effect before the request disappears.
   while not task.done():
    try:await asyncio.shield(task)
    except asyncio.CancelledError:continue
    except BaseException:break
   raise
  finally:self.effects.discard(task);self.requests.discard(request);self.lifecycle.leave(ticket)
 async def execute(self,workspace,operation,args):
  if operation in PERMISSION_OPERATIONS:
   from .permissions import Permissions
   from .maintenance_gate import Gate
   owner=Permissions(self.config)
   if operation in {'permissions.get','permissions.receipt'}:return owner.perform(workspace,operation,args)
   with Gate(self.config):return owner.perform(workspace,operation,args)
  if operation in VOICE_PREFERENCE_OPERATIONS:
   from .voice_preferences import VoicePreferences
   from .maintenance_gate import Gate
   owner=VoicePreferences(self.config)
   if operation=='voice.preferences.receipt':return owner.perform(workspace,operation,args)
   with Gate(self.config):return owner.perform(workspace,operation,args)
  if operation in HISTORY_IMPORT_OPERATIONS:
   from .history_import import HistoryImport
   from .maintenance_gate import Gate
   owner=HistoryImport(self.config)
   if operation=='history.import.receipt':return owner.perform(workspace,operation,args)
   with Gate(self.config):return owner.perform(workspace,operation,args)
  if operation=='voice.configuration':
   if args:raise ValueError('Voice configuration reads take no client arguments.')
   from .native.voice_admin import configuration
   from .native.preferences import SettingsStore
   from .maintenance_gate import native_home
   # Exact passive read: no setup/provider initialization and no writer-gate
   # reentry while this owner's coordinator holds a native snapshot lease.
   store=SettingsStore(self.config.get('appHome') or '.',shared_home=native_home(self.config))
   return configuration(store,workspace,credential_access=self.config.get('adminVoiceCredentials') is True,preferences_access=self.config.get('adminVoicePreferences') is True)
  if operation in MAINTENANCE_OPERATIONS:
   if not hasattr(self,'maintenance'):self.maintenance=__import__('amplifier_acp.maintenance',fromlist=['Maintenance']).Maintenance(self)
   return await self.maintenance.perform(workspace,operation,args)
  from .maintenance_gate import Gate
  with Gate(self.config):
   try:return await self.authorized(workspace,operation,args)
   finally:
    # Interactive sign-in outlives its initial request and can later commit
    # settings. Retain it even if its initial presentation callback failed.
    for row in getattr(self.setup,'logins',{}).values():
     task=row.get('task')
     if task is not None and not task.done() and task not in self.background_gates:
      from .admin_lifecycle import AdminGate
      self.background_gates[task]=[Gate(self.config),AdminGate(self.config)]
      def release(done):
       gate=self.background_gates.pop(done,None)
       if gate:
        from .maintenance import release_all
        release_all(gate)
       self.lifecycle.maybe_idle()
      task.add_done_callback(release)
 async def authorized(self,workspace,operation,args):
  if operation in GENERATION_OPERATIONS:
   self.initialize()
   from .generations import Generations
   if not hasattr(self,'generations'):self.generations=Generations(self.home,self.config)
   result=await self.generations.perform(workspace,operation,args)
   if len(json.dumps(result).encode())>768*1024:raise ValueError('Native generation result exceeds bounded representation limit.')
   return result
  async with self.lock:
   self.initialize();values={**args,'workspace':workspace}
   if operation.startswith('voice.'):
    from .native.voice_admin import perform
    result=perform(self.setup,workspace,operation,args,preferences_access=self.config.get('adminVoicePreferences') is True)
   elif operation.startswith(('providers.','routing.')):
    result=await self.setup.perform(operation,values)
    if operation=='providers.loginStatus':result.update(providers=self.setup.provider_rows(workspace),providersWorkspace=workspace)
   elif operation.startswith(('modules.','sources.')):result=await self.registry.perform(operation,values)
   elif operation=='configuration.defaults':
    from .native.draft_defaults import resolve_defaults
    result={**await resolve_defaults(self.home,workspace,args.get('bundle'),self.app_bundle()),'phase':'ready'}
   elif operation=='bundle.default':
    bundle=args.get('bundle');scope=args.get('scope')
    if bundle is not None and (not isinstance(bundle,str) or not bundle.strip()):raise ValueError('Choose a bundle or clear the override.')
    if scope not in {'app','workspace','shared'}:raise ValueError('Choose app, workspace or shared scope.')
    def update(settings):
     if bundle:settings.setdefault('bundle',{})['active']=bundle.strip()
     else:settings.get('bundle',{}).pop('active',None)
    if scope=='app':
     from .native.shared_settings import update_settings
     update_settings(self.home/'admin-settings.yaml',lambda settings:settings.setdefault('bundle',{}).update(active=bundle.strip() if bundle else None))
    else:self.setup.store.update(workspace,'local' if scope=='workspace' else 'global',update)
    from .native.bundle_selection import defaults
    result={'bundleDefaults':defaults(self.home,workspace,self.app_bundle())}
   else:
    # Session export must supply the already-authorized public mount plan, not invent one.
    result=await self.bundles.perform(operation,values,effective_config=args.get('effectiveConfig'),root_bundle=args.get('rootBundle'),provenance=args.get('provenance'),resources=args.get('resources'))
    if operation=='bundles.list':
     from .native.bundle_selection import defaults
     result['bundleDefaults']=defaults(self.home,workspace,self.app_bundle())
   if len(json.dumps(result).encode())>768*1024:raise ValueError('Native administration result exceeds bounded representation limit.')
   return result
 async def close(self):
  self.lifecycle.closing=True
  if self.requests:await asyncio.gather(*list(self.requests),return_exceptions=True)
  if hasattr(self,'maintenance'):await self.maintenance.close()
  if self.setup:await self.setup.close()
  from .maintenance import release_all
  for gates in list(self.background_gates.values()):release_all(gates)
  self.background_gates.clear()
  self.lifecycle.close()
