import asyncio,hashlib,json,subprocess,sys,zipfile
from pathlib import Path
import pytest
from amplifier_acp.generations import Generations,digest
from amplifier_acp.generation_repair import preview
from amplifier_acp.native import runtime_environment as env,runtime_qualification as qualification

async def installed_fixture(tmp_path):
    generation='a'*32;directory=env.receipt_directory(tmp_path,generation);directory.mkdir(parents=True)
    project=tmp_path/'runtime'/('q-'+generation);project.mkdir(parents=True)
    wheel=tmp_path/'repair_fixture-1.0-py3-none-any.whl'
    with zipfile.ZipFile(wheel,'w') as archive:
        archive.writestr('repair_fixture.py','VALUE="qualified-original"\n')
        archive.writestr('repair_fixture-1.0.dist-info/METADATA','Metadata-Version: 2.1\nName: repair-fixture\nVersion: 1.0\n')
        archive.writestr('repair_fixture-1.0.dist-info/WHEEL','Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        archive.writestr('repair_fixture-1.0.dist-info/RECORD','')
    manifest='[project]\nname="repair-runtime"\nversion="0.1.0"\nrequires-python=">=3.13"\ndependencies=["repair-fixture"]\n[tool.uv.sources]\nrepair-fixture={path='+json.dumps(str(wheel))+'}\n'
    (project/'pyproject.toml').write_text(manifest)
    subprocess.run(['uv','sync','--offline','--project',str(project),'--python','3.13'],check=True,capture_output=True)
    graph=qualification.installed_graph(project)
    for name,body in {'runtime-project.json':{'project':project.name},'runtime-installed.json':graph,'profiles-qualified.json':{'profiles':['fixture']},'validated.json':{'generationSchema':1},'runtime-sources.json':{}}.items():(directory/name).write_text(json.dumps(body))
    for name,body in {'runtime.toml':manifest,'runtime-base.toml':manifest,'runtime.lock':(project/'uv.lock').read_text()}.items():(directory/name).write_text(body)
    # Record the exported policy before recording graph presence, matching freeze.
    (directory/'runtime-installed.json').unlink()
    await qualification.prepare_overrides(project,directory/'runtime-install-overrides.txt')
    (directory/'runtime-installed.json').write_text(json.dumps(graph))
    (tmp_path/'updates/active.json').write_text(json.dumps({'current':generation}))
    (tmp_path/'projects/retained-session').mkdir(parents=True);(tmp_path/'projects/retained-session/events.jsonl').write_text('original history\n')
    return generation,project,directory

@pytest.mark.asyncio
async def test_repair_real_uv_environment_preserves_original_and_requires_separate_activation(tmp_path,monkeypatch):
    generation,original,directory=await installed_fixture(tmp_path);owner=Generations(tmp_path,{})
    module=next((original/'.venv').glob('lib/python*/site-packages/repair_fixture.py'));module.write_text('VALUE="damaged-original"\n')
    original_files={p:p.read_bytes() for p in directory.iterdir() if p.is_file()};pointer=(tmp_path/'updates/active.json').read_bytes()
    summary=preview(tmp_path,{'generation':generation});probes=[]
    async def validate(stage,release,*,profiles):
        assert profiles==['fixture'];project=env.project_path(tmp_path,release)
        result=subprocess.check_output([str(project/'.venv/bin/python'),'-c','import repair_fixture;print(repair_fixture.VALUE)'],text=True).strip()
        assert result=='qualified-original';probes.append(release);qualification.verify_recorded(project,stage)
        (stage/'profiles-qualified.json').write_text(json.dumps({'profiles':profiles,'fixtureQualification':True}))
    monkeypatch.setattr(owner,'validate',validate)
    args={'commandId':'repair-1','generation':generation,'expectedSourceHash':summary['sourceHash'],'expectedCurrent':generation}
    result=await owner.perform(str(tmp_path),'generations.repair',args)
    assert result['state']=='succeeded',result
    repaired=result['result']['generation'];assert probes==[repaired] and repaired!=generation
    assert result['result']['activation']=='separate-generation-promotion'
    assert (tmp_path/'updates/active.json').read_bytes()==pointer and module.read_text()=='VALUE="damaged-original"\n'
    assert all(p.read_bytes()==raw for p,raw in original_files.items())
    assert (tmp_path/'projects/retained-session/events.jsonl').read_text()=='original history\n'
    assert await owner.perform(str(tmp_path),'generations.repair',args)==result and probes==[repaired]
    assert len(list((tmp_path/'runtime').iterdir()))==2

@pytest.mark.asyncio
async def test_repair_stale_receipts_fail_before_install_and_unknown_never_replays(tmp_path,monkeypatch):
    generation,original,directory=await installed_fixture(tmp_path);owner=Generations(tmp_path,{})
    summary=preview(tmp_path,{});args={'commandId':'stale','generation':generation,'expectedSourceHash':summary['sourceHash'],'expectedCurrent':generation}
    (directory/'runtime-base.toml').write_text('changed')
    result=await owner.perform(str(tmp_path),'generations.repair',args)
    assert result['state']=='failed' and len(list((tmp_path/'runtime').iterdir()))==1
    args={**args,'commandId':'lost','expectedSourceHash':preview(tmp_path,{})['sourceHash']}
    owner.save({'commandId':'lost','operation':'generations.repair','state':'running','candidateGeneration':'b'*32},digest({'operation':'generations.repair','args':args,'workspace':str(tmp_path)}))
    unknown=await owner.perform(str(tmp_path),'generations.repair',args)
    assert unknown['state']=='unknown' and unknown['candidateGeneration']=='b'*32
    assert len(list((tmp_path/'runtime').iterdir()))==1

@pytest.mark.asyncio
async def test_repair_failed_profile_preserves_candidate_for_inspection_without_qualification(tmp_path,monkeypatch):
    generation,original,directory=await installed_fixture(tmp_path);owner=Generations(tmp_path,{})
    async def refused(*_,**__):raise ValueError('fixture profile could not mount read-only')
    monkeypatch.setattr(owner,'validate',refused)
    args={'commandId':'profile-fail','generation':generation,'expectedSourceHash':preview(tmp_path,{})['sourceHash'],'expectedCurrent':generation}
    result=await owner.perform(str(tmp_path),'generations.repair',args);assert result['state']=='failed'
    candidate=env.receipt_directory(tmp_path,result['candidateGeneration'])
    assert candidate.exists() and (candidate/'repair-source.json').exists() and not (candidate/'validated.json').exists()
    assert json.loads((tmp_path/'updates/active.json').read_text())['current']==generation
    with pytest.raises(ValueError,match='bounded native qualification'):
        (directory/'runtime.toml').unlink();(directory/'runtime.toml').symlink_to(original/'pyproject.toml');preview(tmp_path,{})

@pytest.mark.asyncio
async def test_interrupted_repair_retains_exact_unknown_candidate_without_second_install(tmp_path,monkeypatch):
    generation,original,directory=await installed_fixture(tmp_path);owner=Generations(tmp_path,{})
    entered=asyncio.Event();probes=[]
    async def delayed(stage,release,*,profiles):
        probes.append(release);entered.set();await asyncio.Event().wait()
    monkeypatch.setattr(owner,'validate',delayed)
    args={'commandId':'interrupted','generation':generation,'expectedSourceHash':preview(tmp_path,{})['sourceHash'],'expectedCurrent':generation}
    running=asyncio.create_task(owner.perform(str(tmp_path),'generations.repair',args));await asyncio.wait_for(entered.wait(),10)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):await running
    saved=owner.receipt('interrupted');assert saved['state']=='unknown' and saved['candidateGeneration']==probes[0]
    assert await owner.perform(str(tmp_path),'generations.repair',args)==saved
    assert probes==[saved['candidateGeneration']] and len(list((tmp_path/'runtime').iterdir()))==2
    assert not (env.receipt_directory(tmp_path,saved['candidateGeneration'])/'validated.json').exists()

def test_repair_probe_diagnostics_report_sanitized_config_failures_without_secret_data():
    from amplifier_acp.native.module_failures import ConfiguredModuleError
    from amplifier_acp.native.update_diagnostics import probe_failure,probe_record,PROBE_PREFIX
    raw={'module':'provider-fixture','type':'provider','reason_code':'provider_configuration_failed','instance_id':'configured','apiKey':'DO-NOT-RECORD'}
    error=ConfiguredModuleError([raw]*12);facts=probe_failure(error,'prepare');parsed=probe_record(PROBE_PREFIX+json.dumps(facts))
    assert parsed['reason']=='configured-module-failed' and parsed['errorType']=='ConfiguredModuleError'
    assert parsed['moduleFailuresTruncated'] and len(parsed['moduleFailures'])==8
    assert 'DO-NOT-RECORD' not in json.dumps(parsed) and 'apiKey' not in json.dumps(parsed)
