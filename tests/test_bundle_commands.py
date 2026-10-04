import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import pytest

from client import Client
from native_setup import configuration
from amplifier_acp.bundle_commands import BundleCommands, BundleCommandError, MAX_RESULT

async def initialized(client):
    row=await client.request('initialize',{'protocolVersion':1,'clientCapabilities':{'_meta':{'amplifier.dev/native':{'version':1}}}})
    assert row['result']['agentCapabilities']['_meta']['amplifier.dev/native']['bundleCommands']['passiveReceipt']

class LostReplyClient(Client):
    """Drop the selected transport response before delivery to the caller."""
    async def read(self):
        while line:=await self.process.stdout.readline():
            row=json.loads(line)
            if row.get('id')=='lost-reply':
                self.dropped.set_result(None)  # deliberately discard every result byte
            elif row.get('method'):
                self.events.append(row)
                if 'id' in row:raise AssertionError('No tools/provider work belongs in bundle controls fixture')
            elif row.get('id') in self.pending:self.pending[row['id']].set_result(row)
    async def lose(self,method,params):
        self.dropped=asyncio.get_running_loop().create_future()
        await self.send({'jsonrpc':'2.0','id':'lost-reply','method':method,'params':params})
        await asyncio.wait_for(self.dropped,60)

@pytest.mark.asyncio
async def test_actual_save_activation_export_default_lost_replies_restart_passive(tmp_path):
    config,cwd=configuration(tmp_path)
    settings=json.loads(config.read_text());settings['adminWorkspaceRoots']=[str(cwd)]
    bundle=Path(settings['bundle']);bundle.write_text('\n'.join(line for line in bundle.read_text().splitlines() if not line.strip().startswith('source:'))+'\n')
    audit=tmp_path/'provider-audit';bundle.write_text(bundle.read_text().replace('config: {}','config:\n      selectedModelAudit: '+str(audit)))
    config.write_text(json.dumps(settings));client=await LostReplyClient.launch(config)
    original_settings=(Path(settings['home'])/'settings.yaml').read_bytes()
    async def call(operation,args):
        row=await client.request('_amplifier/native',{'sessionId':sid,'operation':operation,'args':args});assert 'result'in row,row;return row['result']
    async def admin(operation,args):
        row=await client.request('_amplifier/admin',{'cwd':str(cwd),'operation':operation,'args':args});assert 'result'in row,row;return row['result']
    try:
        await initialized(client)
        sid=(await client.request('session/new',{'cwd':str(cwd),'mcpServers':[]}))['result']['sessionId']
        voice=await call('voice.transcript.record',{'callId':'retained-call','itemId':'item','role':'user','text':'original canonical spoken history','append':False,'commandId':'voice-retained'})
        assert voice['recorded']
        original=await call('configuration.inspect',{})
        resources=await call('configuration.exportResources',{})
        plan=original['plan'];plan['session']['context']['config']={'max_tokens':7777}
        save={'commandId':'bundle:outer:save','name':'durable-bundle','scope':'global','effectiveConfig':plan,'resources':resources}
        await client.lose('_amplifier/admin',{'cwd':str(cwd),'operation':'bundle.save','args':save})
        saved=(await admin('bundle.receipt',{'commandId':save['commandId']}))['receipt']
        assert saved['status']=='succeeded' and saved['operation']=='bundle.save'
        import jsonschema
        schema=json.loads((Path(__file__).parents[1]/'schemas/native-bundle-command-receipt.v1.json').read_text())
        jsonschema.validate({'receipt':saved},schema)
        saved_bytes=Path(saved['result']['saved']['uri']).read_bytes()
        current=await call('configuration.inspect',{});assert current['configurationRevision']==original['configurationRevision']
        activate={'commandId':'bundle:outer:activate','bundle':saved['result']['saved']['name']}
        await client.lose('_amplifier/native',{'sessionId':sid,'operation':'bundle.switch','args':activate})
        receipt=(await client.request('_amplifier/bundle/receipt',{'cwd':str(cwd),'sessionId':sid,'commandId':activate['commandId']}))['result']['receipt']
        assert receipt['status']=='succeeded' and receipt['result']['historyPreserved']
        snapshot=await call('history.snapshot',{})
        assert any(row.get('content')=='original canonical spoken history' and row.get('metadata',{}).get('amplifier_voice',{}).get('recordedOnly') for row in snapshot['messages'])
        assert receipt['result']['configuration']['plan']['session']['context']['config']['max_tokens']==7777
        # Distinct export/default commands retain their exact own original result.
        await client.lose('_amplifier/admin',{'cwd':str(cwd),'operation':'bundle.export','args':{**save,'commandId':'bundle:export'}})
        exported=(await admin('bundle.receipt',{'commandId':'bundle:export'}))['receipt']
        assert exported['result']['content']==saved['result']['export']['content']
        await client.lose('_amplifier/admin',{'cwd':str(cwd),'operation':'bundle.default','args':{'commandId':'bundle:default','scope':'app','bundle':'durable-bundle'}})
        default=(await admin('bundle.receipt',{'commandId':'bundle:default'}))['receipt']
        assert default['result']['bundleDefaults']['app']=='durable-bundle'
        history=list((Path(settings['home'])/'projects').rglob('transcript.jsonl'))
        history_before={p:p.read_bytes() for p in history}
        assert not audit.exists()
    finally:await client.close()
    settings['workerCommand']=['/impossible/passive-receipt-worker'];config.write_text(json.dumps(settings))
    client=await Client.launch(config)
    try:
        await initialized(client)
        for identity,expected in [(save['commandId'],saved),('bundle:export',exported),('bundle:default',default)]:
            assert (await admin('bundle.receipt',{'commandId':identity}))['receipt']==expected
        recovered=(await client.request('_amplifier/bundle/receipt',{'cwd':str(cwd),'sessionId':sid,'commandId':activate['commandId']}))['result']['receipt']
        assert recovered==receipt
        # A changed current file is not used to manufacture an original receipt.
        Path(saved['result']['saved']['uri']).write_text('later deliberate fixture edit\n')
        assert (await admin('bundle.receipt',{'commandId':save['commandId']}))['receipt']==saved
        assert await admin('bundle.save',save)==saved['result']
        assert Path(saved['result']['saved']['uri']).read_text()=='later deliberate fixture edit\n'
        conflict=await client.request('_amplifier/admin',{'cwd':str(cwd),'operation':'bundle.save','args':{**save,'name':'different'}})
        assert conflict['error']['data']['executed'] is False
        assert all(p.read_bytes()==value for p,value in history_before.items())
        assert not client.events and not audit.exists()
        assert original_settings in (Path(settings['home'])/'settings.yaml').read_bytes() # original app suppression retained
        assert saved_bytes==saved['result']['export']['content'].encode()
    finally:await client.close()

