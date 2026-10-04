"""Actual Maintenance journal owners; inert records, never runtime/user effects."""
from types import SimpleNamespace
import pytest
from amplifier_acp.maintenance import Maintenance
from amplifier_acp.cache_maintenance import Caches
from amplifier_acp.app_reset import AppReset

@pytest.fixture
def owner(tmp_path):
    home=tmp_path/'home';home.mkdir()
    return Maintenance(SimpleNamespace(config={'home':str(home),'appHome':str(tmp_path/'app')}))

@pytest.mark.parametrize('constructor',[Caches,AppReset],ids=['cache','app-reset'])
def test_actual_owner_optional_constructor_preserves_original_unknown_receipt(owner,constructor):
    original={'commandId':'original','state':'unknown','operation':'fixture','replayed':False}
    owner.save('/owned-workspace','original-signature',original)
    constructor(owner)
    assert owner.receipt('/owned-workspace','original')==('original-signature',original)
    replacement=Maintenance(owner.admin)
    assert replacement.receipt('/owned-workspace','original')==('original-signature',original)
    assert not replacement.claim('/owned-workspace','original-signature',original)
    replacement.save('/owned-workspace','new-signature',{'commandId':'new','state':'refused'})
    assert replacement.receipt('/owned-workspace','original')==('original-signature',original)

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import sqlite3
import subprocess
import sys
from amplifier_acp.journal_schema import connect, JournalUnavailable

# Exact table DDL from the pre-fix Cache/AppReset constructors, independent of
# the validator's expected-shape cache. The old initializer published BASE first.
BASE=0x414A0200
DDL={
    'cache':(
        'CREATE TABLE cache_scans(id TEXT PRIMARY KEY,workspace TEXT NOT NULL,body TEXT NOT NULL)',
        'CREATE TABLE cache_entries(scan TEXT NOT NULL,id TEXT NOT NULL,body TEXT NOT NULL,PRIMARY KEY(scan,id))',
        'CREATE TABLE cache_previews(hash TEXT PRIMARY KEY,workspace TEXT NOT NULL,body TEXT NOT NULL)'),
    'app-reset':(
        'CREATE TABLE app_reset_revision(id INTEGER PRIMARY KEY,stamp TEXT,revision TEXT)',
        'CREATE TABLE app_reset_previews(id TEXT PRIMARY KEY,workspace TEXT,body TEXT,private TEXT)')}
BITS={'cache':1,'app-reset':2}
OWNERS={'cache':Caches,'app-reset':AppReset}
LAYOUTS=[(),('cache',),('app-reset',),('cache','app-reset')]

def path(owner):return owner.root/'receipts.sqlite3'
def marker(owner):
    with closing(sqlite3.connect(path(owner))) as db:return db.execute('PRAGMA user_version').fetchone()[0]
def hashes(owner):
    p=path(owner)
    return {suffix:hashlib.sha256(p.with_name(p.name+suffix).read_bytes()).hexdigest()
            for suffix in ('','-wal') if p.with_name(p.name+suffix).exists()}
def seed(owner):
    row={'commandId':'original','state':'unknown','operation':'fixture','replayed':False}
    owner.save('/owned','signature',row)
    return ('signature',row)

@pytest.mark.parametrize('names',LAYOUTS+[('app-reset','cache')])
def test_actual_owner_combinations_preserve_all_enrolled_namespaces(owner,names):
    original=seed(owner)
    for name in names:OWNERS[name](owner)
    assert marker(owner)==BASE|sum(BITS[n] for n in set(names))
    restarted=Maintenance(owner.admin)
    for name in reversed(names):OWNERS[name](restarted)
    assert restarted.receipt('/owned','original')==original
    assert marker(owner)==BASE|sum(BITS[n] for n in set(names))

@pytest.mark.parametrize('names',LAYOUTS)
@pytest.mark.parametrize('old_marker',[0,BASE],ids=['unversioned','legacy-base-marker'])
def test_complete_legacy_groups_remain_passive_then_enroll_on_write(owner,names,old_marker):
    original=seed(owner)
    with closing(sqlite3.connect(path(owner))) as db:
        for name in names:
            for sql in DDL[name]:db.execute(sql)
        db.execute(f'PRAGMA user_version={old_marker}');db.commit()
    restarted=Maintenance(owner.admin)
    assert restarted.receipt('/owned','original')==original
    assert marker(owner)==old_marker
    with restarted.db():pass
    assert marker(owner)==BASE|sum(BITS[n] for n in names)
    assert restarted.receipt('/owned','original')==original

