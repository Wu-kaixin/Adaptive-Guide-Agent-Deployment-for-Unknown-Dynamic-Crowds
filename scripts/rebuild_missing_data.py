"""Maintenance orchestration for explicitly labelled, separate data reconstruction.

Uses the existing scientific generators, planners, runner and independent judge.
Never writes into historical runs or changes a historical hash lock.
"""
from pathlib import Path
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import time
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT / 'runs/reconstructed_20261023'
SUITES = {
    'record_v1': ('nonconvex_record_rebuild_v1', 'nonconvex', '2c12886c'),
    'confirmation_v2': ('nonconvex_confirmation_v2', 'nonconvex', 'a0781c56'),
    'confirmation_v3': ('nonconvex_execution_confirmation_v3', 'nonconvex', '9427a2e9'),
    'demand_pilot': ('nonconvex_demand_pilot_v1', 'pilot', '4b2b199f'),
    'demand_confirmation': ('nonconvex_demand_confirmation_v1', 'all_phase', '125fd653'),
    'resource_response': ('nonconvex_resource_response_v1', 'all_phase', '125fd653'),
    'resource_confirmation': ('nonconvex_resource_confirmation_v1', 'all_phase', '0433bb19'),
    'recovery_regression': ('resource_recovery_diagnostics_v1', 'all_phase', '865e6a48'),
}

def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temp.replace(path)

def read(path): return json.loads(path.read_text(encoding='utf-8'))
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def snapshot(ref):
    target = OUTPUT / 'sources' / ref
    if not target.exists():
        raw = subprocess.check_output(['git', 'archive', '--format=zip', ref, 'src/udc', 'pyproject.toml'], cwd=PROJECT)
        target.mkdir(parents=True)
        with zipfile.ZipFile(io.BytesIO(raw)) as z: z.extractall(target)
        write(target / 'source_snapshot.json', {'git_ref': ref, 'commit': subprocess.check_output(['git', 'rev-parse', ref], cwd=PROJECT, text=True).strip(),
              'archive_sha256': hashlib.sha256(raw).hexdigest(), 'files': {p.relative_to(target).as_posix(): sha(p) for p in target.rglob('*') if p.is_file()},
              'checkout_line_endings': 'Git archive LF; not claimed identical to original dirty Windows checkout'})
    return target

def initialize(ref):
    for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        os.environ[key] = '1'
    os.environ['UDC_PRIORITY_STALL_COUNTERFACTUAL'] = '0'
    sys.path.insert(0, str(OUTPUT / 'sources' / ref / 'src'))
    os.chdir(PROJECT)