@pytest.mark.asyncio
async def test_reserved_unknown_never_reexecutes_after_post_effect_failure(tmp_path):
    store=BundleCommands({'appHome':str(tmp_path/'app')},str(tmp_path))
    args={'commandId':'unknown','name':'original'};effects=[]
    async def fail(effect):
        effect();effects.append('write');raise OSError('private error content must not enter receipt')
    with pytest.raises(BundleCommandError) as error:await store.perform('bundle.save',args,fail)
    assert error.value.data['outcome']=='unknown'
    reopened=BundleCommands({'appHome':str(tmp_path/'app')},str(tmp_path))
    with pytest.raises(BundleCommandError):await reopened.perform('bundle.save',args,fail)
    receipt=reopened.receipt('unknown')
    assert effects==['write'] and receipt['status']=='unknown' and 'result' not in receipt
    assert 'private' not in json.dumps(receipt)
    assert BundleCommands({'appHome':str(tmp_path/'app')},str(tmp_path/'other')).receipt('unknown') is None
    assert BundleCommands({'appHome':str(tmp_path/'app')},str(tmp_path),'session').receipt('unknown') is None
    with sqlite3.connect(store.path) as db:
        plan=' '.join(str(row) for row in db.execute('EXPLAIN QUERY PLAN SELECT body FROM commands WHERE id=? AND scope=?',('unknown','x')))
        assert 'INDEX' in plan

