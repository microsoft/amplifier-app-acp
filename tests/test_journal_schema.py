"""Inert journal faults only: no bundles, providers, histories or owner effects."""
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest
from amplifier_acp.journal_schema import connect, JournalUnavailable

LEGACY=json.loads((Path(__file__).parent/'fixtures/native-journal-legacy.json').read_text())['profiles']

def family(item):
    return {'messages-quotes':'messages','inputs-outcomes':'inputs'}.get(item['profile'],item['profile'])

@contextmanager
def sql(path):
    db=sqlite3.connect(path)
    try:
        with db:yield db
    finally:db.close()

def legacy(path,item):
    with sql(path) as db:
        for statement in item['tables']:db.execute(statement)
        if family(item)=='coordination':db.execute('INSERT INTO state VALUES(1,0)')

def bytes_hashes(path):
    return {suffix:hashlib.sha256(Path(str(path)+suffix).read_bytes()).hexdigest() for suffix in ('','-wal') if Path(str(path)+suffix).exists()}

def preserved(path,before):
    after=bytes_hashes(path)
    assert all(after.get(key)==value for key,value in before.items())
    # SQLite RO may create an empty WAL/SHM; pre-existing authority must survive.
    assert all(key=='-wal' and value==hashlib.sha256(b'').hexdigest() for key,value in after.items() if key not in before)

def drop(path,table):
    with sql(path) as db:db.execute('DROP TABLE '+table)

@pytest.mark.parametrize('item',LEGACY,ids=lambda x:x['profile'])
def test_exact_historical_layout_enrolls_without_rewriting_records(tmp_path,item):
    path=tmp_path/'journal.sqlite3';legacy(path,item);profile=family(item)
    table=item['tables'][0].split('(',1)[0].split()[-1]
    with sql(path) as db:
        columns=db.execute('PRAGMA table_info('+table+')').fetchall()
        values=tuple(0 if row[2]=='INTEGER' else '{}' if row[1]=='body' else 'original' for row in columns)
        db.execute('INSERT INTO '+table+' VALUES('+','.join('?' for _ in values)+')',values)
    # Passive first read leaves legacy marker untouched.
    with closing(connect(path,profile)) as db:assert db.execute('PRAGMA user_version').fetchone()==(0,)
    with closing(connect(path,profile,write=True)) as db:
        marker=db.execute('PRAGMA user_version').fetchone()[0];assert marker!=0
    with closing(connect(path,profile)) as db:
        assert db.execute('PRAGMA user_version').fetchone()==(marker,)
        assert db.execute('SELECT * FROM '+table).fetchone()==values

@pytest.mark.parametrize('item',LEGACY,ids=lambda x:x['profile'])
def test_missing_authoritative_table_refuses_before_writable_open(tmp_path,item,monkeypatch):
    path=tmp_path/'journal.sqlite3';legacy(path,item);profile=family(item)
    table='provenance' if profile=='inputs' else 'annotations' if profile=='messages' else 'commands' if profile not in {'lifecycle','steering'} else 'receipts'
    drop(path,table);before=bytes_hashes(path);real=sqlite3.connect;opened=[]
    def tracked(database,*args,**kwargs):
        opened.append(str(database));return real(database,*args,**kwargs)
    monkeypatch.setattr(sqlite3,'connect',tracked)
    for write in (False,True):
        with pytest.raises(JournalUnavailable):connect(path,profile,write=write)
    assert all(p==':memory:' or p.endswith('?mode=ro') for p in opened)
    preserved(path,before)

@pytest.mark.parametrize('suffix',['-wal','-shm','-journal'])
@pytest.mark.parametrize('kind',['empty','dangling'])
def test_absent_main_with_any_sidecar_is_not_new(tmp_path,suffix,kind):
    path=tmp_path/'receipt.sqlite3';side=Path(str(path)+suffix)
    if kind=='empty':side.write_bytes(b'')
    else:side.symlink_to(tmp_path/'does-not-exist')
    for write in (False,True):
        with pytest.raises((JournalUnavailable,ValueError)):connect(path,'bundle',write=write)
    assert not path.exists();assert os.path.lexists(side)

