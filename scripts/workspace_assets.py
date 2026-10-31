"""Resolve or reconstruct archived files by source identity and verified bytes.

The local catalog is deliberately external to Git. No basename search, mutable
historical manifests, source imports, environment activation, or source deletion.
"""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat


def native(path):
    value=os.path.abspath(path)
    if os.name!='nt' or value.startswith('\\\\?\\'):return value
    return '\\\\?\\UNC\\'+value[2:] if value.startswith('\\\\') else '\\\\?\\'+value


def sha256(path):
    result=hashlib.sha256()
    with open(native(path),'rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):result.update(block)
    return result.hexdigest()


def stream_hashes(path):
    if os.name!='nt':return []
    class Stream(ctypes.Structure):
        _fields_=[('size',ctypes.c_longlong),('name',ctypes.c_wchar*296)]
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.FindFirstStreamW.argtypes=[ctypes.c_wchar_p,ctypes.c_int,ctypes.POINTER(Stream),ctypes.c_int];api.FindFirstStreamW.restype=ctypes.c_void_p
    api.FindNextStreamW.argtypes=[ctypes.c_void_p,ctypes.POINTER(Stream)];api.FindClose.argtypes=[ctypes.c_void_p]
    item=Stream();handle=api.FindFirstStreamW(native(path),0,ctypes.byref(item),0)
    if handle==ctypes.c_void_p(-1).value:
        error=ctypes.get_last_error()
        if error==38:return []
        raise ctypes.WinError(error)
    found=[]
    try:
        while True:
            if item.name!='::$DATA':found.append({'name':item.name,'size':item.size,'sha256':sha256(str(path)+item.name)})
            if not api.FindNextStreamW(handle,ctypes.byref(item)):
                error=ctypes.get_last_error()
                if error not in (0,38):raise ctypes.WinError(error)
                break
    finally:api.FindClose(handle)
    return sorted(found,key=lambda x:x['name'])


def safe_relative(value):
    value=value.replace('\\','/')
    path=PurePosixPath(value)
    if not value or path.is_absolute() or '..' in path.parts or ':' in value or path.as_posix()!=value:
        raise ValueError('Use the exact source-relative catalog path')
    return value


def checked_target(row,expected_sha256=None):
    if row['action']=='BLOCKED' or not row['canonical_target'] or not row['sha256']:
        raise ValueError('Asset is not verified/mapped')
    if expected_sha256 is not None and expected_sha256!=row['sha256']:
        raise ValueError('Requested historical version does not match this source identity')
    path=Path(row['canonical_target'])
    if not path.is_absolute():raise ValueError('Catalog canonical path must be absolute')
    for parent in (path,*path.parents):
        info=os.stat(native(parent),follow_symlinks=False)
        if getattr(info,'st_file_attributes',0)&0x400 or stat.S_ISLNK(info.st_mode):
            raise ValueError('Reparse point in canonical path')
    if os.stat(native(path)).st_size!=row['size_bytes'] or sha256(path)!=row['sha256']:
        raise ValueError('Canonical bytes differ from catalog')
    expected_streams=json.loads(row['streams_json']) if 'streams_json' in row.keys() else []
    if any('error' in stream for stream in expected_streams) or stream_hashes(path)!=sorted(expected_streams,key=lambda x:x['name']):
        raise ValueError('Canonical named streams differ from catalog')
    return path


def connect(catalog):
    connection=sqlite3.connect(Path(catalog).resolve().as_uri()+'?mode=ro',uri=True)
    connection.row_factory=sqlite3.Row
    return connection


def resolve_asset(catalog,source,path,expected_sha256=None):
    path=safe_relative(path)
    with connect(catalog) as connection:
        rows=connection.execute('SELECT * FROM files WHERE source_machine=? AND relative_path=? AND kind=?',(source,path,'file')).fetchall()
    if len(rows)!=1:raise LookupError('Exact source/path identity absent or ambiguous')
    return checked_target(rows[0],expected_sha256)


def restore_tree(catalog,source,prefix,destination):
    """Restore indexed regular files and relative names; no environment activation."""
    prefix=safe_relative(prefix).rstrip('/')
    destination=Path(os.path.abspath(destination))
    if os.path.lexists(native(destination)):raise FileExistsError('Restore destination must not exist')
    for ancestor in destination.parents:
        if os.path.lexists(native(ancestor)) and getattr(os.stat(native(ancestor),follow_symlinks=False),'st_file_attributes',0)&0x400:
            raise ValueError('Restore parent contains a reparse point')
    with connect(catalog) as connection:
        rows=connection.execute("SELECT * FROM files WHERE source_machine=? AND kind='file' AND substr(relative_path,1,?)=? ORDER BY relative_path",(source,len(prefix)+1,prefix+'/')).fetchall()
    if not rows:raise LookupError('No files for exact source/prefix')
    # Validate all source mappings before creating an output directory.
    verified=[(row,checked_target(row)) for row in rows]
    os.makedirs(native(destination),exist_ok=False)
    receipt=[]
    for row,canonical in verified:
        rel=safe_relative(row['relative_path'][len(prefix)+1:])
        target=destination/rel
        if not target.is_relative_to(destination):raise ValueError('Restore escape')
        os.makedirs(native(target.parent),exist_ok=True)
        if os.name=='nt':
            api=ctypes.WinDLL('kernel32',use_last_error=True)
            api.CopyFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_wchar_p,ctypes.c_int]
            if not api.CopyFileW(native(canonical),native(target),1):raise ctypes.WinError(ctypes.get_last_error())
        else:
            with open(canonical,'rb') as src,open(target,'xb') as dst:shutil.copyfileobj(src,dst)
            shutil.copystat(canonical,target)
        if sha256(target)!=row['sha256']:raise ValueError('Restored bytes differ')
        expected_streams=json.loads(row['streams_json']) if 'streams_json' in row.keys() else []
        if stream_hashes(target)!=sorted(expected_streams,key=lambda x:x['name']):raise ValueError('Restored named streams differ')
        expected_mtime=row['mtime_ns'] if 'mtime_ns' in row.keys() else None
        if expected_mtime is not None:
            current=os.stat(native(target));os.utime(native(target),ns=(current.st_atime_ns,expected_mtime))
            if os.stat(native(target)).st_mtime_ns!=expected_mtime:raise ValueError('Restored mtime differs')
        receipt.append({'source_id':row['source_id'],'relative_path':rel,'sha256':row['sha256'],'streams':expected_streams,'mtime_ns':expected_mtime})
    receipt_path=destination.with_name(destination.name+'.restore-receipt.json')
    with open(native(receipt_path),'x',encoding='utf-8') as stream:
        json.dump({'source':source,'prefix':prefix,'files':receipt,'independent_backup':False,'scope':'indexed regular files, main stream, named streams and source mtime','not_restored':['empty directories','ACL','creation time','links']},stream,ensure_ascii=False,indent=2)
    return len(receipt)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog',type=Path,required=True)
    parser.add_argument('--source',choices=['PC1','PC2','PC3'],required=True)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--path');group.add_argument('--restore-prefix')
    parser.add_argument('--sha256');parser.add_argument('--destination',type=Path)
    args=parser.parse_args()
    if args.path:
        if args.destination:parser.error('--destination requires --restore-prefix')
        print(resolve_asset(args.catalog,args.source,args.path,args.sha256))
    else:
        if not args.destination:parser.error('--restore-prefix requires --destination')
        print(json.dumps({'restored_files':restore_tree(args.catalog,args.source,args.restore_prefix,args.destination)}))


if __name__=='__main__':main()
