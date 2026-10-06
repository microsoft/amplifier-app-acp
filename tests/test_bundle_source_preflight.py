import json
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
import pytest
from amplifier_acp.native import bundle_selection
from amplifier_acp.source_closure import SourceClosureDenied
from amplifier_acp.bundle_commands import BundleCommands,BundleCommandError


def worker_fixture(tmp_path,monkeypatch):
 home=tmp_path/'app';workspace=tmp_path/'workspace';workspace.mkdir();shared=tmp_path/'shared';shared.mkdir();monkeypatch.setenv('AMPLIFIER_HOME',str(shared))
 controls=SimpleNamespace(require_idle=Mock(),checkpoint=AsyncMock(),close=AsyncMock())
 session=SimpleNamespace(cleanup=AsyncMock())
 worker=SimpleNamespace(controls=controls,session=session,workspace=workspace,home=home,runtime=SimpleNamespace(session_id='fixture-session'),start_config={'bundle':'original'},execution=None,bundle_preview=None)
 diagnostic={'diagnosticReceipt':'startup-'+'1'*32+'.log'}
 monkeypatch.setattr(bundle_selection,'_failure_reference',lambda *args:diagnostic)
 return worker,controls,session

@pytest.mark.asyncio
@pytest.mark.parametrize('error,expected',[(SourceClosureDenied(),'refused'),(ValueError('invalid candidate'),'refused'),(RuntimeError('unknown preparation failure'),'unknown')])
async def test_exact_receipt_classifies_only_proven_inspection_refusal(tmp_path,monkeypatch,error,expected):
 worker,controls,session=worker_fixture(tmp_path,monkeypatch)
 inspect=AsyncMock(side_effect=error);monkeypatch.setattr(bundle_selection,'inspect_bundle',inspect)
 owner=BundleCommands({'appHome':str(worker.home)},str(worker.workspace),'fixture-session')
 calls=[]
 async def invoke(effect):
  calls.append(1);effect()
  return await bundle_selection.switch(worker,{'bundle':'target'})
 args={'commandId':'owned-inspection-fixture','bundle':'target'}
 with pytest.raises(BundleCommandError) as captured:await owner.perform('bundle.switch',args,invoke)
 assert owner.receipt(args['commandId'])['status']==expected
 assert (captured.value.data.get('executed') is False)==(expected=='refused')
 controls.checkpoint.assert_not_awaited();controls.close.assert_not_awaited();session.cleanup.assert_not_awaited()
 assert not (worker.home/'sessions/fixture-session/bundle-transition.json').exists()
 with pytest.raises(BundleCommandError):await owner.perform('bundle.switch',args,invoke)
 assert calls==[1] and inspect.await_count==1

@pytest.mark.asyncio
async def test_source_denial_after_dispose_keeps_unknown_and_restores_history(tmp_path,monkeypatch):
 worker,controls,session=worker_fixture(tmp_path,monkeypatch)
 monkeypatch.setattr(bundle_selection,'inspect_bundle',AsyncMock(return_value=({'modelCompatible':True,'bundle':'target'},object())))
 journal=bundle_selection.BundleTransaction(worker.home,worker.workspace,'fixture-session')
 journal.native.mkdir(parents=True);journal.local.mkdir(parents=True)
 metadata=journal.native/'metadata.json';metadata.write_text(json.dumps({'bundle':'original','bundle_name':'original'}))
 transcript=journal.native/'transcript.jsonl';transcript.write_text(json.dumps({'role':'system','content':'original instruction'})+'\n'+json.dumps({'role':'user','content':'original input'})+'\n')
 (journal.local/'control-state.json').write_text(json.dumps({'selection':{'instance':'fixture','model':'original-model'},'goal':'retained'}))
 before={key:path.read_bytes() if path.exists() else None for key,path in journal.paths.items()}
 starts=[]
 async def start(config,**kwargs):
  starts.append(config['bundle'])
  if config['bundle']=='target':
   assert journal.path.exists()
   assert json.loads(metadata.read_text())['bundle']=='target'
   raise SourceClosureDenied()
  worker.controls=SimpleNamespace(close=AsyncMock());worker.session=SimpleNamespace(cleanup=AsyncMock())
 worker.start=start
 owner=BundleCommands({'appHome':str(worker.home)},str(worker.workspace),'fixture-session')
 calls=[]
 async def invoke(effect):
  calls.append(1);effect()
  return await bundle_selection.switch(worker,{'bundle':'target'})
 args={'commandId':'owned-post-dispose-fixture','bundle':'target'}
 with pytest.raises(BundleCommandError) as captured:await owner.perform('bundle.switch',args,invoke)
 assert captured.value.data['outcome']=='unknown' and 'executed' not in captured.value.data
 assert owner.receipt(args['commandId'])['status']=='unknown'
 assert starts==['target','original'] and not journal.path.exists()
 assert {key:path.read_bytes() if path.exists() else None for key,path in journal.paths.items()}==before
 controls.checkpoint.assert_awaited_once();controls.close.assert_awaited_once();session.cleanup.assert_awaited_once()
 with pytest.raises(BundleCommandError):await owner.perform('bundle.switch',args,invoke)
 assert calls==[1] and starts==['target','original']