@pytest.mark.parametrize('profile',sorted({family(x) for x in LEGACY}))
def test_genuinely_new_passive_read_creates_nothing(tmp_path,profile):
    path=tmp_path/'uncreated'/'journal.sqlite3'
    assert connect(path,profile) is None
    assert not path.parent.exists()

@pytest.mark.parametrize('profile,optional',[('messages','quotes'),('inputs','outcomes')])
def test_optional_namespace_enrollment_and_later_loss(tmp_path,profile,optional):
    path=tmp_path/'journal.sqlite3'
    with closing(connect(path,profile,write=True)) as db:
        baseline=db.execute('PRAGMA user_version').fetchone()[0]
        assert not db.execute('SELECT 1 FROM sqlite_schema WHERE name=?',(optional,)).fetchone()
    with closing(connect(path,profile,write=True,enroll=True)) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]!=baseline
    # Any writer, even the base-only peer, must preserve optional enrollment.
    with closing(connect(path,profile,write=True)) as db:assert db.execute('SELECT 1 FROM sqlite_schema WHERE name=?',(optional,)).fetchone()
    drop(path,optional);before=bytes_hashes(path)
    for write in (False,True):
        with pytest.raises(JournalUnavailable):connect(path,profile,write=write)
    preserved(path,before)

@pytest.mark.parametrize('profile,optional',[('messages','quotes'),('inputs','outcomes')])
def test_optional_ddl_and_marker_roll_back_together(tmp_path,monkeypatch,profile,optional):
    path=tmp_path/'journal.sqlite3'
    with closing(connect(path,profile,write=True)) as db:before=db.execute('PRAGMA user_version').fetchone()[0]
    original=sqlite3.connect
    class FailCommit(sqlite3.Connection):
        def execute(self,sql,*args,**kwargs):
            if sql=='COMMIT' and not self.execute('PRAGMA query_only').fetchone()[0]:raise RuntimeError('inert enrollment interruption')
            return super().execute(sql,*args,**kwargs)
    def injected(database,*args,**kwargs):
        if str(database)==str(path):kwargs['factory']=FailCommit
        return original(database,*args,**kwargs)
    monkeypatch.setattr(sqlite3,'connect',injected)
    with pytest.raises(RuntimeError,match='enrollment interruption'):connect(path,profile,write=True,enroll=True)
    monkeypatch.setattr(sqlite3,'connect',original)
    with closing(connect(path,profile)) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==before
        assert not db.execute('SELECT 1 FROM sqlite_schema WHERE name=?',(optional,)).fetchone()


def test_pre_target_import_migration_only_exact_complete_legacy(tmp_path):
    item=next(x for x in LEGACY if x['profile']=='history-import');path=tmp_path/'imports.sqlite3'
    old={**item,'tables':[s.replace(',target TEXT)',' )') for s in item['tables']]};legacy(path,old)
    with closing(connect(path,'history-import',write=True)) as db:assert db.execute('PRAGMA table_info(uploads)').fetchall()[-1][1]=='target'
    # Once enrolled, losing that column is damage, not another legacy migration.
    with sql(path) as db:db.execute('ALTER TABLE uploads DROP COLUMN target')
    with pytest.raises(JournalUnavailable):connect(path,'history-import',write=True)

@pytest.mark.parametrize('damage',['column','marker','extra','coordination-singleton'])
def test_invalid_current_shapes_do_not_become_healthy(tmp_path,damage):
    profile='coordination' if damage=='coordination-singleton' else 'bundle';path=tmp_path/'journal.sqlite3'
    with closing(connect(path,profile,write=True)):pass
    with sql(path) as db:
        if damage=='column':db.execute('ALTER TABLE commands DROP COLUMN body')
        elif damage=='marker':db.execute('PRAGMA user_version=17')
        elif damage=='extra':db.execute('CREATE TABLE unowned(value TEXT)')
        else:db.execute('DELETE FROM state WHERE id=1')
    before=bytes_hashes(path)
    with pytest.raises(JournalUnavailable):connect(path,profile,write=True)
    preserved(path,before)

