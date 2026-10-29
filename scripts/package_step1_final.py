"""Publish/verify final Step 1 evidence bytes; never executes an experiment.

Default: verify the Git-distributed reports and metadata/input/source archive.
--full-local additionally requires original trajectories and checks receipts.
--restore-public writes only the public subset into a new destination.
"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = 'reports/step1_release_20261030'
FINAL = 'reports/step1_final_20261029'
STUDY = 'runs/step1_final_evaluation_20261029'
ARCHIVE = 'evidence/final_metadata_inputs_sources.tar.gz'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def safe_name(name):
    p = PurePosixPath(name)
    if not name or not p.parts or p.is_absolute() or '..' in p.parts or '\\' in name or ':' in name or p.as_posix() != name:
        raise ValueError('Unsafe archive/artifact path: ' + name)
    return name


def checked(root, name, expected):
    safe_name(name)
    path = (Path(root) / name).resolve()
    if not path.is_relative_to(Path(root).resolve()) or not path.is_file() or digest(path) != expected:
        raise ValueError('Missing or changed artifact: ' + name)
    return path


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def write_archive(path, files, compression=6):
    path.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    with path.open('xb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0, compresslevel=compression) as zipped:
            with tarfile.open(fileobj=zipped, mode='w|', format=tarfile.PAX_FORMAT) as archive:
                for name, source in sorted(files.items()):
                    safe_name(name)
                    data = source.read_bytes()
                    info = tarfile.TarInfo(name)
                    info.size, info.mtime, info.mode = len(data), 0, 0o644
                    archive.addfile(info, io.BytesIO(data))
                    size += len(data)
    return dict(sha256=digest(path), bytes=path.stat().st_size, files=len(files), uncompressed_bytes=size)


def verify_archive(path, expected, destination=None):
    """Verify each regular member; reject traversal, links and duplicate names."""
    if destination is not None:
        destination = Path(destination).resolve()
        destination.mkdir(parents=True, exist_ok=False)
    seen, folded = set(), set()
    with tarfile.open(path, 'r|gz') as archive:
        for member in archive:
            name = safe_name(member.name)
            if not member.isfile() or name not in expected or name in seen or name.casefold() in folded:
                raise ValueError('Unexpected, linked or duplicate member: ' + name)
            content = archive.extractfile(member)
            data = content.read()
            if hashlib.sha256(data).hexdigest() != expected[name]:
                raise ValueError('Archive member hash mismatch: ' + name)
            if destination is not None:
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as stream:
                    stream.write(data)
            seen.add(name)
            folded.add(name.casefold())
    if seen != set(expected):
        raise ValueError('Archive membership incomplete')
    return len(seen)


def indexed_expected(root):
    report = root / FINAL
    seal = read(report / 'final_seal.json')
    checked(root, FINAL + '/artifact_index.json', seal['artifact_index_sha256'])
    checked(root, FINAL + '/metrics.json', seal['metrics_sha256'])
    checked(root, FINAL + '/REPORT.zh.md', seal['report_sha256'])
    return read(report / 'artifact_index.json')['files']


def build(root, raw_output=None):
    package = root / PACKAGE
    if (package / 'publication_manifest.json').exists() or (package / ARCHIVE).exists():
        raise FileExistsError('Published package is immutable; use a separate version')
    index = indexed_expected(root)
    for name, sha in index.items():
        checked(root, name, sha)
    expected = {n: h for n, h in index.items() if n.startswith(STUDY + '/')}
    extra_paths = set()
    for base in (root / STUDY, root / STUDY / 'api_contract_audit'):
        m = read(base / 'manifest.json')
        extra_paths.update(base / 'source_snapshot' / n for n in m['sources'])
        for u in m['units']:
            extra_paths.update(base / u[n] for n in ('truth', 'observation'))
            extra_paths.update(base / v['path'] for v in u['series'].values())
    extra_paths.update(p for p in (root / STUDY).rglob('*.json')
                       if p.name in ('session.json', 'session_receipt.json', 'config.json'))
    extras = {p.relative_to(root).as_posix(): digest(p) for p in sorted(extra_paths)
              if p.relative_to(root).as_posix() not in expected}
    expected.update(extras)
    archive = write_archive(package / ARCHIVE, {n: checked(root, n, h) for n, h in expected.items()})
    verify_archive(package / ARCHIVE, expected)
    published = {p.relative_to(root).as_posix(): digest(p) for p in (root / FINAL).iterdir() if p.is_file()}
    raw = None
    if raw_output is not None:
        raw_files = {p.relative_to(root).as_posix(): p for p in (root / STUDY).rglob('*') if p.is_file()}
        raw = write_archive(raw_output, raw_files, compression=1)
        raw.update(filename=raw_output.name, availability='local maintainer archive; no public download URL supplied',
                   member_prefix=STUDY)
    manifest = dict(schema=1, purpose='Static Step 1 engineering baseline and bounded research evidence; whole research goal incomplete',
        study=STUDY, final_reports=FINAL, published_files=published, extra_archive_files=extras,
        archive=dict(path=ARCHIVE, **archive), raw_archive=raw, raw_trajectories_in_public_archive=False,
        independent_physical_inputs=48, scientific_executions_by_packager=0,
        data_root_mapping='Data root is a repository-shaped directory containing runs/; report files come from the Git checkout')
    write_new(package / 'publication_manifest.json', manifest)
    return manifest


def verify(root, data_root=None, full=False, restore=None):
    package = root / PACKAGE
    manifest = read(package / 'publication_manifest.json')
    for name, h in manifest['published_files'].items():
        checked(root, name, h)
    index = indexed_expected(root)
    expected = {n: h for n, h in index.items() if n.startswith(STUDY + '/')}
    expected.update(manifest['extra_archive_files'])
    archive = checked(package, manifest['archive']['path'], manifest['archive']['sha256'])
    members = verify_archive(archive, expected, restore)
    receipts, linked = 0, 0
    if full:
        data_root = Path(data_root or root).resolve()
        for name, h in {**index, **expected}.items():
            checked(data_root if name.startswith('runs/') else root, name, h)
        # Metadata seals alone do not establish raw trajectory availability.
        for name in expected:
            if PurePosixPath(name).name not in ('receipt.json', 'session_receipt.json'):
                continue
            path = data_root / name
            receipt = read(path)
            if not receipt or any(not isinstance(h, str) or re.fullmatch('[0-9a-f]{64}', h) is None for h in receipt.values()):
                raise ValueError('Malformed direct receipt: ' + name)
            for rel, h in receipt.items():
                safe_name(rel)
                checked(data_root, (path.parent / rel).relative_to(data_root).as_posix(), h)
                linked += 1
            receipts += 1
        for prefix in (STUDY, STUDY + '/api_contract_audit'):
            base = data_root / prefix
            m = read(base / 'manifest.json')
            for name, h in m['sources'].items():
                checked(data_root, prefix + '/source_snapshot/' + name, h)
            for u in m['units']:
                for field in ('truth', 'observation'):
                    checked(data_root, prefix + '/' + u[field], u[field + '_sha256'])
                for v in u['series'].values():
                    checked(data_root, prefix + '/' + v['path'], v['sha256'])
    return dict(verified=True, public_archive_members=members, published_files=len(manifest['published_files']),
                raw_trajectories_in_public_archive=False, full_local_receipts_verified=receipts,
                raw_and_state_artifact_links_verified=linked, full_local_data_verified=bool(full),
                new_scientific_executions=0)


def restore_raw(root, archive_path, destination):
    info = read(root / PACKAGE / 'publication_manifest.json')['raw_archive']
    if info is None or digest(archive_path) != info['sha256']:
        raise ValueError('Raw archive is unavailable or its SHA-256 differs from the publication manifest')
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    seen, folded = set(), set()
    with tarfile.open(archive_path, 'r|gz') as archive:
        for member in archive:
            name = safe_name(member.name)
            if (not member.isfile() or not name.startswith(STUDY + '/') or name in seen
                    or name.casefold() in folded):
                raise ValueError('Unsafe or duplicate raw member: ' + name)
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                shutil.copyfileobj(archive.extractfile(member), stream, 1048576)
            seen.add(name)
            folded.add(name.casefold())
    if len(seen) != info['files']:
        raise ValueError('Raw archive member count changed')
    return dict(raw_archive_sha256_verified=True, restored_files=len(seen), destination=str(destination),
                full_local_verification_still_required=True, new_scientific_executions=0)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build', action='store_true')
    p.add_argument('--raw-output', type=Path)
    p.add_argument('--full-local', action='store_true')
    p.add_argument('--data-root', type=Path)
    p.add_argument('--restore-public', type=Path)
    p.add_argument('--restore-raw', type=Path)
    p.add_argument('--destination', type=Path)
    args = p.parse_args()
    if args.raw_output and not args.build:
        p.error('--raw-output requires --build')
    if bool(args.restore_raw) != bool(args.destination):
        p.error('--restore-raw requires --destination, and vice versa')
    if args.restore_raw:
        print(json.dumps(restore_raw(ROOT, args.restore_raw, args.destination), ensure_ascii=False))
        return
    if args.build:
        build(ROOT, args.raw_output)
    print(json.dumps(verify(ROOT, args.data_root, args.full_local, args.restore_public), ensure_ascii=False))


if __name__ == '__main__':
    main()
