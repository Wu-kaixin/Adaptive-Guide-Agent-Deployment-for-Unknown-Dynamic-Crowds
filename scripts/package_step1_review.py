"""Export/verify the completed Step 1 review evidence without executing science.

The default verifies the published bundle on a clean clone. --build requires
the original local artifacts and a new output directory. No source is modified.
"""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'reports/step1_review_20261029'
STUDIES = ('deployment_confirmation_v2', 'sequential_development_v3',
           'sequential_confirmation_v3')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_new(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(data)


def build():
    if (DEST / 'manifest.json').exists() or (DEST / 'evidence').exists():
        raise FileExistsError('Existing review evidence is immutable')
    rows = []

    def copy(rel):
        data = (ROOT / rel).read_bytes()
        target = DEST / 'evidence' / rel
        write_new(target, data)
        rows.append(dict(original=rel, published=target.relative_to(ROOT).as_posix(),
                         bytes=len(data), sha256=digest(data)))

    def archive(name, paths):
        target = DEST / 'evidence' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        members = []
        with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED) as z:
            for path in sorted(paths):
                data = path.read_bytes()
                rel = path.relative_to(ROOT).as_posix()
                info = zipfile.ZipInfo(rel, (2026, 10, 29, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, data)
                members.append(dict(original=rel, sha256=digest(data), bytes=len(data)))
        rows.append(dict(published=target.relative_to(ROOT).as_posix(),
                         sha256=digest(target.read_bytes()), bytes=target.stat().st_size,
                         members=members))

    completion = ROOT / 'reports/step1_closure_20261028/final_readonly_summary_v1/completion_verification.json'
    verified = json.loads(completion.read_text(encoding='utf-8'))
    for rel, expected in verified['artifact_sha256'].items():
        if digest((ROOT / rel).read_bytes()) != expected:
            raise ValueError('Completion hash mismatch: ' + rel)
    for rel in verified['artifact_sha256']:
        if rel.startswith('runs/'):
            copy(rel)
    snapshots = []
    for study in STUDIES:
        base = ROOT / 'runs/step1_closure_20261028' / study
        manifest = json.loads((base / 'manifest.json').read_text(encoding='utf-8'))
        for rel, expected in manifest['sources'].items():
            source = base / 'source_snapshot' / rel
            if digest(source.read_bytes()) != expected:
                raise ValueError('Frozen source mismatch: ' + str(source))
            snapshots.append(source)
        copy((base / 'protocol.yaml').relative_to(ROOT).as_posix())
        copy((base / 'summary.json').relative_to(ROOT).as_posix())
        summary = base / 'readonly_audit_v2/episode_summaries.json'
        if summary.exists():
            copy(summary.relative_to(ROOT).as_posix())
    control = ROOT / 'runs/step1_closure_20261028/control_v2'
    snapshots.extend(p for p in (control / 'engine_snapshot').rglob('*')
                     if p.is_file() and '__pycache__' not in p.parts)
    workflow = json.loads((control / 'workflow.json').read_text(encoding='utf-8'))
    for rel, expected in workflow['sealed_files'].items():
        if digest((control / rel).read_bytes()) != expected:
            raise ValueError('Analysis seal mismatch: ' + rel)
    archive('frozen_sources.zip', snapshots)
    receipts = list((ROOT / 'reports/static_service_audit_20261026/final_path_failure_census/units').rglob('*'))
    receipts += list((ROOT / 'reports/static_service_audit_20261026/parallel_frozen_replay').glob('*/*'))
    archive('original_readonly_unit_receipts.zip', [p for p in receipts if p.is_file()])
    for rel in ('runs/step1_closure_20261028/control_v1/error.json',
                'runs/step1_closure_20261028/control_v1/workflow.json',
                'runs/step1_closure_20261028/sequential_smoke_v2/interruption_receipt.json'):
        copy(rel)
    # Index the original published reports without rewriting frozen evidence.
    for directory in ('step1_closure_20261028', 'step1_safety_stop_20261028',
                      'step1_followthrough_20261028'):
        for path in sorted((ROOT / 'reports' / directory).rglob('*')):
            if path.is_file():
                rel = path.relative_to(ROOT).as_posix()
                data = path.read_bytes()
                rows.append(dict(original=rel, published=rel, bytes=len(data), sha256=digest(data)))
    for rel in ('reports/static_service_audit_20261026/REPORT.zh.md',
                'reports/static_service_audit_20261026/completion_receipt.json'):
        data = (ROOT / rel).read_bytes()
        rows.append(dict(original=rel, published=rel, bytes=len(data), sha256=digest(data)))
    manifest = dict(schema=1, purpose='Byte-preserved review export; no new experiments',
                    raw_trajectories_included=False, original_confirmation_resumed=False,
                    files=rows)
    write_new(DEST / 'manifest.json', (json.dumps(manifest, indent=2, ensure_ascii=False) + '\n').encode())


def verify():
    manifest = json.loads((DEST / 'manifest.json').read_text(encoding='utf-8'))
    members = 0
    for row in manifest['files']:
        path = ROOT / row['published']
        if digest(path.read_bytes()) != row['sha256']:
            raise ValueError('Published artifact mismatch: ' + row['published'])
        if 'members' in row:
            with zipfile.ZipFile(path) as z:
                if sorted(z.namelist()) != sorted(m['original'] for m in row['members']):
                    raise ValueError('Archive membership mismatch: ' + str(path))
                for member in row['members']:
                    if digest(z.read(member['original'])) != member['sha256']:
                        raise ValueError('Archive content mismatch: ' + member['original'])
                    members += 1
    print(json.dumps(dict(verified=True, files=len(manifest['files']),
                          archived_members=members, new_scientific_executions=0)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', action='store_true')
    args = parser.parse_args()
    if args.build:
        build()
    verify()