@pytest.mark.skipif(os.name!='posix',reason='SIGKILL/WAL preservation requires POSIX')
def test_crashed_writer_missing_table_preserves_main_and_wal(tmp_path):
    path=tmp_path/'journal.sqlite3'
    with closing(connect(path,'bundle',write=True)) as db:
        db.execute("INSERT INTO commands VALUES('original','binding','scope','unknown')");db.commit()
    script="""import sqlite3,sys,time
p=sys.argv[1]
db=sqlite3.connect(p)
db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA wal_autocheckpoint=0')
db.execute('DROP TABLE commands');db.commit()
print('committed',flush=True)
time.sleep(60)
"""
    child=subprocess.Popen([sys.executable,'-I','-B','-c',script,str(path)],stdout=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='committed'
        child.kill();child.wait(timeout=5)
        before=bytes_hashes(path);assert '-wal' in before
        for write in (False,True):
            with pytest.raises(JournalUnavailable):connect(path,'bundle',write=write)
        preserved(path,before)
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=5)
        child.stdout.close()


def test_concurrent_first_writer_has_one_complete_schema(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path=tmp_path/'journal.sqlite3'
    def claim(i):
        with closing(connect(path,'inputs',write=True,enroll=bool(i%2))) as db:
            with db:db.execute('INSERT INTO provenance VALUES(?,?)',(str(i),'{}'))
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(claim,range(8)))
    with closing(connect(path,'inputs')) as db:
        assert db.execute('SELECT count(*) FROM provenance').fetchone()==(8,)
        assert db.execute('SELECT 1 FROM sqlite_schema WHERE name=\'outcomes\'').fetchone()


@pytest.mark.parametrize('profile,optional',[('inputs','outcomes'),('messages','quotes')])
def test_passive_read_joins_first_schema_commit_without_false_missing_receipt(tmp_path,monkeypatch,profile,optional):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from amplifier_acp import journal_schema as journal
    path=tmp_path/'journal.sqlite3';opened=Event();release=Event();observed=[]
    original_connect=sqlite3.connect;original_validate=journal._validate
    def paused(database,*args,**kwargs):
        db=original_connect(database,*args,**kwargs)
        if database==path:
            opened.set()
            assert release.wait(3)
        return db
    def inspect(db,family):
        try:return original_validate(db,family)
        except journal._InitializingJournal:
            observed.append(True);release.set();raise
    monkeypatch.setattr(sqlite3,'connect',paused)
    monkeypatch.setattr(journal,'_validate',inspect)
    def initialize():
        with closing(connect(path,profile,write=True,enroll=True)):pass
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(initialize)
        try:
            assert opened.wait(3)
            with closing(connect(path,profile,timeout=.25)) as db:
                assert db.execute('SELECT name FROM sqlite_schema WHERE name=?',(optional,)).fetchone()==(optional,)
            pending.result(timeout=3)
        finally:release.set()
    assert observed


def test_abandoned_empty_journal_stays_unknown_and_is_never_initialized_by_read(tmp_path):
    path=tmp_path/'journal.sqlite3';path.touch();before=path.read_bytes()
    with pytest.raises(JournalUnavailable):connect(path,'inputs',timeout=.02)
    assert path.read_bytes()==before
    assert not Path(str(path)+'.schema.lock').exists()


@pytest.mark.asyncio
async def test_original_unknown_bundle_never_reenters_after_journal_loss(tmp_path):
    from amplifier_acp.bundle_commands import BundleCommands,BundleCommandError
    owner=BundleCommands({'appHome':str(tmp_path/'app')},str(tmp_path),'inert-session')
    args={'commandId':'original','bundle':'fixture-only'};calls=[]
    async def lost(effect):
        effect();calls.append(1);raise RuntimeError('inert post-effect reply loss')
    for _ in range(2):
        with pytest.raises(BundleCommandError):await owner.perform('bundle.switch',args,lost)
    assert calls==[1];assert owner.receipt('original')['status']=='unknown'
    drop(owner.path,'commands');before=bytes_hashes(owner.path)
    with pytest.raises(JournalUnavailable):owner.receipt('original')
    with pytest.raises(JournalUnavailable):await owner.perform('bundle.switch',args,lost)
    assert calls==[1];preserved(owner.path,before)


