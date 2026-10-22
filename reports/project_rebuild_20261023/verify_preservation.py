"""Account for all pre-merge project files without rewriting any evidence."""
from pathlib import Path
from collections import defaultdict, Counter
import hashlib
import json

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent

def read(name): return json.loads((OUT / name).read_text(encoding='utf-8'))
def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''): h.update(block)
    return h.hexdigest()

original = read('current_inventory.json'); merged = read('merge_manifest.json')
locations = defaultdict(list)
for row in merged['files']:
    locations[row['sha256']].append(row['archive'])
    if row.get('previous_archive'):
        path = ROOT / row['previous_archive']
        assert sha(path) == row['previous']['sha256']
        locations[row['previous']['sha256']].append(row['previous_archive'])
result = []
for rel, info in original.items():
    path = ROOT / rel
    runtime = '__pycache__' in path.parts or path.suffix in ('.pyc', '.pyo') or '.egg-info' in rel
    if path.is_file() and sha(path) == info['sha256']:
        state, location = 'unchanged', rel
    elif locations.get(info['sha256']):
        state, location = 'preserved_variant', locations[info['sha256']][0]
    elif runtime:
        state, location = 'generated_cache_rebuilt', rel
    else:
        raise RuntimeError('Unaccounted pre-merge file: ' + rel)
    result.append({'original_path': rel, 'sha256': info['sha256'], 'status': state, 'location': location})
assert merged['transfer_deleted'] and not (ROOT / '其他电脑转移').exists()
summary = {'pre_merge_files': len(result), 'counts': dict(Counter(r['status'] for r in result)),
           'unaccounted_scientific_or_user_files': 0, 'incoming_files_verified_in_archive': merged['verified_archive_files'],
           'transfer_deleted': True, 'files': result}
(OUT / 'preservation_verification.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print({k: v for k, v in summary.items() if k != 'files'})