@pytest.mark.asyncio
async def test_preflight_refusals_are_durable_bounded_and_not_retry_permission(tmp_path):
    owner=BundleCommands({'appHome':str(tmp_path/'app')},str(tmp_path));args={'commandId':'refused'};calls=[]
    async def fail(effect):calls.append(1);raise ValueError('invalid private input')
    for _ in range(2):
        with pytest.raises(BundleCommandError) as error:await owner.perform('bundle.save',args,fail)
        assert error.value.data['executed'] is False
    assert calls==[1] and owner.receipt('refused')['status']=='refused'
    async def oversized(effect):return {'body':'x'*(MAX_RESULT+1)}
    with pytest.raises(BundleCommandError):await owner.perform('bundle.export',{'commandId':'oversized'},oversized)
    assert owner.receipt('oversized')['status']=='refused'

@pytest.mark.asyncio
async def test_receipts_remain_passive_under_exact_admin_and_native_home_hold(tmp_path):
    from amplifier_acp.admin import Administration
    from amplifier_acp.maintenance_gate import Gate
    from test_admin_lifecycle import CONTEXT
    home=tmp_path/'home';home.mkdir();cwd=tmp_path/'workspace';cwd.mkdir()
    config={'home':str(home),'appHome':str(tmp_path/'app'),'adminWorkspaceRoots':[str(cwd)]}
    owner=Administration(config)
    try:
        saved=await owner.perform(str(cwd),'bundle.default',{'commandId':'default','scope':'app','bundle':'work'})
        assert owner.lifecycle.perform('acquire',CONTEXT)['acquired']
        with Gate(config,exclusive=True):
            receipt=await owner.perform(str(cwd),'bundle.receipt',{'commandId':'default'})
            assert receipt['receipt']['result']==saved
            with pytest.raises(ValueError):await owner.perform(str(cwd),'bundle.default',{'commandId':'another','scope':'app','bundle':'foundation'})
        assert not (home/'projects').exists()
    finally:await owner.close()