def test_lazy_owner_constructors_and_lifecycle_lookup_refuse_damage(tmp_path):
    from amplifier_acp.maintenance import Maintenance
    from amplifier_acp.generations import Generations
    from amplifier_acp.session_lifecycle import Lifecycle
    config={'appHome':str(tmp_path/'app'),'home':str(tmp_path/'native')}
    maintenance=Maintenance(SimpleNamespace(config=config));assert maintenance.receipt(str(tmp_path),'original') is None
    maintenance.claim(str(tmp_path),'inert',{'commandId':'original','state':'running'})
    drop(maintenance.root/'receipts.sqlite3','commands')
    with pytest.raises(JournalUnavailable):Maintenance(SimpleNamespace(config=config))
    generation=Generations(tmp_path/'generation',{});assert generation.state()=={'phase':'idle'}
    generation.save({'commandId':'original','state':'unknown'},'inert');drop(generation.path,'state')
    with pytest.raises(JournalUnavailable):Generations(tmp_path/'generation',{})
    lifecycle=Lifecycle.__new__(Lifecycle);lifecycle.app=tmp_path/'lifecycle'
    assert lifecycle.saved('original') is None;assert not lifecycle.app.exists()
    lifecycle.save('original','inert',{'status':'running'});drop(lifecycle.app/'lifecycle-receipts.sqlite3','receipts')
    with pytest.raises(JournalUnavailable):lifecycle.saved('original')


def test_coordination_observation_cannot_recreate_original_unknown_command(tmp_path):
    from amplifier_acp.coordination import Store
    store=Store(tmp_path,'inert');store.save({'commandId':'original','workerId':'worker'},{'disposition':'unknown'})
    drop(store.path,'commands');before=bytes_hashes(store.path)
    with pytest.raises(JournalUnavailable):store.observe({'type':'job.queued','job_id':'unrelated'})
    preserved(store.path,before)


def test_shared_owner_readers_and_writers_honor_enrollment(tmp_path):
    from amplifier_acp.message_metadata import MessageMetadata,guard_input_identity,input_quote
    from amplifier_acp.input_outcome import Outcomes
    from amplifier_acp.native.input_provenance import reserve,reserved
    messages=MessageMetadata(tmp_path,'inert-session',actor_id='inert-actor')
    with closing(messages._db(True,quotes=True)) as db:
        db.execute("INSERT INTO quotes VALUES('original','binding','{}')")
    drop(messages.path,'quotes')
    for callback in (lambda:guard_input_identity(messages.path,'original'),lambda:input_quote(messages.path,'original'),lambda:messages.receipt('original'),lambda:messages._db(True)):
        with pytest.raises(JournalUnavailable):callback()
    outcomes=Outcomes(tmp_path,'inert-session',str(tmp_path))
    reserve(outcomes.path,'original',{'inputOrigin':'coordination'})
    with closing(outcomes.connect(True)) as db:pass
    drop(outcomes.path,'outcomes')
    for callback in (lambda:outcomes.read('original'),lambda:reserved(outcomes.path,'original'),lambda:reserve(outcomes.path,'next',{})):
        with pytest.raises(JournalUnavailable):callback()

@pytest.mark.parametrize('kind',['history-import','voice-preferences','permissions','managed-disposal','steering'])
def test_other_owner_write_initializers_preserve_missing_command_authority(tmp_path,kind):
    if kind=='managed-disposal':
        from amplifier_acp import managed_disposal
        owner=SimpleNamespace(app=tmp_path);path=tmp_path/'managed-files.sqlite3'
        def writer():
            with managed_disposal.db(owner):pass
    elif kind=='steering':
        from amplifier_acp.steering import Receipts
        owner=Receipts(tmp_path,'inert');path=owner.path
        def writer():
            with owner.db(True):pass
    else:
        if kind=='history-import':from amplifier_acp.history_import import HistoryImport as Owner
        elif kind=='voice-preferences':from amplifier_acp.voice_preferences import VoicePreferences as Owner
        else:from amplifier_acp.permissions import Permissions as Owner
        owner=Owner({'appHome':str(tmp_path/'app'),'home':str(tmp_path/'native')});path=owner.path
        def writer():owner.db(True).close()
    writer();drop(path,'receipts' if kind=='steering' else 'commands');before=bytes_hashes(path)
    with pytest.raises(JournalUnavailable):writer()
    preserved(path,before)