@pytest.mark.parametrize('names',LAYOUTS[1:])
def test_missing_any_enrolled_optional_table_refuses_before_writable_open(owner,names,monkeypatch):
    seed(owner)
    for name in names:OWNERS[name](owner)
    # One separate fixture per table avoids reconstructing an authority database.
    for name in names:
        for sql in DDL[name]:
            table=sql.split('(',1)[0].split()[-1]
            copy=owner.root/(table+'.sqlite3')
            with closing(sqlite3.connect(path(owner))) as source,closing(sqlite3.connect(copy)) as dest:source.backup(dest)
            with closing(sqlite3.connect(copy)) as db:db.execute('DROP TABLE '+table);db.commit()
            before=copy.read_bytes();real=sqlite3.connect;opens=[]
            def checked_connect(target,*args,**kwargs):
                opens.append(str(target));assert 'mode=ro' in str(target)
                return real(target,*args,**kwargs)
            with monkeypatch.context() as m:
                m.setattr(sqlite3,'connect',checked_connect)
                with pytest.raises(JournalUnavailable):connect(copy,'maintenance',write=True,namespace=name)
            assert opens and copy.read_bytes()==before

@pytest.mark.parametrize('name',DDL)
@pytest.mark.parametrize('old_marker',[0,BASE])
def test_legacy_partial_group_is_never_reconstructed(owner,name,old_marker):
    seed(owner)
    with closing(sqlite3.connect(path(owner))) as db:
        db.execute(DDL[name][0]);db.execute(f'PRAGMA user_version={old_marker}');db.commit()
    before=hashes(owner)
    with pytest.raises(JournalUnavailable):OWNERS[name](owner)
    assert all(hashes(owner).get(k)==v for k,v in before.items())

@pytest.mark.parametrize('name',DDL)
def test_complete_but_malformed_legacy_namespace_refuses(owner,name):
    seed(owner)
    with closing(sqlite3.connect(path(owner))) as db:
        for sql in DDL[name]:db.execute(sql.replace('body TEXT','body BLOB'))
        db.commit()
    before=hashes(owner)
    with pytest.raises(JournalUnavailable):Maintenance(owner.admin)
    assert all(hashes(owner).get(k)==v for k,v in before.items())

@pytest.mark.parametrize('name',DDL)
def test_namespace_publication_rolls_back_tables_and_marker_together(owner,name,monkeypatch):
    original=seed(owner);real=sqlite3.connect
    denied=DDL[name][-1].split('(',1)[0].split()[-1]
    def failing_connect(target,*args,**kwargs):
        db=real(target,*args,**kwargs)
        if 'mode=ro' not in str(target):
            db.set_authorizer(lambda action,table,*rest: sqlite3.SQLITE_DENY
                              if action==sqlite3.SQLITE_CREATE_TABLE and table==denied else sqlite3.SQLITE_OK)
        return db
    with monkeypatch.context() as m:
        m.setattr(sqlite3,'connect',failing_connect)
        with pytest.raises(sqlite3.DatabaseError):OWNERS[name](owner)
    assert marker(owner)==BASE
    assert Maintenance(owner.admin).receipt('/owned','original')==original
    with closing(sqlite3.connect(path(owner))) as db:
        assert db.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()==[('commands',)]
    OWNERS[name](owner)
    assert marker(owner)==BASE|BITS[name]


def test_cooperating_constructors_do_not_lose_other_namespace_enrollment(owner):
    original=seed(owner)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda ctor:ctor(Maintenance(owner.admin)),[Caches,AppReset]))
    assert marker(owner)==BASE|3
    assert Maintenance(owner.admin).receipt('/owned','original')==original


def test_missing_optional_table_in_crash_left_wal_preserves_original_evidence(owner):
    seed(owner);Caches(owner);AppReset(owner)
    code="import sqlite3,os,sys; db=sqlite3.connect(sys.argv[1]); db.executescript('PRAGMA journal_mode=WAL;PRAGMA wal_autocheckpoint=0;DROP TABLE app_reset_previews');os._exit(0)"
    subprocess.run([sys.executable,'-I','-B','-c',code,str(path(owner))],check=True)
    before=hashes(owner);assert '-wal' in before
    with pytest.raises(JournalUnavailable):Maintenance(owner.admin)
    assert all(hashes(owner).get(k)==v for k,v in before.items())


def test_unknown_namespace_or_foreign_family_does_not_gain_authority(owner):
    with pytest.raises(ValueError):connect(path(owner),'maintenance',write=True,namespace='unknown')
    assert not path(owner).exists()
    with pytest.raises(ValueError):connect(path(owner),'bundle',write=True,namespace='cache')
    assert not path(owner).exists()
    seed(owner);Caches(owner)
    with pytest.raises(JournalUnavailable):connect(path(owner),'bundle',write=True)
