"""Optional launcher-authorized administration. No Core session or execution worker."""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path

OPERATIONS={
 'voice.configuration','voice.credential','voice.configure',
 'providers.list','providers.schema','providers.models','providers.test','providers.testMessage','providers.credentials',
 'providers.save','providers.remove','providers.move','providers.reorder','providers.finishSetup','providers.login','providers.loginStatus','providers.loginCancel',
 'routing.list','routing.show','routing.save','routing.use','modules.list','modules.save','modules.remove','modules.validate',
 'sources.list','sources.save','sources.remove','sources.validate','bundles.list','bundles.add','bundles.remove','bundles.toggle',
 'bundles.reorder','bundles.move','bundle.discover','bundle.export','bundle.save','bundle.default','configuration.defaults'}

class Administration:
 def __init__(self,config):
  self.config=config;self.lock=asyncio.Lock();self.setup=None;self.bundles=None;self.registry=None
  self.roots=[Path(p).expanduser().resolve(strict=True) for p in config.get('adminWorkspaceRoots',[])]
  if not self.roots or not all(p.is_dir() for p in self.roots):raise ValueError('Administration requires existing launcher workspace roots.')
 def workspace(self,cwd):
  if not isinstance(cwd,str) or not Path(cwd).is_absolute():raise ValueError('Administration cwd must be absolute.')
  path=Path(cwd).resolve(strict=True)
  if not path.is_dir() or not any(path==root or root in path.parents for root in self.roots):raise ValueError('Administration workspace is outside launcher-authorized roots.')
  return str(path)
 def initialize(self):
  if self.setup:return
  # One process has one trusted native home. Worker children inherit it; no client selects it.
  if self.config.get('home'):os.environ['AMPLIFIER_HOME']=str(Path(self.config['home']).expanduser().resolve())
  if self.config.get('appHome'):os.environ['AMPLIFIER_WEB_HOME']=str(Path(self.config['appHome']).expanduser().resolve())
  if self.config.get('registryHome'):os.environ['AMPLIFIER_ACP_REGISTRY_HOME']=str(Path(self.config['registryHome']).expanduser().resolve())
  from .native.host.config import app_home
  from .native.setup import SetupManager
  from .native.registry import RegistryManager
  from .native.bundles import BundleManager
  self.home=app_home();self.setup=SetupManager(self.home);self.registry=RegistryManager(self.home);self.bundles=BundleManager(self.home)
 def app_bundle(self):
  from .native.shared_settings import read_yaml
  settings=read_yaml(self.home/'admin-settings.yaml').get('bundle',{})
  return settings.get('active') if 'active' in settings else self.config.get('bundle')
 async def perform(self,cwd,operation,args):
  # Validate all request authority before imports, files, probes or network effects.
  workspace=self.workspace(cwd)
  if operation not in OPERATIONS:raise ValueError('Unknown native administration operation.')
  if operation in {'voice.credential','voice.configure'} and self.config.get('adminVoiceCredentials') is not True:raise ValueError('Voice credentials require explicit trusted launcher enablement.')
  if not isinstance(args,dict):raise ValueError('Administration args must be an object.')
  if any(key in args for key in ('home','appHome','cwd','sessionId','session_id')):raise ValueError('Native homes and session identity are launcher-owned.')
  if args.get('workspace') and self.workspace(args['workspace'])!=workspace:raise ValueError('Workspace selector disagrees with authorized cwd.')
  settings_dir=Path(workspace)/'.amplifier'
  if settings_dir.exists() and not settings_dir.resolve().is_relative_to(Path(workspace)):
   raise ValueError('Workspace settings escape the authorized workspace.')
  if len(json.dumps(args).encode())>512*1024:raise ValueError('Administration request exceeds size limit.')
  async with self.lock:
   self.initialize();values={**args,'workspace':workspace}
   if operation.startswith('voice.'):
    from .native.voice_admin import perform
    result=perform(self.setup,workspace,operation,args)
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
  if self.setup:await self.setup.close()
