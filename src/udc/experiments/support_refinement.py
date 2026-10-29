"""Freeze source/config before a read-only support-refinement development audit."""
import argparse
import json
from pathlib import Path
import shutil
import traceback

import psutil
from threadpoolctl import threadpool_limits

from .static_multigroup import ROOT, digest, scientific_sources, environment_record, write_json
from udc.analysis.reference_support_audit import audit


def run(original, output, collective_identity=False):
    original, output = Path(original).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    sources = scientific_sources()
    for name in sources:
        path = output / 'source_snapshot' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, path)
    manifest = {'study': str(original), 'source_manifest_sha256': digest(original / 'manifest.json'),
        'evidence': 'read_only_old_action_development_not_independent_confirmation',
        'new_independent_samples': 0, 'enclosure': 'monotone_sandwich_v1',
        'identity': 'collective_coverage_v1' if collective_identity else 'reduced_link_v1',
        'sources': sources, 'environment': environment_record(), 'actions_reoptimized': False}
    write_json(output / 'manifest.json', manifest)
    write_json(output / 'seal.json', {'manifest_sha256': digest(output / 'manifest.json')})
    process = psutil.Process()
    write_json(output / 'process.json', {'pid': process.pid, 'create_time': process.create_time(),
        'exe': process.exe(), 'cmdline': process.cmdline()})
    try:
        with threadpool_limits(limits=1):
            result = audit(original, output / 'audit', 'monotone_sandwich_v1', manifest['identity'])
        write_json(output / 'completion.json', {'model_violations': len(result['violations']),
            'new_independent_samples': 0, 'fixed_pairs': result['fixed_pairs'], 'accepted': result['accepted']})
        return result
    except Exception:
        write_json(output / 'audit_error.json', {'traceback': traceback.format_exc()})
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--original', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--collective-identity', action='store_true')
    args = parser.parse_args()
    result = run(args.original, args.output, args.collective_identity)
    print(json.dumps({'fixed_pairs': result['fixed_pairs'], 'accepted': result['accepted'],
        'model_violations': len(result['violations']), 'support_enclosure': result['support_enclosure'],
        'support_identity': result['support_identity']}, ensure_ascii=False), flush=True)
