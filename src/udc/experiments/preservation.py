"""Preserve a paused frozen study without changing any original artifact."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import zipfile


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def save(study, output):
    study, output = Path(study).resolve(), Path(output).resolve()
    manifest = json.loads((study / 'manifest.json').read_text(encoding='utf-8'))
    seal = json.loads((study / 'seal.json').read_text(encoding='utf-8'))
    if digest(study / 'manifest.json') != seal['manifest_sha256']:
        raise ValueError('Frozen manifest changed')
    expected = {'protocol.yaml': manifest['protocol_sha256']}
    expected.update({'source_snapshot/' + name: sha for name, sha in manifest['sources'].items()})
    for unit in manifest['units']:
        for field in ('truth', 'observation'):
            expected[unit[field]] = unit[field + '_sha256']
    files = {name: study / name for name in ('manifest.json', 'seal.json', 'protocol.yaml')}
    files.update({name: study / name for name in expected})
    complete, incomplete, receipts, statuses = [], [], {}, Counter()
    for task in manifest['tasks']:
        folder = study / 'results' / task['task_id']
        if not folder.exists():
            continue
        receipt_path = folder / 'receipt.json'
        if receipt_path.is_file():
            receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
            if set(receipt) != {'config.json', 'planning.json', 'metrics.json', 'state.json', 'trajectory.npz'}:
                raise ValueError('Unexpected receipt schema')
            for name, sha in receipt.items():
                expected[f'results/{task["task_id"]}/{name}'] = sha
            complete.append(task['task_id'])
            receipts[task['task_id']] = digest(receipt_path)
            statuses[json.loads((folder / 'metrics.json').read_text(encoding='utf-8'))['status']] += 1
        else:
            incomplete.append(task['task_id'])
        for path in folder.iterdir():
            if path.is_file():
                files[path.relative_to(study).as_posix()] = path
    output.mkdir(parents=True, exist_ok=True)
    archive = output / 'frozen_study_preserved.zip'
    temporary = archive.with_suffix('.zip.partial')
    if archive.exists() or temporary.exists():
        raise FileExistsError('Never overwrite a preservation archive')
    hashes, total = {}, 0
    started = datetime.now().astimezone().isoformat(timespec='seconds')
    with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as target:
        for relative, source in sorted(files.items()):
            if Path(relative).is_absolute() or '..' in Path(relative).parts or source.is_symlink():
                raise ValueError('Unsafe preservation path')
            h = hashlib.sha256()
            with source.open('rb') as file, target.open(relative, 'w', force_zip64=True) as entry:
                for chunk in iter(lambda: file.read(1024 * 1024), b''):
                    h.update(chunk)
                    entry.write(chunk)
                    total += len(chunk)
            hashes[relative] = h.hexdigest()
            if relative in expected and hashes[relative] != expected[relative]:
                raise ValueError('Artifact hash mismatch: ' + relative)
            if len(hashes) % 5000 == 0:
                print(json.dumps({'archived_files': len(hashes), 'total_files': len(files), 'bytes': total}), flush=True)
    with zipfile.ZipFile(temporary) as stored:
        bad = stored.testzip()
        if bad:
            raise ValueError('Archive CRC mismatch: ' + bad)
    temporary.replace(archive)
    (output / 'file_hashes.json').write_text(json.dumps(hashes, indent=2) + '\n', encoding='utf-8')
    (output / 'preserved_receipts.json').write_text(json.dumps(receipts, indent=2) + '\n', encoding='utf-8')
    value = {'started_at': started, 'saved_at': datetime.now().astimezone().isoformat(timespec='seconds'),
             'manifest_sha256': digest(study / 'manifest.json'), 'complete_tasks': len(complete),
             'scheduled_tasks': len(manifest['tasks']), 'unrecorded_tasks': len(manifest['tasks']) - len(complete),
             'incomplete_directories': incomplete, 'statuses': dict(statuses),
             'files_verified_and_archived': len(hashes), 'uncompressed_bytes': total,
             'archive': str(archive), 'archive_sha256': digest(archive), 'archive_crc_verified': True,
             'file_index_sha256': digest(output / 'file_hashes.json'),
             'receipt_index_sha256': digest(output / 'preserved_receipts.json'),
             'original_artifacts_modified': False,
             'in_flight_process_memory': 'Retained in suspended processes, not serialized to disk.'}
    (output / 'checkpoint.json').write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(save(args.study, args.output), ensure_ascii=False, indent=2), flush=True)