@pytest.mark.parametrize('suffix',['','-wal','-shm','-journal'])
@pytest.mark.parametrize('kind',['fifo','directory','hardlink'])
def test_actual_bundle_owner_nonregular_files_refuse_without_hang(tmp_path,suffix,kind):
    import amplifier_acp
    app=tmp_path/'app';path=app/'bundles/command-receipts.sqlite3';path.parent.mkdir(parents=True)
    if suffix:
        with closing(connect(path,'bundle',write=True)):pass
    target=Path(str(path)+suffix)
    if target.exists():target.unlink()
    if kind=='fifo':os.mkfifo(target)
    elif kind=='directory':target.mkdir()
    else:
        other=tmp_path/'retained';other.write_bytes(b'original');os.link(other,target)
    program="""import sys
sys.path.insert(0,sys.argv[1])
from amplifier_acp.bundle_commands import BundleCommands
owner=BundleCommands({'appHome':sys.argv[2]},sys.argv[3])
for call in (lambda:owner.receipt('original'),lambda:owner.claim('bundle.save',{'commandId':'original'})):
    try:call()
    except RuntimeError:pass
    else:raise AssertionError('nonregular authority accepted')
"""
    completed=subprocess.run([sys.executable,'-I','-B','-c',program,str(Path(amplifier_acp.__file__).parent.parent),str(app),str(tmp_path)],capture_output=True,text=True,timeout=3)
    assert completed.returncode==0,completed.stderr
    if kind=='hardlink':assert other.read_bytes()==b'original'


def test_nonregular_initializer_lock_refuses_without_touching_target(tmp_path):
    path=tmp_path/'journal.sqlite3';victim=tmp_path/'retained';victim.write_bytes(b'original')
    os.link(victim,Path(str(path)+'.schema.lock'))
    with pytest.raises(JournalUnavailable):connect(path,'bundle',write=True)
    assert victim.read_bytes()==b'original' and not path.exists()


@pytest.mark.parametrize('kind',['permissions','voice-preferences','history-import'])
@pytest.mark.parametrize('status',['failed','succeeded','unknown'])
def test_healthy_named_row_owners_recover_original_and_refuse_rebinding(tmp_path,monkeypatch,kind,status):
    import importlib
    module=importlib.import_module('amplifier_acp.'+kind.replace('-','_'))
    cls=getattr(module,{'permissions':'Permissions','voice-preferences':'VoicePreferences','history-import':'HistoryImport'}[kind])
    owner=cls.__new__(cls);owner.root=tmp_path/kind;owner.path=owner.root/'receipts.sqlite3'
    calls=[]
    def no_effect(*args,**kwargs):calls.append('unexpected effect');raise AssertionError('Settings/import effect forbidden')
    owner.store=SimpleNamespace(path=lambda *args:tmp_path/'absent-settings.yaml',read=lambda *args:{},update=no_effect)
    owner.begin=no_effect
    cwd=str(tmp_path.resolve());args={'commandId':'original'}
    if kind=='permissions':
        operation='permissions.save';args.update(scope='global',expectedRevision='0'*64,allowed=[],denied=[])
        signature=module.digest({'workspace':cwd,'operation':operation,'args':args})
    elif kind=='voice-preferences':
        operation='voice.preferences.update';args.update(expectedRevision='0'*64,patch={'interruptions':True})
        signature=hashlib.sha256(module.encoded({'operation':operation,'args':args})).hexdigest()
    else:
        operation='history.import.begin';args.update(format='json',bytes=2,sha256='0'*64)
        signature=module.digest(module.encoded({'cwd':cwd,'operation':operation,'args':args}))
    original={'commandId':'original','operation':operation,'status':status,'replayed':False}
    if status=='failed':original['executed']=False
    if status=='succeeded':original['result']={'fixture':'retained public result'}
    with closing(owner.db(True)) as db:
        body=json.dumps(original)
        values=('original',signature,cwd,'global',body) if kind=='permissions' else ('original',signature,body) if kind=='voice-preferences' else ('original',cwd,signature,body)
        db.execute('INSERT INTO commands VALUES('+','.join('?' for _ in values)+')',values)
    # Track actual SQLite ownership, including RO preflight and refused lookups.
    opened=[];real=sqlite3.connect
    class Tracked(sqlite3.Connection):
        was_closed=False
        def close(self):
            try:return super().close()
            finally:self.was_closed=True
    def tracking(database,*args,**kwargs):
        kwargs['factory']=Tracked;db=real(database,*args,**kwargs);opened.append(db);return db
    monkeypatch.setattr(sqlite3,'connect',tracking)
    assert owner.receipt(cwd,'original')==original
    assert owner.perform(cwd,operation,args)==original
    changed={**args,**({'allowed':['different']} if kind=='permissions' else {'patch':{'interruptions':False}} if kind=='voice-preferences' else {'title':'different'})}
    with pytest.raises(ValueError):owner.perform(cwd,operation,changed)
    assert owner.receipt(cwd,'original')==original
    if kind!='voice-preferences':
        with pytest.raises(ValueError):owner.receipt(str(tmp_path/'other'),'original')
    assert calls==[] and opened and all(db.was_closed for db in opened)