@pytest.mark.asyncio
async def test_process_death_after_default_effect_retains_unknown_not_current_state(tmp_path):
    import sys
    home=tmp_path/'home';home.mkdir();cwd=tmp_path/'workspace';cwd.mkdir();config=tmp_path/'config.json'
    values={'home':str(home),'appHome':str(tmp_path/'app'),'adminWorkspaceRoots':[str(cwd)],'workerCommand':['/impossible/worker']}
    config.write_text(json.dumps(values))
    script="from amplifier_acp.bundle_commands import BundleCommands; import os; BundleCommands.finish=lambda *a,**kw:os._exit(73); from amplifier_acp.server import main; main()"
    process=await asyncio.create_subprocess_exec(sys.executable,'-c',script,'--config',str(config),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    client=Client(process)
    args={'commandId':'crashed-default','scope':'app','bundle':'original-durable-value'}
    await initialized(client)
    await client.send({'jsonrpc':'2.0','id':777,'method':'_amplifier/admin','params':{'cwd':str(cwd),'operation':'bundle.default','args':args}})
    assert await asyncio.wait_for(process.wait(),10)==73
    await client.close()
    settings=tmp_path/'app/admin-settings.yaml';before=settings.read_bytes();assert b'original-durable-value' in before
    client=await Client.launch(config)
    try:
        await initialized(client)
        receipt=(await client.request('_amplifier/admin',{'cwd':str(cwd),'operation':'bundle.receipt','args':{'commandId':args['commandId']}}))['result']['receipt']
        assert receipt['status']=='unknown' and 'result' not in receipt
        retry=await client.request('_amplifier/admin',{'cwd':str(cwd),'operation':'bundle.default','args':args})
        assert retry['error']['data']['outcome']=='unknown' and retry['error']['data']['replayed'] is False
        assert settings.read_bytes()==before and not client.events
    finally:await client.close()

@pytest.mark.asyncio
async def test_actual_saved_bundle_requires_model_choice_receipt_is_unapplied(tmp_path):
    config,cwd=configuration(tmp_path);values=json.loads(config.read_text());values['adminWorkspaceRoots']=[str(cwd)]
    bundle=Path(values['bundle']);bundle.write_text('\n'.join(line for line in bundle.read_text().splitlines() if not line.strip().startswith('source:'))+'\n')
    audit=tmp_path/'provider-audit';bundle.write_text(bundle.read_text().replace('config: {}','config:\n      selectedModelAudit: '+str(audit)))
    config.write_text(json.dumps(values));client=await Client.launch(config)
    async def native(op,args={}):
        result=await client.request('_amplifier/native',{'sessionId':sid,'operation':op,'args':args});assert 'result'in result,result;return result['result']
    try:
        await initialized(client);sid=(await client.request('session/new',{'cwd':str(cwd),'mcpServers':[]}))['result']['sessionId']
        providers=await native('configuration.providers')
        await native('provider.select',{'instance':providers['providers'][0]['id'],'model':'fixture-model'})
        original=await native('configuration.inspect');plan=original['plan'];plan['providers'][0]['instance_id']='different-instance'
        resources=await native('configuration.exportResources')
        saved=await client.request('_amplifier/admin',{'cwd':str(cwd),'operation':'bundle.save','args':{'commandId':'separate-save','name':'different-provider','effectiveConfig':plan,'resources':resources}})
        assert 'result'in saved,saved
        args={'commandId':'separate-activation','bundle':'different-provider'}
        result=await native('bundle.switch',args)
        assert result['requiresModelChoice'] and result['applied'] is False
        stored=(await client.request('_amplifier/bundle/receipt',{'cwd':str(cwd),'sessionId':sid,'commandId':args['commandId']}))['result']['receipt']
        assert stored['status']=='succeeded' and stored['result']==result
        assert (await native('configuration.inspect'))['configurationRevision']==original['configurationRevision']
        assert await native('bundle.switch',args)==result
        preserved=(await client.request('_amplifier/admin',{'cwd':str(cwd),'operation':'bundle.receipt','args':{'commandId':'separate-save'}}))['result']['receipt']
        assert preserved['status']=='succeeded' and preserved['result']==saved['result']
        assert not audit.exists()
    finally:await client.close()

@pytest.mark.asyncio
async def test_concurrent_exact_duplicate_observes_unknown_then_original_without_reentry(tmp_path):
    owner=BundleCommands({'appHome':str(tmp_path/'app')},str(tmp_path))
    entered=asyncio.Event();finish=asyncio.Event();calls=[]
    async def effectful(effect):
        effect();calls.append(1);entered.set();await finish.wait();return {'original':'result'}
    original=asyncio.create_task(owner.perform('bundle.save',{'commandId':'concurrent'},effectful))
    await entered.wait()
    try:
        with pytest.raises(BundleCommandError) as duplicate:await owner.perform('bundle.save',{'commandId':'concurrent'},effectful)
        assert duplicate.value.data['outcome']=='unknown' and calls==[1]
    finally:finish.set()
    result=await original
    assert await owner.perform('bundle.save',{'commandId':'concurrent'},effectful)==result and calls==[1]

@pytest.mark.asyncio
async def test_actual_activation_post_effect_receipt_failure_preserves_independent_save(tmp_path):
    import sys
    config,cwd=configuration(tmp_path);values=json.loads(config.read_text());values['adminWorkspaceRoots']=[str(cwd)]
    bundle=Path(values['bundle']);bundle.write_text('\n'.join(line for line in bundle.read_text().splitlines() if not line.strip().startswith('source:'))+'\n')
    audit=tmp_path/'provider-audit';bundle.write_text(bundle.read_text().replace('config: {}','config:\n      selectedModelAudit: '+str(audit)))
    config.write_text(json.dumps(values))
    script="""from amplifier_acp.bundle_commands import BundleCommands
original=BundleCommands.finish
def finish(self,row,**kwargs):
 if row['operation']=='bundle.switch':raise OSError('injected after confirmed remount before receipt commit')
 return original(self,row,**kwargs)
BundleCommands.finish=finish
from amplifier_acp.server import main
main()
"""
    process=await asyncio.create_subprocess_exec(sys.executable,'-c',script,'--config',str(config),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    client=Client(process)
    try:
        await initialized(client);sid=(await client.request('session/new',{'cwd':str(cwd),'mcpServers':[]}))['result']['sessionId']
        async def control(op,args={}):return await client.request('_amplifier/native',{'sessionId':sid,'operation':op,'args':args})
        plan=(await control('configuration.inspect'))['result']['plan'];plan['session']['context']['config']={'max_tokens':4321}
        resources=(await control('configuration.exportResources'))['result']
        save={'commandId':'save-before-unknown','name':'kept-save','effectiveConfig':plan,'resources':resources}
        saved=await client.request('_amplifier/admin',{'cwd':str(cwd),'operation':'bundle.save','args':save});assert 'result' in saved,saved
        args={'commandId':'unknown-activation','bundle':'kept-save'}
        unknown=await control('bundle.switch',args)
        assert unknown['error']['data']['outcome']=='unknown'
        assert (await control('configuration.inspect'))['result']['plan']['session']['context']['config']['max_tokens']==4321
        # Actual changed state does not resolve the missing command outcome.
        receipt=(await client.request('_amplifier/bundle/receipt',{'cwd':str(cwd),'sessionId':sid,'commandId':args['commandId']}))['result']['receipt']
        assert receipt['status']=='unknown' and 'result' not in receipt
        assert (await control('bundle.switch',args))['error']['data']['outcome']=='unknown'
        assert not audit.exists()
    finally:await client.close()
    values['workerCommand']=['/impossible/receipt-worker'];config.write_text(json.dumps(values));client=await Client.launch(config)
    try:
        await initialized(client)
        recovered=(await client.request('_amplifier/admin',{'cwd':str(cwd),'operation':'bundle.receipt','args':{'commandId':save['commandId']}}))['result']['receipt']
        assert recovered['status']=='succeeded' and recovered['result']==saved['result']
        assert (await client.request('_amplifier/bundle/receipt',{'cwd':str(cwd),'sessionId':sid,'commandId':args['commandId']}))['result']['receipt']==receipt
        assert not client.events and not audit.exists()
    finally:await client.close()

@pytest.mark.asyncio
async def test_corrupt_oversized_receipt_refuses_before_payload_materialization(tmp_path):
    owner=BundleCommands({'appHome':str(tmp_path/'app')},str(tmp_path))
    async def original(effect):return {'saved':'original'}
    await owner.perform('bundle.save',{'commandId':'bounded'},original)
    with sqlite3.connect(owner.path) as db:db.execute('UPDATE commands SET body=?',('x'*(MAX_RESULT+4096),))
    with pytest.raises(BundleCommandError) as oversized:owner.receipt('bounded')
    assert oversized.value.data['reason']=='bundle-command-receipt-oversized'
    with pytest.raises(BundleCommandError):await owner.perform('bundle.save',{'commandId':'bounded'},original)


@pytest.mark.asyncio
@pytest.mark.parametrize('refused', [False, True])
async def test_private_diagnostic_reference_is_durable_without_reclassifying_outcome(tmp_path, refused):
    from amplifier_acp.bundle_commands import BundleActivationFailure, BundlePreflightRefusal
    owner = BundleCommands({'appHome': str(tmp_path/'app')}, str(tmp_path))
    args = {'commandId': 'diagnosed'}
    reference = {'diagnosticReceipt': 'startup-' + 'a'*32 + '.log'}
    calls = []
    async def invoke(effect):
        calls.append(1)
        effect()
        if refused: raise BundlePreflightRefusal('Private failure', diagnostic=reference)
        raise BundleActivationFailure(diagnostic=reference)
    for _ in range(2):
        with pytest.raises(BundleCommandError) as error:
            await owner.perform('bundle.switch', args, invoke)
        assert error.value.data['diagnosticReceipt'] == reference['diagnosticReceipt']
        assert (error.value.data.get('executed') is False) == refused
    receipt = owner.receipt(args['commandId'])
    assert calls == [1]
    assert receipt['status'] == ('refused' if refused else 'unknown')
    assert receipt['diagnosticReceipt'] == reference['diagnosticReceipt']
    if not refused:
        assert not {'completedAt','executed','reason','result'} & receipt.keys()
    from jsonschema import validate
    validate({'receipt': receipt}, json.loads((Path(__file__).parents[1]/'schemas/native-bundle-command-receipt.v1.json').read_text()))


@pytest.mark.parametrize('reference', ['../startup-'+'a'*32+'.log', '/private/key', 'secret text', 'startup-'+'a'*33+'.log', None, 12])
def test_bundle_diagnostics_reject_unowned_or_unbounded_references(reference):
    from amplifier_acp.bundle_commands import BundleActivationFailure
    error = BundleActivationFailure(diagnostic={'diagnosticReceipt': reference, 'private': 'secret'})
    assert error.data == {'outcome': 'unknown', 'replayed': False}
