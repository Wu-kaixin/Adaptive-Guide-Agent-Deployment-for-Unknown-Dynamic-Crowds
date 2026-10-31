"""Read-only catalogue and explicit restores of preserved research assets.

This module does not participate in simulation or evaluation. Archives are
ordinary ZIP64 files with original relative names, not an executable format.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import sys
import zipfile


def native(path):
    value = os.path.abspath(path)
    if os.name == 'nt' and not value.startswith('\\\\?\\'):
        return '\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value
    return value


def relative_name(value):
    """Reject ambiguous/escaping names on Windows and POSIX."""
    p = PurePosixPath(value)
    if not value or '\\' in value or p.is_absolute() or any(
        part in ('', '.', '..') or ':' in part or part.endswith((' ', '.'))
        or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part)
        for part in value.split('/')
    ):
        raise ValueError(f'Unsafe relative path: {value!r}')
    return p


def guarded(root, path, *, must_exist=False):
    root = Path(os.path.abspath(root))
    path = Path(os.path.abspath(path))
    if not path.is_relative_to(root):
        raise ValueError('Path escapes allowed root')
    # Include ancestors of root: a junction above the project is also relevant.
    for item in reversed((path, *path.parents)):
        try:
            info = os.stat(native(item), follow_symlinks=False)
        except FileNotFoundError:
            if must_exist:
                raise
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError(f'Reparse/symlink path is not allowed: {item}')
    return path


def digest_file(path):
    h = hashlib.sha256()
    with open(native(path), 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def stream_member(asset_id, name):
    if not re.fullmatch(r':[^:/\\]+:\$DATA', name):
        raise ValueError('Unsupported NTFS stream name')
    return f'__ntfs_streams__/{asset_id}/{name.encode("utf-8").hex()}.bin'


def expected_members(rows):
    expected = {}
    for row in rows:
        name = row['member']
        relative_name(name)
        if name in expected:
            raise ValueError('Duplicate catalogue member')
        expected[name] = (row['sha256'], row['size_bytes'])
        for ads in json.loads(row['streams_json']):
            extra = stream_member(row['asset_id'], ads['name'])
            if extra in expected:
                raise ValueError('Duplicate stream member')
            expected[extra] = (ads['sha256'], ads['size'])
    return expected


def verify_archive(path, rows):
    """Independently re-read every byte and reject extra/missing/duplicate members."""
    expected = expected_members(rows)
    read_bytes = 0
    with zipfile.ZipFile(native(path)) as archive:
        members = archive.infolist()
        if len(members) != len(expected) or {m.filename for m in members} != set(expected):
            raise ValueError('Archive member set differs from catalogue')
        for member in members:
            expected_sha, expected_size = expected[member.filename]
            if member.file_size != expected_size or member.flag_bits & 1 or member.is_dir():
                raise ValueError('Archive member size/type differs')
            if stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError('Archive symlink is not allowed')
            h = hashlib.sha256()
            size = 0
            with archive.open(member) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    size += len(chunk)
                    h.update(chunk)
            if size != expected_size or h.hexdigest() != expected_sha:
                raise ValueError(f'Archive SHA-256 differs: {member.filename}')
            read_bytes += size
    return {'members': len(expected), 'payload_bytes': read_bytes,
            'sha256': digest_file(path), 'archive_bytes': os.stat(native(path)).st_size}


class Catalogue:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.root = self.path.parent
        self.db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)
        self.db.row_factory = sqlite3.Row
        if self.db.execute("SELECT value FROM metadata WHERE key='schema'").fetchone()[0] != '1':
            raise ValueError('Unsupported asset catalogue')

    def close(self):
        self.db.close()

    def source(self, source_id):
        row = self.db.execute('SELECT s.*,a.member,a.streams_json,a.archive_id,r.path AS archive_path,r.state AS archive_state FROM sources s LEFT JOIN assets a USING(asset_id) LEFT JOIN archives r USING(archive_id) WHERE source_id=?', (source_id,)).fetchone()
        if row is None:
            raise KeyError(source_id)
        return row

    def find(self, text, machine=None, limit=30):
        return [dict(r) for r in self.db.execute('SELECT source_id,machine,relative_path,kind,category,action,sha256 FROM sources WHERE instr(lower(relative_path),lower(?))>0 AND (? IS NULL OR machine=?) ORDER BY machine,relative_path LIMIT ?', (text,machine,machine,limit))]

    def restore_file(self, source_id, destination):
        row = self.source(source_id)
        if row['kind'] != 'file' or row['archive_state'] != 'VERIFIED':
            raise ValueError('Source is not in a verified file archive')
        destination = Path(os.path.abspath(destination))
        guarded(destination.parent, destination)
        if os.path.lexists(native(destination)):
            raise FileExistsError('Restore never overwrites an existing path')
        archive_path = self.root / relative_name(row['archive_path'])
        guarded(self.root, archive_path, must_exist=True)
        streams = json.loads(row['streams_json'])
        if streams and os.name != 'nt':
            raise ValueError('Use Windows to restore NTFS alternate streams exactly')
        os.makedirs(native(destination.parent), exist_ok=True)
        temporary = destination.with_name(destination.name + '.asset-restore-partial')
        guarded(destination.parent, temporary)
        if os.path.lexists(native(temporary)):
            raise FileExistsError('An unfinished restore exists; inspect it first')
        with zipfile.ZipFile(native(archive_path)) as archive:
            # A duplicate name can hide different bytes; never let getinfo choose it.
            names = [entry.filename for entry in archive.infolist()]
            for member in [row['member']] + [stream_member(row['asset_id'], a['name']) for a in streams]:
                if names.count(member) != 1:
                    raise ValueError('Archive member missing or ambiguous')
            def copy_member(member, target, expected_sha, expected_size, mode):
                info = archive.getinfo(member)
                if info.file_size != expected_size or info.flag_bits & 1 or stat.S_ISLNK(info.external_attr >> 16):
                    raise ValueError('Archive member metadata mismatch')
                h = hashlib.sha256(); size = 0
                with archive.open(info) as source, open(native(target), mode) as output:
                    for chunk in iter(lambda: source.read(1024 * 1024), b''):
                        size += len(chunk); h.update(chunk); output.write(chunk)
                    output.flush(); os.fsync(output.fileno())
                if size != expected_size or h.hexdigest() != expected_sha:
                    raise ValueError('Restored content hash mismatch; partial retained')
            copy_member(row['member'], temporary, row['sha256'], row['size_bytes'], 'xb')
            for ads in streams:
                copy_member(stream_member(row['asset_id'], ads['name']), str(temporary)+ads['name'], ads['sha256'], ads['size'], 'wb')
        if row['mtime_ns'] is not None:
            os.utime(native(temporary), ns=(row['mtime_ns'],row['mtime_ns']))
        guarded(destination.parent, destination)
        if os.name == 'nt':
            os.rename(native(temporary), native(destination))  # Windows: fails if destination exists.
        else:
            os.link(temporary, destination); temporary.unlink()
        if digest_file(destination) != row['sha256']:
            raise ValueError('Published restore hash mismatch')
        for ads in streams:
            if digest_file(str(destination)+ads['name']) != ads['sha256']:
                raise ValueError('Published stream hash mismatch')
        return {'source_id':source_id,'destination':str(destination),'sha256':row['sha256'],
                'streams':len(streams),'mtime_ns':row['mtime_ns']}

    def restore_tree(self, machine, prefix, destination):
        prefix = str(relative_name(prefix)).rstrip('/')
        rows = self.db.execute('SELECT source_id,relative_path,kind,mtime_ns FROM sources WHERE machine=? AND (relative_path=? OR substr(relative_path,1,?)=?) ORDER BY length(relative_path),relative_path', (machine,prefix,len(prefix)+1,prefix+'/')).fetchall()
        if not rows:
            raise ValueError('No sources match this exact prefix')
        destination = Path(os.path.abspath(destination))
        guarded(destination.parent, destination)
        os.makedirs(native(destination), exist_ok=False)
        receipts = []
        directories = []
        # Retain the selected directory itself under destination. This also
        # makes single-file prefixes unambiguous and preserves relative links.
        parent = str(PurePosixPath(prefix).parent)
        for row in rows:
            sub = PurePosixPath(row['relative_path']).relative_to(parent)
            target = destination / relative_name(str(sub))
            guarded(destination, target)
            if row['kind'] == 'directory':
                os.makedirs(native(target), exist_ok=True); directories.append((target,row['mtime_ns']))
            elif row['kind'] == 'file':
                receipts.append(self.restore_file(row['source_id'], target))
            else:
                raise ValueError('Unsupported source kind retained in catalogue')
        for target,mtime in reversed(directories):
            if mtime is not None:
                os.utime(native(target), ns=(mtime,mtime))
        receipt_path = destination/'RESTORE_RECEIPT.json'
        with open(native(receipt_path),'x',encoding='utf-8') as stream:
            json.dump({'machine':machine,'prefix':prefix,'files':receipts,'directories':len(directories),
                       'limits':'File bytes, NTFS streams and mtime verified; ACL, creation time and hardlink topology are recorded, not recreated.'},stream,ensure_ascii=False,indent=2)
        return {'files':len(receipts),'directories':len(directories),'receipt':str(receipt_path)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog',type=Path,default=Path(__file__).resolve().parents[2]/'data/catalog.sqlite')
    sub = parser.add_subparsers(dest='command',required=True)
    find = sub.add_parser('find');find.add_argument('text');find.add_argument('--machine',choices=['PC1','PC2','PC3']);find.add_argument('--limit',type=int,default=30)
    show = sub.add_parser('show');show.add_argument('source_id')
    restore = sub.add_parser('restore');restore.add_argument('source_id');restore.add_argument('--to',type=Path,required=True)
    tree = sub.add_parser('restore-tree');tree.add_argument('machine',choices=['PC1','PC2','PC3']);tree.add_argument('prefix');tree.add_argument('--to',type=Path,required=True)
    args = parser.parse_args(argv);catalogue=Catalogue(args.catalog)
    try:
        if args.command == 'find': result=catalogue.find(args.text,args.machine,args.limit)
        elif args.command == 'show': result=dict(catalogue.source(args.source_id))
        elif args.command == 'restore': result=catalogue.restore_file(args.source_id,args.to)
        else: result=catalogue.restore_tree(args.machine,args.prefix,args.to)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:
        catalogue.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