def freeze_all_phase(root, suite):
    import numpy as np
    import yaml
    from shapely.geometry import Polygon
    from udc.boundary import construct_boundary
    from udc.experiments import demand_confirmation as dc
    from udc.experiments.demand_pilot import frozen_density, physical_hash
    from udc.experiments.nonconvex_rebuild import draw_cell, array_hash
    from udc.experiments.resource_response import extend_guides, quantity
    from udc.experiments.runner import _atomic_npz, _atomic_text
    from udc.experiments.scene_closure import _execution_config
    from udc.experiments.provenance import provenance
    from udc.experiments.boundary_comparison import input_quality
    from udc.navigation import navigation_domain
    from udc.safety import Safety, path_clearances, is_safe
    spec, pilot, phase, generator, base = dc.dependencies()
    seeds = list(range(95, 115)) if suite == 'resource_confirmation' else list(range(75, 95))
    demands = ['ones', 'hotspot5'] if suite == 'demand_confirmation' else ['hotspot5']
    increments = [0] if suite == 'demand_confirmation' else [0, 2, 4]
    assert all(0 <= n < 5000 for n in seeds)
    root.mkdir(parents=True)
    lock = {'spec': spec, 'pilot': pilot, 'phase': phase, 'generator': generator, 'base': base,
            'provenance': provenance({}, None, 'fixed_n', None), 'reconstruction': True,
            'independent_confirmation': False, 'seeds_previously_exposed': True}
    sources, starts, conditions, rows, diagnostics = {}, {}, {}, [], []
    for seed in seeds:
        for scene in dc.SCENES:
            key = f'{scene}__seed_{seed}'; n0 = pilot['guide_counts'][scene]
            cell = root / 'physical' / key; cell.mkdir(parents=True)
            data, streams = draw_cell(generator, base, next(s for s in base['scenes'] if s['name'] == scene), seed)
            _atomic_npz(cell / 'input.npz', **data)
            room = np.array([[0., 0.], [30., 0.], [30., 30.], [0., 30.]])
            cfgpath = _execution_config(cell, data['people'], room, n0, seed, base)
            cfg = yaml.safe_load(cfgpath.read_text(encoding='utf-8')); cfg['guides']['initial_positions'] = data['guides'].tolist()
            _atomic_text(cfgpath, yaml.safe_dump(cfg, sort_keys=False))
            quality = input_quality(data); b = None; desc = None
            status, reason = 'INITIALIZATION_INVALID', 'INITIALIZATION_INVALID: one-draw personnel/room constraints failed'
            if quality['accepted']:
                b, desc, occupied, _ = construct_boundary('disk_union_closed', data['people'], data['radii'], data['room'], **pilot['boundary'])
                if occupied is not None: (cell / 'occupied.wkb').write_bytes(occupied.wkb)
                status = 'ACCEPTED' if b is not None else desc['failure_reason'].split(':')[0]
                reason = '' if b is not None else desc['failure_reason']
            if b is not None:
                _atomic_npz(cell / 'boundary.npz', crowd=b.crowd, deployment=b.deployment, arc=b.arc, length=np.array(b.length))
                domain = navigation_domain(data['room'], .55, Polygon(b.crowd), .55)
                for name in ('free', 'physical_free', 'physical_obstacle', 'conservative_obstacle'):
                    (cell / (name + '.wkb')).write_bytes(getattr(domain, name).wkb)
                write(cell / 'static_domain.json', {'geometry_sha256': domain.geometry_sha256})
            source = {'scene': scene, 'seed': seed, 'count': n0, 'physical_sha256': physical_hash(data), 'people_quality': quality,
                      'status': status, 'reason': reason, 'streams': streams, 'boundary_description': desc,
                      'input_sha256': sha(cell / 'input.npz'), 'boundary_sha256': sha(cell / 'boundary.npz') if b is not None else None}
            write(cell / 'input_record.json', source); sources[key] = source
            extended, arcs, additions = extend_guides(data, source)
            diagnostics.append({'physical_key': key, **quantity(b.length if b is not None else None, n0 - 1)})
            for demand_name in demands:
                cid = key + '__' + demand_name; condition = root / 'conditions' / cid; condition.mkdir(parents=True)
                region = pilot['hotspots'][scene] if demand_name == 'hotspot5' else None
                mask = np.linalg.norm(data['people'] - region['center'], axis=1) <= region['radius'] if region else np.zeros(len(data['people']), bool)
                demand = np.where(mask, 5., 1.)
                ccfg = {**cfg, 'crowd': dict(cfg['crowd'], demand={'mean': 1., 'std': 0., 'min': 1., 'max': 5.})}
                ccfg['crowd'].pop('demand_region', None)
                if region: ccfg['crowd']['demand_region'] = region
                _atomic_text(condition / 'input.yaml', yaml.safe_dump(ccfg, sort_keys=False))
                payload, density = ({'demand': demand, 'hotspot_mask': mask}, None)
                if b is not None: payload, density = frozen_density(b, data, demand, mask, pilot, 5. if region else None)
                _atomic_npz(condition / 'demand.npz', **payload)
                observation_hash = array_hash(people=data['people'], radii=data['radii'], demand=demand)
                conditions[cid] = {'density': density, 'observation_sha256': observation_hash}
                write(condition / 'condition.json', conditions[cid])
                for inc in increments:
                    count = n0 + inc; ikey = cid + f'__N_{count}'; initial = root / 'initials' / ikey; initial.mkdir(parents=True)
                    guides = extended[:count]; guidefile = initial / 'guides.npz'
                    _atomic_npz(guidefile, guides=guides, ids=np.arange(count), arcs=arcs[:count])
                    ncfg = {**ccfg, 'guides': dict(ccfg['guides'], count=count, initial_positions=guides.tolist())}
                    ncfgpath = initial / 'input.yaml'; _atomic_text(ncfgpath, yaml.safe_dump(ncfg, sort_keys=False))
                    safe = is_safe(path_clearances(guides, guides, data['people'], data['room']), Safety(.75, .75, .55, 1., 1e-7))
                    starts[ikey] = {'physical': str(cell), 'condition': str(condition), 'config': str(ncfgpath), 'guides': str(guidefile), 'initialization_accepted': safe}
                    write(initial / 'mapping.json', {**starts[ikey], 'appended': additions[:inc]})
                    for method in dc.METHODS:
                        tid = f'{scene}__seed_{seed}__{demand_name}__N_{count}__{method}'
                        rows.append({'task_id': tid, 'physical_key': key, 'condition_key': cid, 'initial_key': ikey, 'scene': scene, 'seed': seed,
                                     'increment': inc, 'count': count, 'method': method, 'method_label': method + '+all_phase_256', 'demand': demand_name,
                                     'input_sha256': observation_hash, 'physical_sha256': source['physical_sha256'], 'density_sha256': sha(condition / 'demand.npz')})
    files = {str(p.resolve()): sha(p) for folder in ('physical', 'conditions', 'initials') for p in (root / folder).rglob('*') if p.is_file()}
    write(root / 'protocol.json', lock)
    write(root / 'inputs_frozen.json', {'sources': sources, 'conditions': conditions, 'starts': starts, 'files': files, 'replacement_draws': 0})
    write(root / 'quantity_diagnostics.json', diagnostics)
    write(root / 'manifest.json', {'rows': rows, 'physical_units': len(sources), 'task_count': len(rows)})

