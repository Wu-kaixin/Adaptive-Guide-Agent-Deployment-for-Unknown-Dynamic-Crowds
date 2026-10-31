import hashlib
import os
from pathlib import Path
import runpy
import sqlite3

import pytest

api=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/workspace_assets.py'))


@pytest.fixture
def catalog(tmp_path):
    canonical=tmp_path/'canonical';canonical.mkdir()
    data=canonical/'trajectory.bin';data.write_bytes(b'frozen evidence')
    digest=hashlib.sha256(data.read_bytes()).hexdigest()
    path=tmp_path/'catalog.sqlite'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE files(source_id TEXT,source_machine TEXT,relative_path TEXT,kind TEXT,action TEXT,canonical_target TEXT,sha256 TEXT,size_bytes INTEGER,streams_json TEXT,mtime_ns INTEGER)')
        connection.execute('INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?,?)',('one','PC2','study/trajectory.bin','file','KEEP_EVIDENCE',str(data),digest,data.stat().st_size,'[]',1700000000000000000))
    return path,data,digest


def test_resolves_without_original_source(catalog):
    db,data,digest=catalog
    assert api['resolve_asset'](db,'PC2','study/trajectory.bin',digest)==data
    with pytest.raises(LookupError):api['resolve_asset'](db,'PC3','study/trajectory.bin')
    with pytest.raises(LookupError):api['resolve_asset'](db,'PC2','trajectory.bin')


def test_refuses_wrong_version_and_changed_bytes(catalog):
    db,data,_=catalog
    with pytest.raises(ValueError):api['resolve_asset'](db,'PC2','study/trajectory.bin','0'*64)
    data.write_bytes(b'changed bytes')
    with pytest.raises(ValueError):api['resolve_asset'](db,'PC2','study/trajectory.bin')


def test_restore_preserves_relative_names_without_overwrite(catalog,tmp_path):
    db,data,_=catalog;target=tmp_path/'restored'
    assert api['restore_tree'](db,'PC2','study',target)==1
    assert (target/'trajectory.bin').read_bytes()==data.read_bytes()
    assert (target/'trajectory.bin').stat().st_mtime_ns==1700000000000000000
    with pytest.raises(FileExistsError):api['restore_tree'](db,'PC2','study',target)


def test_rejects_escape_and_blocked_mapping(catalog):
    db,_,_=catalog
    with pytest.raises(ValueError):api['resolve_asset'](db,'PC2','../study/trajectory.bin')
    with sqlite3.connect(db) as connection:connection.execute("UPDATE files SET action='BLOCKED'")
    with pytest.raises(ValueError):api['resolve_asset'](db,'PC2','study/trajectory.bin')


@pytest.mark.skipif(os.name!='nt',reason='Windows named streams')
def test_unexpected_named_stream_is_detected(catalog):
    db,data,_=catalog
    Path(str(data)+':unexpected').write_bytes(b'additional evidence')
    with pytest.raises(ValueError):api['resolve_asset'](db,'PC2','study/trajectory.bin')