@pytest.mark.parametrize('damage',['coordination-revision','column-default','column-name','column-type','unique-index-name'])
def test_invalid_wide_metadata_is_bounded_before_python_materialization(tmp_path,monkeypatch,damage):
    profile='coordination' if damage=='coordination-revision' else 'bundle';path=tmp_path/'journal.sqlite3';wide='x'*(512*1024)
    if damage=='coordination-revision':
        with closing(connect(path,profile,write=True)):pass
        with sql(path) as db:db.execute('UPDATE state SET revision=? WHERE id=1',(wide,))
    else:
        with sql(path) as db:
            if damage=='column-default':db.execute("CREATE TABLE commands(id TEXT PRIMARY KEY,binding TEXT NOT NULL,scope TEXT NOT NULL,body TEXT NOT NULL DEFAULT '"+wide+"')")
            elif damage=='column-name':db.execute('CREATE TABLE commands(id TEXT PRIMARY KEY,binding TEXT NOT NULL,scope TEXT NOT NULL,"'+wide+'" TEXT NOT NULL)')
            elif damage=='column-type':db.execute('CREATE TABLE commands(id TEXT PRIMARY KEY,binding TEXT NOT NULL,scope TEXT NOT NULL,body "'+wide+'" NOT NULL)')
            else:
                db.execute('CREATE TABLE commands(id TEXT PRIMARY KEY,binding TEXT NOT NULL,scope TEXT NOT NULL,body TEXT NOT NULL)')
                db.execute('CREATE UNIQUE INDEX "'+wide+'" ON commands(scope)')
    real=sqlite3.connect;observed=[];opened=[]
    def record(row):
        if row is not None:
            for value in row:
                if isinstance(value,(str,bytes)):observed.append(len(value.encode() if isinstance(value,str) else value))
        return row
    class Cursor(sqlite3.Cursor):
        def fetchone(self):return record(super().fetchone())
        def fetchall(self):return [record(row) for row in super().fetchall()]
        def fetchmany(self,*args):return [record(row) for row in super().fetchmany(*args)]
    class Connection(sqlite3.Connection):
        was_closed=False
        def execute(self,statement,*args):return self.cursor(factory=Cursor).execute(statement,*args)
        def close(self):
            try:return super().close()
            finally:self.was_closed=True
    def tracking(database,*args,**kwargs):
        kwargs['factory']=Connection;db=real(database,*args,**kwargs);opened.append(db);return db
    monkeypatch.setattr(sqlite3,'connect',tracking)
    for write in (False,True):
        with pytest.raises(JournalUnavailable):connect(path,profile,write=write)
    assert observed and max(observed)<=128
    assert all(db.was_closed for db in opened)