def worker(task):
    suite, root_string, row = task
    root = Path(root_string); kind = SUITES[suite][1]
    if kind == 'nonconvex':
        from udc.experiments.nonconvex_rebuild import run_task
        result = run_task((root_string, row, 'baseline'))
    elif kind == 'pilot':
        from udc.experiments.demand_pilot import run_row
        result = run_row((root_string, row))
    else:
        from udc.experiments.demand_confirmation import run_row
        frozen = read(root / 'inputs_frozen.json'); initial = frozen['starts'][row['initial_key']]
        context = {'lock': read(root / 'protocol.json'), 'frozen': frozen, **initial}
        result = run_row((root_string, row), context=context)
    return {'task_id': row['task_id'], 'status': result.get('status', result.get('termination_status')),
            'success': result.get('success', False), 'offline_consistent': result.get('offline_consistent', True)}

def run_suite(suite, workers):
    import yaml
    from udc.experiments.provenance import provenance
    name, kind, ref = SUITES[suite]; root = OUTPUT / ('step1_' + name)
    started = time.perf_counter()
    if not (root / 'reconstruction.json').exists():
        if root.exists(): raise RuntimeError('Incomplete freeze needs investigation; refusing to overwrite ' + str(root))
        if kind == 'nonconvex':
            from udc.experiments.nonconvex_rebuild import freeze
            freeze(PROJECT / f'configs/step1/{name}.yaml', root)
        elif kind == 'pilot':
            from udc.experiments.demand_pilot import freeze
            derived = yaml.safe_load((PROJECT / f'configs/step1/{name}.yaml').read_text(encoding='utf-8'))
            # Force separate reconstructed input provenance, including the pilot-only seed59.
            derived['snapshot_root'] = str(OUTPUT / 'pilot_input_lookup')
            derived_path = OUTPUT / 'derived_configs/pilot.yaml'; derived_path.parent.mkdir(parents=True, exist_ok=True)
            derived_path.write_text(yaml.safe_dump(derived, sort_keys=False), encoding='utf-8')
            freeze(root, derived_path)
        else:
            freeze_all_phase(root, suite)
        write(root / 'reconstruction.json', {'label': '2026-10-23 reconstructed development data', 'historical_original': False,
              'independent_confirmation': False, 'suite': suite, 'frozen_code_ref': ref, 'source_snapshot': str(OUTPUT / 'sources' / ref),
              'historical_dirty_source_exact_recovery': False, 'actual_provenance': provenance({}, None, 'fixed_n', None),
              'driver_sha256': sha(Path(__file__)), 'constraints': 'same frozen seeds, geometry, safety limits, no rejection redraw, no validation5000-5029'})
    meta = read(root / 'reconstruction.json')
    if meta['driver_sha256'] != sha(Path(__file__)): raise RuntimeError('Driver changed after freeze')
    manifest = read(root / 'manifest.json'); rows = manifest.get('rows', manifest.get('tasks'))
    prior = read(root / 'checkpoint.json')['rows'] if (root / 'checkpoint.json').exists() else []
    completed = {r['task_id'] for r in prior}; pending = [r for r in rows if r['task_id'] not in completed]
    print(json.dumps({'suite': suite, 'tasks': len(rows), 'pending': len(pending), 'source': ref}), flush=True)
    # A single first row is reviewed before process-parallel episode execution.
    def accept(result):
        prior.append(result); write(root / 'checkpoint.json', {'rows': prior})
        print(json.dumps({'suite': suite, 'completed': len(prior), **result}), flush=True)
        if result['status'] in ('EXPERIMENT_ERROR', 'SAFETY_VIOLATION') or result['offline_consistent'] is False:
            raise RuntimeError('Reconstruction gate failed; artifacts retained: ' + str(result))
    if pending:
        accept(worker((suite, str(root), pending.pop(0))))
    with ProcessPoolExecutor(max_workers=workers, initializer=initialize, initargs=(ref,)) as pool:
        futures = [pool.submit(worker, (suite, str(root), row)) for row in pending]
        for future in as_completed(futures): accept(future.result())
    summary = {'suite': suite, 'tasks': len(rows), 'completed': len(prior), 'statuses': dict(Counter(r['status'] for r in prior)),
               'successes': sum(r['success'] for r in prior), 'wall_s': time.perf_counter() - started, 'workers': workers,
               'original_historical_data': False, 'independent_confirmation': False, 'rows': prior}
    write(root / 'reconstruction_summary.json', summary)
    print(json.dumps({k: v for k, v in summary.items() if k != 'rows'}), flush=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=list(SUITES) + ['all'], default='all')
    parser.add_argument('--workers', type=int, default=2)
    args = parser.parse_args()
    if args.workers not in (1, 2): raise ValueError('Frozen episode protocols cap workers at two')
    if args.suite == 'all':
        OUTPUT.mkdir(parents=True, exist_ok=True)
        for suite, (_, _, ref) in SUITES.items():
            snapshot(ref)
            subprocess.run([sys.executable, str(Path(__file__).resolve()), '--suite', suite, '--workers', str(args.workers)], cwd=PROJECT, check=True)
        return
    ref = SUITES[args.suite][2]; snapshot(ref); initialize(ref)
    run_suite(args.suite, args.workers)

if __name__ == '__main__': main()
