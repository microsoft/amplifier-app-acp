"""Small owner-image fixtures; protocol coverage lives in store-snapshot tests."""
from contextlib import closing
import os
import sqlite3
from types import SimpleNamespace

from filelock import FileLock, Timeout
import pytest

from amplifier_acp.maintenance import Maintenance
from amplifier_acp.maintenance_gate import Gate, MaintenanceBusy
from amplifier_acp.private_store_snapshot import prepare
from amplifier_acp.cache_maintenance import Caches
from amplifier_acp.app_reset import AppReset


@pytest.fixture
def owner(tmp_path):
    home=tmp_path/'home';home.mkdir()
    # Synthetic admin identity only; the full public protocol tests separately
    # establish held recovery admission. The native-home gate here is real.
    fence={'purpose':'recovery','fenceId':'fixture','commandId':'fixture',
           'leaseId':'fixture','adminInstanceId':'fixture'}
    admin=SimpleNamespace(config={'home':str(home),'appHome':str(tmp_path/'app')},
                          lifecycle=SimpleNamespace(fence=fence))
    value=Maintenance(admin)
    value.save('/owned','original-binding',{'commandId':'original','state':'unknown'})
    (value.archives().root/('a'*32)).mkdir()
    return value


def snapshot(owner):
    with owner.archive_gate(exclusive=True):
        return prepare(owner,'a'*32,['native-maintenance-records'],owner.ensure_open,'fixture-prepare')


def test_snapshot_excludes_exact_coordination_lock_without_releasing_or_losing_authority(owner,tmp_path):
    Caches(owner);AppReset(owner)
    with owner.db() as db:
        db.execute('INSERT INTO cache_scans VALUES(?,?,?)',('scan','/owned','{"retained":true}'))
        db.execute('INSERT INTO app_reset_previews VALUES(?,?,?,?)',('review','/owned','{}','{"private":"fixture"}'))
    source=owner.root/'receipts.sqlite3';original=source.read_bytes()
    lock=owner.root/'receipts.sqlite3.schema.lock';inode=lock.stat().st_ino
    canonical=owner.home/'transcript.jsonl';canonical.write_bytes(b'untouched canonical fixture\n')
    with FileLock(lock,timeout=0):
        with owner.archive_gate(exclusive=True):
            with pytest.raises(MaintenanceBusy):Gate(owner.config)
            rows=prepare(owner,'a'*32,['native-maintenance-records'],owner.ensure_open,'fixture-prepare')
            with pytest.raises(Timeout):FileLock(lock,timeout=0).acquire()
        assert lock.stat().st_ino==inode
    assert rows[0]['databaseImages']==1
    destination=owner.root/'plans'/('a'*32)/'native-stores/native-maintenance-records'
    assert sorted(p.name for p in destination.iterdir())==['receipts.sqlite3']
    with closing(sqlite3.connect(destination/'receipts.sqlite3')) as db:
        assert db.execute('SELECT body FROM commands WHERE id="original"').fetchone()==('{"commandId": "original", "state": "unknown"}',)
        assert db.execute('SELECT body FROM cache_scans').fetchone()==('{"retained":true}',)
        assert db.execute('SELECT private FROM app_reset_previews').fetchone()==('{"private":"fixture"}',)
    assert source.read_bytes()==original
    assert owner.receipt('/owned','original')[1]['state']=='unknown'
    assert canonical.read_bytes()==b'untouched canonical fixture\n'
    with Gate(owner.config):pass
    with FileLock(lock,timeout=0):pass


@pytest.mark.parametrize('kind',['directory','fifo','symlink','hardlink'])
def test_named_schema_lock_must_still_be_safe_regular_coordination_file(owner,tmp_path,kind):
    lock=owner.root/'receipts.sqlite3.schema.lock';lock.unlink()
    target=tmp_path/'retained-target';target.write_bytes(b'retained')
    if kind=='directory':lock.mkdir()
    elif kind=='fifo':os.mkfifo(lock)
    elif kind=='symlink':lock.symlink_to(target)
    else:os.link(target,lock)
    original=(owner.root/'receipts.sqlite3').read_bytes()
    with pytest.raises(ValueError):snapshot(owner)
    assert (owner.root/'receipts.sqlite3').read_bytes()==original
    assert target.read_bytes()==b'retained' and lock.lstat()


def test_other_lock_named_root_entry_is_not_silently_excluded(owner):
    unknown=owner.root/'unknown.schema.lock';unknown.write_bytes(b'unclassified authority')
    with pytest.raises(ValueError,match='Unknown maintenance-owner entry'):snapshot(owner)
    assert unknown.read_bytes()==b'unclassified authority'
