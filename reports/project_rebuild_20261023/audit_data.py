"""Resolve historical hash references without editing frozen manifests or inputs."""
from pathlib import Path
from collections import Counter, defaultdict
import hashlib
import json
import re
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
HEX = re.compile(r'^[0-9a-f]{64}$', re.I)
TOPS = {'runs', 'reports', 'configs', 'src', 'tests', 'experiments', 'notebooks', '.tmp', 'docs'}

def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''): h.update(b)
    return h.hexdigest()

def mapped(raw):
    value = raw.replace('\\', '/')
    parts = value.split('/')
    # Old absolute paths retain their original spelling in the evidence.
    if ROOT.name in parts:
        parts = parts[parts.index(ROOT.name) + 1:]
    elif parts[0] not in TOPS:
        return None
    if not parts or parts[0] not in TOPS or '..' in parts: return None
    rel = '/'.join(parts)
    # Some historical maps use package-relative experiments/*.py keys.
    if rel.startswith('experiments/') and rel.endswith('.py') and not (ROOT / rel).exists() and (ROOT / 'src/udc' / rel).exists():
        return 'src/udc/' + rel
    return rel

def references(obj, origin):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and HEX.fullmatch(v) and mapped(k):
                yield (mapped(k), v.lower(), origin, k)
            elif k in ('path', 'file', 'source_path') and isinstance(v, str) and mapped(v):
                h = obj.get('sha256') or obj.get('hash')
                if isinstance(h, str) and HEX.fullmatch(h): yield (mapped(v), h.lower(), origin, v)
            yield from references(v, origin)
    elif isinstance(obj, list):
        for value in obj: yield from references(value, origin)

def main():
    ledger = OUT / 'recovered_data_files.json'
    previous_recoveries = {(r['path'], r['sha256']): r['recovered_from'] for r in json.loads(ledger.read_text(encoding='utf-8'))} if ledger.exists() else {}
    refs = defaultdict(set)
    paths = [p for p in (ROOT / 'reports').rglob('*.json') if OUT not in p.parents]
    paths += [p for p in (ROOT / 'runs').rglob('protocol.json') if 'project_rebuild_20261023' not in p.parts]
    errors = []
    for p in paths:
        try: obj = json.loads(p.read_text(encoding='utf-8-sig'))
        except Exception as e:
            errors.append({'path': p.relative_to(ROOT).as_posix(), 'error': str(e)}); continue
        for rel, h, origin, raw in references(obj, p.relative_to(ROOT).as_posix()):
            refs[rel, h].add((origin, raw))
    manifest = json.loads((OUT / 'merge_manifest.json').read_text(encoding='utf-8'))
    candidates = defaultdict(list)
    for rel, info in json.loads((OUT / 'current_inventory.json').read_text(encoding='utf-8')).items():
        candidates[info['sha256']].append(('file', rel))
    for row in manifest['files']:
        candidates[row['sha256']].append(('zip', row['source']))
    for row in manifest['recovered_from_git']:
        candidates[row['sha256']].append(('file', row['destination']))
    print(f'{len(paths)} JSON files; {len(refs)} unique path/hash references', flush=True)
    existing_hashes = {}
    def cached(p):
        s = str(p)
        if s not in existing_hashes: existing_hashes[s] = sha(p)
        return existing_hashes[s]
    missing_hashes = {h for (rel, h) in refs if not (ROOT / rel).is_file()}
    # Existing local backups are part of the available project, not new data.
    backup_inventory = {}
    for p in (ROOT / 'backups').rglob('*'):
        if not p.is_file() or 'project_rebuild_20261023' in p.parts or '__pycache__' in p.parts: continue
        rel = p.relative_to(ROOT).as_posix()
        h = cached(p)
        backup_inventory[rel] = {'bytes': p.stat().st_size, 'sha256': h}
        if h in missing_hashes: candidates[h].append(('file', rel))
    (OUT / 'existing_backup_inventory.json').write_text(json.dumps(backup_inventory, ensure_ascii=False, indent=2), encoding='utf-8')
    rows = []
    import shutil
    with zipfile.ZipFile(ROOT / manifest['archive']) as archive:
        for (rel, expected), sources in sorted(refs.items()):
            target = ROOT / rel
            recovered_from = previous_recoveries.get((rel, expected))
            if not target.exists() and rel.split('/')[0] in ('runs', 'reports'):
                for kind, loc in candidates.get(expected, []):
                    if kind == 'file':
                        p = ROOT / loc
                        if not p.is_file() or cached(p) != expected: continue
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(p, target)
                    else:
                        data = archive.read(loc)
                        if hashlib.sha256(data).hexdigest() != expected: continue
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(data)
                    recovered_from = f'{kind}:{loc}'
                    assert cached(target) == expected
                    break
            actual = cached(target) if target.is_file() else None
            state = 'matching' if actual == expected else 'different_version' if actual else 'missing'
            rows.append({'path': rel, 'expected_sha256': expected, 'actual_sha256': actual,
                         'status': state, 'recovered_from': recovered_from,
                         'sources': [{'manifest': a, 'original_path': b} for a, b in sorted(sources)]})
    summary = {'scope': 'hash-bearing file references in historical reports and run protocol.json; not a claim about unreferenced historical data',
               'json_files_read': len(paths), 'parse_errors': errors,
               'unique_path_hash_references': len(rows), 'status_counts': dict(Counter(r['status'] for r in rows)),
               'missing_unique_paths': len({r['path'] for r in rows if r['status'] == 'missing'}),
               'byte_identical_files_recovered': sum(r['recovered_from'] is not None for r in rows),
               'status_by_top_level': {state: dict(Counter(r['path'].split('/')[0] for r in rows if r['status'] == state)) for state in ('matching', 'missing', 'different_version')},
               'missing_runs_by_dataset': dict(Counter(r['path'].split('/')[1] for r in rows if r['status'] == 'missing' and r['path'].startswith('runs/'))),
               'references': rows}
    (OUT / 'data_reference_audit.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in summary.items() if k != 'references'}, ensure_ascii=False), flush=True)

if __name__ == '__main__': main()
