"""Map reconstruction to historical references, materializing exact bytes only."""
from pathlib import Path
from collections import defaultdict, Counter
import gzip
import json
import shutil

from rebuild_missing_data import PROJECT, OUTPUT, read, write, sha

def main():
    report = PROJECT / 'reports/project_rebuild_20261023'
    audit = read(report / 'data_reference_audit.json')
    files = read(report / 'reconstruction_files.json')
    by_hash = defaultdict(list)
    for rel, info in files.items(): by_hash[info['sha256']].append(rel)
    recreated, refs = [], []
    for row in audit['references']:
        target = PROJECT / row['path']
        candidates = by_hash.get(row['expected_sha256'], [])
        materialized = None
        if row['status'] == 'missing' and row['path'].startswith('runs/') and not target.exists() and candidates:
            source = PROJECT / candidates[0]
            assert sha(source) == row['expected_sha256']
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            assert sha(target) == row['expected_sha256']
            materialized = {'historical_path': row['path'], 'sha256': row['expected_sha256'], 'reconstructed_source': candidates[0],
                            'origin': 're-executed in 2026-10-23 reconstruction; exact expected bytes, not a recovered old file'}
            recreated.append(materialized)
        if target.is_file():
            actual = sha(target)
            state = 'matching_bytes' if actual == row['expected_sha256'] else 'different_historical_version'
        else:
            actual, state = None, 'missing_historical_original'
        refs.append({**row, 'initial_audit_status': row['status'], 'post_reconstruction_status': state,
                     'post_reconstruction_sha256': actual, 'reconstructed_byte_sources': candidates,
                     'reconstructed_bytes_materialized': materialized is not None})
    write(report / 'reconstructed_byte_matches.json', recreated)
    with gzip.open(report / 'historical_references_after_reconstruction.json.gz', 'wt', encoding='utf-8', compresslevel=6) as stream:
        json.dump(refs, stream, ensure_ascii=False, separators=(',', ':'))
    result = {'initial_reference_count': len(refs), 'initial_status_counts': audit['status_counts'],
              'post_reconstruction_status_counts': dict(Counter(r['post_reconstruction_status'] for r in refs)),
              'exact_reconstructed_files_materialized': len(recreated),
              'remaining_historical_missing_paths': len({r['path'] for r in refs if r['post_reconstruction_status'] == 'missing_historical_original'}),
              'original_hash_locks_modified': False, 'reconstruction_is_new_independent_confirmation': False,
              'all_new_reconstruction_data_root': OUTPUT.relative_to(PROJECT).as_posix(),
              'meaning': 'Exact-byte recreations are separately attributed. Nonmatching metadata and unavailable historical dirty variants stay historical gaps; new complete datasets are catalogued independently.'}
    write(report / 'historical_references_after_reconstruction_summary.json', result)
    print(result)

if __name__ == '__main__': main()
