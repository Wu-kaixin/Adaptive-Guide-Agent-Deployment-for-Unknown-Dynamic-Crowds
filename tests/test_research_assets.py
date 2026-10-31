"""Safety and byte-preservation checks for project-local archival restores."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import zipfile

import pytest

from udc.assets import Catalogue, native, relative_name, stream_member, verify_archive


@pytest.fixture
def preserved(tmp_path):
    root=tmp_path/'data';root.mkdir()
    payload=b'original research\x00bytes'
    sha=hashlib.sha256(payload).hexdigest()
    archive=root/'one.zip'
    with zipfile.ZipFile(archive,'w') as z:z.writestr('PC2/Unicode 空格/result.bin',payload)
    row={'asset_id':1,'member':'PC2/Unicode 空格/result.bin','sha256':sha,'size_bytes':len(payload),'streams_json':'[]'}
    db=sqlite3.connect(root/'catalog.sqlite')
    db.executescript('''CREATE TABLE metadata(key TEXT,value TEXT);INSERT INTO metadata VALUES('schema','1');
    CREATE TABLE archives(archive_id INTEGER,path TEXT,state TEXT);
    INSERT INTO archives VALUES(1,'one.zip','VERIFIED');
    CREATE TABLE assets(asset_id INTEGER,member TEXT,streams_json TEXT,archive_id INTEGER);
    CREATE TABLE sources(source_id TEXT,machine TEXT,relative_path TEXT,kind TEXT,size_bytes INTEGER,mtime_ns INTEGER,sha256 TEXT,category TEXT,action TEXT,asset_id INTEGER);
    ''')
    db.execute('INSERT INTO assets VALUES(?,?,?,?)',(1,row['member'],'[]',1))
    for sid,machine in [('one','PC2'),('two','PC3')]:
        db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?,?)',(sid,machine,'batch/result.bin','file',len(payload),1700000000000000000,sha,'evidence','KEEP_EVIDENCE',1))
    db.execute("INSERT INTO sources(source_id,machine,relative_path,kind,mtime_ns) VALUES('empty','PC2','batch/empty','directory',1700000000000000000)")
    db.commit();db.close()
    return root,archive,row,payload


def test_restore_preserves_two_identities_and_bytes(preserved,tmp_path):
    root,archive,row,payload=preserved
    assert verify_archive(archive,[row])['members']==1
    c=Catalogue(root/'catalog.sqlite')
    try:
        assert len(c.find('result.bin'))==2
        for sid in ('one','two'):
            destination=tmp_path/sid
            c.restore_file(sid,destination)
            assert destination.read_bytes()==payload
            assert destination.stat().st_mtime_ns==1700000000000000000
        with pytest.raises(FileExistsError):c.restore_file('one',tmp_path/'one')
    finally:c.close()


def test_restore_empty_directory_and_identity_prefix(preserved,tmp_path):
    root,_,_,payload=preserved;c=Catalogue(root/'catalog.sqlite')
    try:
        receipt=c.restore_tree('PC2','batch',tmp_path/'restored')
        assert receipt['files']==1 and receipt['directories']==1
        assert (tmp_path/'restored/batch/empty').is_dir()
        assert (tmp_path/'restored/batch/result.bin').read_bytes()==payload
        with pytest.raises(ValueError):c.restore_tree('PC2','bat',tmp_path/'nomatch')
    finally:c.close()


def test_restore_long_paths_including_empty_directories(preserved,tmp_path):
    root,_,_,payload=preserved
    nested='/'.join(['original-metadata-segment-0123456789']*8)
    with sqlite3.connect(root/'catalog.sqlite') as db:
        db.execute("UPDATE sources SET relative_path=? WHERE source_id='one'",('batch/'+nested+'/result.bin',))
        db.execute("UPDATE sources SET relative_path=? WHERE source_id='empty'",('batch/'+nested+'/empty',))
    c=Catalogue(root/'catalog.sqlite')
    try:
        destination=tmp_path/'long-tree'
        receipt=c.restore_tree('PC2','batch',destination)
        target=destination/'batch'/nested/'result.bin'
        assert len(str(target))>300 and receipt['files']==1
        assert Path(native(target)).read_bytes()==payload
        assert Path(native(target.parent/'empty')).is_dir()
        assert Path(native(target)).stat().st_mtime_ns==1700000000000000000
        direct=tmp_path/'long-file'/nested/'result.bin'
        c.restore_file('one',direct)
        assert Path(native(direct)).read_bytes()==payload
    finally:c.close()


@pytest.mark.parametrize('path',['../escape','C:/escape','a/../b','a\\b','/absolute','a//b','a/./b','a/file:stream','NUL','a/trailing.'])
def test_reject_escaping_or_ambiguous_relative_names(path):
    with pytest.raises(ValueError):relative_name(path)


def test_tamper_truncation_and_duplicate_members_rejected(preserved,tmp_path):
    root,archive,row,payload=preserved
    with zipfile.ZipFile(archive,'w') as z:z.writestr(row['member'],b'x'*len(payload))
    with pytest.raises(ValueError,match='SHA'):verify_archive(archive,[row])
    c=Catalogue(root/'catalog.sqlite')
    try:
        with pytest.raises(ValueError,match='hash'):c.restore_file('one',tmp_path/'bad')
        assert not (tmp_path/'bad').exists()
        assert (tmp_path/'bad.asset-restore-partial').exists()
    finally:c.close()
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr(row['member'],payload)
        with pytest.warns(UserWarning):z.writestr(row['member'],payload)
    with pytest.raises(ValueError,match='member set'):verify_archive(archive,[row])
    archive.write_bytes(archive.read_bytes()[:15])
    with pytest.raises(zipfile.BadZipFile):verify_archive(archive,[row])


def test_incomplete_archive_never_restored(preserved,tmp_path):
    root,_,_,_=preserved
    with sqlite3.connect(root/'catalog.sqlite') as db:db.execute("UPDATE archives SET state='WRITING'")
    c=Catalogue(root/'catalog.sqlite')
    try:
        with pytest.raises(ValueError,match='verified'):c.restore_file('one',tmp_path/'bad')
        assert not (tmp_path/'bad').exists()
    finally:c.close()


@pytest.mark.skipif(os.name!='nt',reason='NTFS named-stream preservation')
def test_named_stream_bytes_restored(preserved,tmp_path):
    root,archive,row,_=preserved
    name=':Zone.Identifier:$DATA';payload=b'[ZoneTransfer]\r\nZoneId=3\r\n'
    ads={'name':name,'size':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}
    with zipfile.ZipFile(archive,'a') as z:z.writestr(stream_member(1,name),payload)
    row['streams_json']=json.dumps([ads])
    with sqlite3.connect(root/'catalog.sqlite') as db:db.execute('UPDATE assets SET streams_json=?',(row['streams_json'],))
    assert verify_archive(archive,[row])['members']==2
    c=Catalogue(root/'catalog.sqlite')
    try:
        c.restore_file('one',tmp_path/'with-stream')
        with open(str(tmp_path/'with-stream')+name,'rb') as stream:assert stream.read()==payload
    finally:c.close()


def test_symlink_parent_is_not_followed(preserved,tmp_path):
    root,_,_,_=preserved;outside=tmp_path/'outside';outside.mkdir();link=tmp_path/'link'
    try:link.symlink_to(outside,target_is_directory=True)
    except OSError:pytest.skip('Host does not grant symlink creation')
    c=Catalogue(root/'catalog.sqlite')
    try:
        with pytest.raises(ValueError,match='Reparse/symlink'):c.restore_file('one',link/'new')
        assert not (outside/'new').exists()
    finally:c.close()
