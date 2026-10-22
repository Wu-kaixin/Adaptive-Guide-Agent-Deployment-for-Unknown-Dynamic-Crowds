"""One-time, byte-preserving local consolidation; never executes experiments."""
from pathlib import Path
from collections import Counter
import hashlib
import json
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / '其他电脑转移' / ROOT.name
OUT = Path(__file__).resolve().parent
BACKUP = ROOT / 'backups/project_rebuild_20261023'

def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def git(*args):
    return subprocess.check_output(['git', '-C', str(SOURCE), *args])

if __name__ == '__main__':
    rows = json.loads((OUT / 'source_inventory.json').read_text(encoding='utf-8'))['files']
    BACKUP.mkdir(parents=True, exist_ok=True)
    archive = BACKUP / 'incoming_snapshot.zip'
    if archive.exists():
        raise RuntimeError('Refusing to overwrite source snapshot')
    assert SOURCE.resolve() == (ROOT / '其他电脑转移' / ROOT.name).resolve()
    assert git('merge-base', '--is-ancestor', 'c37a023', 'HEAD') == b''
    metadata = {'current_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip(),
                'incoming_head': git('rev-parse', 'HEAD').decode().strip(),
                'incoming_status': git('status', '--porcelain=v1').decode(),
                'incoming_log': git('log', '--oneline', 'c37a023..HEAD').decode()}
    (OUT / 'git_sources.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Archiving all {len(rows)} incoming files', flush=True)
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as z:
        for i, r in enumerate(rows):
            z.write(SOURCE / r['source'], r['source'])
            if i % 3000 == 0: print(f'archive {i}/{len(rows)}', flush=True)
    print('Checking every archived byte', flush=True)
    with zipfile.ZipFile(archive) as z:
        assert len(z.infolist()) == len(rows)
        for r in rows:
            h = hashlib.sha256()
            with z.open(r['source']) as f:
                for block in iter(lambda: f.read(1024 * 1024), b''):
                    h.update(block)
            assert h.hexdigest() == r['sha256'], r['source']
            r['archive'] = f'{archive.relative_to(ROOT).as_posix()}!/{r["source"]}'
    print('Installing project files and retaining previous conflicts', flush=True)
    for r in rows:
        if r['classification'] == 'runtime_archive':
            r['destination'] = r['archive']
            continue
        src, dst = SOURCE / r['source'], ROOT / r['source']
        if r['classification'] == 'conflict':
            assert sha(dst) == r['previous']['sha256'], r['source']
            old = BACKUP / 'previous_conflicts' / r['source']
            old.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, old)
            assert sha(old) == r['previous']['sha256']
            r['previous_archive'] = old.relative_to(ROOT).as_posix()
        if r['classification'] != 'identical':
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        assert sha(dst) == r['sha256'], r['source']
        r['destination'] = r['source']
    recovered = []
    for name in git('ls-files', '--deleted', '-z').decode().split('\0'):
        if not name:
            continue
        blob = git('show', f'HEAD:{name}')
        target = ROOT / name
        expected = hashlib.sha256(blob).hexdigest()
        if target.exists() and sha(target) != expected:
            target = BACKUP / 'recovered_git_conflicts' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
        assert sha(target) == expected
        recovered.append({'source': f'{metadata["incoming_head"]}:{name}', 'destination': target.relative_to(ROOT).as_posix(), 'sha256': expected, 'bytes': len(blob)})
    result = {'source_files': len(rows), 'classification_counts': dict(Counter(r['classification'] for r in rows)),
              'source_bytes': sum(r['bytes'] for r in rows), 'archive': str(archive.relative_to(ROOT)),
              'archive_sha256': sha(archive), 'verified_archive_files': len(rows),
              'recovered_from_git': recovered, 'files': rows, 'transfer_deleted': False}
    (OUT / 'merge_manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k not in ('files', 'recovered_from_git')}, ensure_ascii=False), flush=True)
    print(f'Recovered {len(recovered)} tracked files from incoming Git objects', flush=True)
