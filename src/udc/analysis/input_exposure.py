"""Local manifest inventory before allocating a new stochastic input namespace.

This does not claim access to other machines or unindexed historical artifacts.
New inputs still require a separate content-hash overlap check after generation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import numpy as np

from udc.experiments.static_multigroup import write_json


def namespaces(value):
    result = set()
    if isinstance(value, dict):
        if isinstance(value.get('namespace'), int):
            result.add(value['namespace'])
        for child in value.values():
            result |= namespaces(child)
    elif isinstance(value, list):
        for child in value:
            result |= namespaces(child)
    return result


def _truth_references(value):
    if isinstance(value, dict):
        if isinstance(value.get('truth'), str):
            yield value['truth']
        for child in value.values():
            yield from _truth_references(child)
    elif isinstance(value, list):
        for child in value:
            yield from _truth_references(child)


def _people_hash(path):
    fingerprint = hashlib.sha256()
    with np.load(path, allow_pickle=False) as data:
        for name in ('positions', 'radii', 'demand'):
            array = np.ascontiguousarray(data[name], dtype='<f8')
            fingerprint.update(name.encode())
            fingerprint.update(json.dumps(array.shape).encode())
            fingerprint.update(array.tobytes())
    return fingerprint.hexdigest()


def check_content(study, inventory_path, output):
    study = Path(study).resolve()
    inventory_path = Path(inventory_path).resolve()
    inventory = json.loads(inventory_path.read_text(encoding='utf-8'))
    if not inventory['namespace_available_in_indexed_local_manifests']:
        raise ValueError('Input namespace did not pass exposure inventory')
    checked_paths, old_hashes, missing = set(), set(), set()
    for row in inventory['inventory']:
        manifest = Path(row['path'])
        payload = manifest.read_bytes()
        if hashlib.sha256(payload).hexdigest() != row['sha256']:
            raise ValueError('A prior manifest changed after exposure inventory')
        for reference in _truth_references(json.loads(payload)):
            path = (manifest.parent / reference).resolve()
            if path in checked_paths:
                continue
            checked_paths.add(path)
            if not path.is_file():
                missing.add(str(path))
                continue
            old_hashes.add(_people_hash(path))
    manifest_path = study / 'manifest.json'
    payload = manifest_path.read_bytes()
    manifest = json.loads(payload)
    if manifest['design']['namespace'] != inventory['proposed_namespace']:
        raise ValueError('Prepared study differs from inventoried namespace')
    new_hashes, overlaps = [], []
    for unit in manifest['units']:
        path = study / unit['truth']
        if hashlib.sha256(path.read_bytes()).hexdigest() != unit['truth_sha256']:
            raise ValueError('New frozen input changed')
        fingerprint = _people_hash(path)
        new_hashes.append(fingerprint)
        if fingerprint in old_hashes:
            overlaps.append(unit['unit_id'])
    result = {'study_manifest_sha256': hashlib.sha256(payload).hexdigest(),
        'inventory_sha256': hashlib.sha256(inventory_path.read_bytes()).hexdigest(),
        'new_physical_inputs': len(new_hashes), 'new_unique_people_fingerprints': len(set(new_hashes)),
        'indexed_old_unique_people_fingerprints': len(old_hashes), 'old_people_overlaps': overlaps,
        'all_new_physical_inputs_unique': len(new_hashes) == len(set(new_hashes)) and not overlaps,
        'missing_indexed_truth_paths': sorted(missing),
        'scope': 'available truth references in inventoried local manifests; absent or unindexed historical data remain outside this check',
        'method_outcomes_generated': False}
    write_json(output, result)
    if not result['all_new_physical_inputs_unique']:
        raise ValueError('Prepared physical inputs overlap known inputs or each other')
    return result


def audit(root, namespace, seed_start, seed_count, output):
    root = Path(root).resolve()
    seeds = list(range(seed_start, seed_start + seed_count))
    if seed_count <= 0 or any(5000 <= seed <= 5029 for seed in seeds):
        raise ValueError('Positive sample count and no reserved seeds required')
    backend = 'rg'
    try:
        command = subprocess.run(['rg', '--files', '--no-ignore', str(root), '-g', 'manifest.json'],
                                 capture_output=True, text=True, encoding='utf-8', check=False)
        if command.returncode not in (0, 1):
            raise RuntimeError('Manifest inventory failed: ' + command.stderr)
        names = command.stdout.splitlines()
    except OSError:
        # Some Windows app PATH shims are invocable by PowerShell but cannot
        # be executed as native child processes. Preserve complete inventory
        # semantics using a walk that raises on unreadable directories.
        backend = 'python_walk_after_rg_unavailable'
        def on_error(error):
            raise error
        names = [str(Path(folder) / 'manifest.json') for folder, _, files in os.walk(root, onerror=on_error)
                 if 'manifest.json' in files]
    inventory, collisions = [], []
    for name in sorted(names):
        path = Path(name)
        payload = path.read_bytes()
        value = json.loads(payload)
        present = sorted(namespaces(value))
        row = {'path': str(path), 'sha256': hashlib.sha256(payload).hexdigest(), 'namespaces': present}
        inventory.append(row)
        if namespace in present:
            collisions.append(row['path'])
    result = {'local_manifest_inventory_complete': True, 'inventory_backend': backend, 'manifest_count': len(inventory),
        'proposed_namespace': namespace, 'proposed_seeds': seeds, 'reserved_seeds_used': False,
        'namespace_already_exposed': collisions, 'namespace_available_in_indexed_local_manifests': not collisions,
        'inventory': inventory, 'scope': 'local indexed manifests only; not other machines or all unindexed history',
        'input_content_overlap_checked': False, 'method_outcomes_generated': False}
    write_json(output, result)
    if collisions:
        raise ValueError('Proposed namespace was already exposed in local manifests')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root')
    parser.add_argument('--namespace', type=int)
    parser.add_argument('--seed-start', type=int)
    parser.add_argument('--seed-count', type=int)
    parser.add_argument('--study')
    parser.add_argument('--inventory')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = (check_content(args.study, args.inventory, args.output) if args.study else
              audit(args.root, args.namespace, args.seed_start, args.seed_count, args.output))
    print(json.dumps({k: v for k, v in result.items() if k != 'inventory'}, ensure_ascii=False))
