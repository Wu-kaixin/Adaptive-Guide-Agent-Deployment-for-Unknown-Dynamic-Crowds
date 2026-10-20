"""Frozen paired nonconvex demand pilot; reuse planning, execution and acceptance.

No controller or boundary algorithm lives here. Freeze all inputs and windows
before planning; never retry a completed row or extend a failed row's budget.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter
import traceback

import numpy as np
from shapely.geometry import Polygon
import yaml
from threadpoolctl import threadpool_info, threadpool_limits

from udc.boundary import construct_boundary
from udc.deployment.admissible import admissible_phase
from udc.deployment.geometry import pair_geometry
from udc.deployment.planners import (coverage_statistics, demand_profile, feasibility_bounds,
    infeasibility_certificate, periodic_gaps, plan_coverage, quadrature_mass)
from udc.deployment.risk_aware import plan_projected_mass
from udc.environment import Environment
from udc.evaluation.comparison import common_motion_metrics
from udc.evaluation.coverage import excess_coverage, _log_gaussian_mass, excess_weights
from udc.experiments.boundary_comparison import input_quality
from udc.experiments.nonconvex_rebuild import (array_hash, draw_cell, load_boundary,
    load_protocol, rejected_result, sha, write, _official_monitor)
from udc.experiments.provenance import provenance
from udc.experiments.runner import experiment, reevaluate_result, _atomic_npz, _atomic_text, STATUSES
from udc.experiments.scene_closure import _execution_config
from udc.experiments.target_domain import save_plan, independent_geometry
from udc.navigation import navigation_domain, navigate_and_assign
from udc.navigation.paths import target_domain_check
from udc.safety import Safety


SPEC = 'configs/step1/nonconvex_demand_pilot_v1.yaml'
OUTPUT = 'runs/step1_nonconvex_demand_pilot_v1'
UNITS = [['elongated', 36], ['elongated', 51], ['tight_u', 36],
         ['tight_u', 59], ['wide_u', 35], ['wide_u', 36]]
METHODS = ['equal_arc', 'mass_cvt', 'projected_mass']


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def arrays(path):
    with np.load(path, allow_pickle=False) as data:
        return {key: data[key].copy() for key in data.files}


def physical_hash(data):
    return array_hash(**{key: data[key] for key in ('people', 'radii', 'guides', 'room')})


def frozen_density(boundary, data, demand, mask, spec, multiplier=None):
    """One raw Gaussian field and target-independent excess window for all methods."""
    density = spec['density']
    bandwidth, samples = density['gaussian_bandwidth_m'], density['quadrature_samples']
    rho = demand_profile(boundary, data['people'], demand, bandwidth, 'mass_cvt')
    q, mass, length = quadrature_mass(boundary, rho, samples)
    log_total = _log_gaussian_mass(boundary.deployment, data['people'], demand, bandwidth)
    shift = float(log_total.max())
    excess, applicable = excess_weights(demand, mask, multiplier)
    excess_rho = np.zeros_like(rho)
    if applicable:
        excess_rho = np.exp(np.clip(_log_gaussian_mass(boundary.deployment,
            data['people'], excess, bandwidth) - shift, -745., 0.))
    excess_mass = quadrature_mass(boundary, excess_rho, samples)[1]
    fraction = np.divide(excess_mass, mass, out=np.zeros_like(mass), where=mass > 0)
    window = fraction >= .5 if applicable else np.zeros(len(q), dtype=bool)
    payload = dict(demand=demand, hotspot_mask=mask, rho=rho, quadrature=q, mass=mass,
        excess_rho=excess_rho, excess_mass=excess_mass,
        background_mass=np.maximum(mass-excess_mass, 0.), excess_window=window)
    record = {'bandwidth_m': bandwidth, 'samples': samples, 'curve_length_m': length,
        'kernel': 'exp(-squared_euclidean_distance/(2*h*h)); sum over people weighted by demand',
        'log_scale_shift': shift, 'scale': 'one curve-wide maximum, no per-node ratio',
        'quadrature': 'periodic linear rho interpolation including seam; 2048 midpoint nodes',
        'integral': float(mass.sum()), 'normalization': 'sum(mass*d_L^2)/sum(mass), m^2',
        'minimum_rho': float(rho.min()), 'maximum_rho': float(rho.max()),
        'excess_applicable': applicable, 'window_nodes': int(window.sum()),
        'window_rule': 'excess mass >= half local total mass; fixed before targets',
        'array_sha256': array_hash(**payload)}
    return payload, record


def freeze(output, spec_path=SPEC):
    root = Path(output).resolve()
    if root.exists():
        raise FileExistsError('Use a new output directory; frozen evidence is immutable')
    spec = yaml.safe_load(Path(spec_path).read_text(encoding='utf-8'))
    if (spec['units'] != UNITS or spec['methods'] != METHODS or spec['demands'] != ['ones', 'hotspot5']
            or spec['guide_counts'] != {'elongated': 11, 'tight_u': 13, 'wide_u': 19}):
        raise ValueError('Changed factors require another protocol revision')
    generator, base = load_protocol(spec['generator_spec'])
    root.mkdir(parents=True)
    sources, conditions, rows = {}, {}, []
    files = {str(Path(p).resolve()): sha(p) for p in
             (spec_path, spec['generator_spec'], generator['base_spec'], 'requirements.txt')}
    for scene, seed in spec['units']:
        key = f'{scene}__seed_{seed}'
        source = Path(spec['snapshot_root']).resolve() / scene / f'seed_{seed}'
        origin = 'existing_original_v3_frozen_snapshot'
        if not (source / 'input.npz').exists():
            # Only the requested missing cell is generated, with no rejection loop.
            source = root / 'reconstructed_inputs' / scene / f'seed_{seed}'
            source.mkdir(parents=True)
            scene_spec = next(s for s in base['scenes'] if s['name'] == scene)
            data, streams = draw_cell(generator, base, scene_spec, seed)
            _atomic_npz(source / 'input.npz', **data)
            _execution_config(source, data['people'], np.array([[0,0],[30,0],[30,30],[0,30]]),
                              spec['guide_counts'][scene], seed, base)
            write(source / 'reconstruction.json', {'origin': 'protocol_reconstruction_not_original_recovery',
                'streams': streams, 'generator_spec_sha256': sha(spec['generator_spec'])})
            origin = 'protocol_reconstruction_not_original_recovery'
        data = arrays(source / 'input.npz')
        old_record = read(source / 'input_record.json') if (source / 'input_record.json').exists() else None
        input_digest = array_hash(**{k: data[k] for k in ('people','radii','demand','guides','room')})
        if old_record and (sha(source / 'input.npz') != old_record['input_file_sha256']
                           or input_digest != old_record['input_sha256']):
            raise RuntimeError('Historical input failed its saved hash')
        historical_match = None
        if scene == 'tight_u' and seed == 59:
            historic = next(r for r in read('reports/step1_boundary_estimator_comparison_v1/input_map_60.json')
                            if r['unit'] == key)
            historical_match = {'array_hash_match': input_digest == historic['input_sha256'],
                'each_array_byte_match': all(hashlib.sha256(np.ascontiguousarray(data[k]).tobytes()).hexdigest() == v
                                           for k,v in historic['array_byte_sha256'].items()),
                'historical_input_sha256': historic['input_sha256'],
                'original_raw_file_available': origin != 'protocol_reconstruction_not_original_recovery'}
        physical = root / 'physical' / key
        physical.mkdir(parents=True)
        quality = input_quality(data)
        b, description, occupied, _ = (None, {}, None, None)
        status, reason = 'INITIALIZATION_INVALID', 'INITIALIZATION_INVALID: frozen people/room invalid'
        if quality['accepted']:
            b, description, occupied, _ = construct_boundary(spec['estimator'], data['people'],
                data['radii'], data['room'], **spec['boundary'])
            status = 'ACCEPTED' if b is not None else description['failure_reason'].split(':')[0]
            reason = '' if b is not None else description['failure_reason']
        curve_match = None
        if b is not None:
            _atomic_npz(physical / 'boundary.npz', crowd=b.crowd, deployment=b.deployment, arc=b.arc,
                        length=np.array(b.length))
            (physical / 'occupied.wkb').write_bytes(occupied.wkb)
            if (source / 'boundary.npz').exists():
                old = load_boundary(source)
                curve_match = all(np.array_equal(getattr(b,k), getattr(old,k))
                                  for k in ('crowd','deployment','arc','length'))
                if not curve_match:
                    raise RuntimeError('Current boundary differs from existing frozen curve')
        ref = {'source_cell': str(source), 'origin': origin, 'input_sha256': input_digest,
            'physical_array_sha256': physical_hash(data), 'count': spec['guide_counts'][scene],
            'people_count': len(data['people']), 'quality': quality, 'status': status, 'reason': reason,
            'boundary_description': description, 'existing_curve_exact_match': curve_match,
            'historical_hash_comparison': historical_match}
        sources[key] = ref
        write(physical / 'source.json', ref)
        # Reuse original SQLite without modifying it; reconstruction has its own.
        database = source / 'people.sqlite'
        for p in (source/'input.npz', database, physical/'source.json'):
            files[str(p)] = sha(p)
        if b is not None:
            files[str(physical/'boundary.npz')] = sha(physical/'boundary.npz')
        for demand_name in spec['demands']:
            condition_key = key + '__' + demand_name
            cell = root / 'conditions' / condition_key
            cell.mkdir(parents=True)
            region = spec['hotspots'][scene] if demand_name == 'hotspot5' else None
            mask = (np.linalg.norm(data['people']-region['center'], axis=1) <= region['radius']
                    if region else np.zeros(len(data['people']), bool))
            demand = np.where(mask, 5., 1.)
            cfg = yaml.safe_load((source / 'input.yaml').read_text(encoding='utf-8'))
            cfg['crowd']['trajectory_file'] = str(database)
            cfg['crowd']['demand'] = {'mean':1., 'std':0., 'min':1., 'max':5.}
            cfg['crowd'].pop('demand_region', None)
            if region:
                cfg['crowd']['demand_region'] = region
            cfg['guides']['initial_positions'] = data['guides'].tolist()
            cfg['controller']['method'] = 'projected_mass'
            _atomic_text(cell/'input.yaml', yaml.safe_dump(cfg, sort_keys=False))
            # Check the actual execution source before any method sees targets.
            if quality['accepted']:
                env = Environment(cfg)
                observed = env.observe()
                if not all(np.array_equal(a,v) for a,v in [(observed.positions,data['people']),
                    (observed.radii,data['radii']), (observed.demand,demand),
                    (env.initialize_guides(cfg['guides']),data['guides'])]):
                    raise RuntimeError('Execution configuration does not reproduce the frozen observation')
            payload, density_record = (dict(demand=demand, hotspot_mask=mask), None)
            if b is not None:
                payload, density_record = frozen_density(b, data, demand, mask, spec, 5. if region else None)
            _atomic_npz(cell/'demand.npz', **payload)
            record = {'physical_key':key, 'demand':demand_name, 'region':region,
                'hotspot_people_indices':np.flatnonzero(mask).tolist(), 'demand_vector':demand.tolist(),
                'density':density_record, 'physical_array_sha256':physical_hash(data),
                'observation_sha256':array_hash(people=data['people'],radii=data['radii'],demand=demand)}
            write(cell/'density.json', record)
            conditions[condition_key] = record
            for p in cell.iterdir():
                files[str(p)] = sha(p)
            for method in spec['methods']:
                rows.append({'task_id':condition_key+'__'+method, 'physical_key':key,
                    'condition_key':condition_key, 'scene':scene, 'seed':seed, 'method':method,
                    'method_label':method+spec['deployment']['label_suffix'], 'demand':demand_name,
                    'count':ref['count'], 'input_sha256':record['observation_sha256'],
                    'physical_sha256':physical_hash(data), 'input_status':status})
    write(root/'manifest.json', {'physical_input_count':6,'demand_units':12,'task_count':36,'tasks':rows})
    files[str(root/'manifest.json')] = sha(root/'manifest.json')
    write(root/'protocol.json', {'spec':spec, 'base':base, 'generator':generator,
        'sources':sources, 'conditions':conditions, 'files':files,
        'provenance':provenance({},None,'fixed_n',None), 'outputs_observed_before_freeze':False})
    print(json.dumps({'frozen_rows':36,'input_status':{k:v['status'] for k,v in sources.items()}}),flush=True)


def locked(root):
    lock = read(root/'protocol.json')
    if provenance({},None,'fixed_n',None)['source_sha256'] != lock['provenance']['source_sha256']:
        raise RuntimeError('Scientific source changed after freeze')
    for name, digest in lock['files'].items():
        if sha(name) != digest:
            raise RuntimeError('Frozen input/config changed: '+name)
    return lock


def plan_original(boundary, data, rho, count, method, spec):
    """Same finite legacy phases. Geometric failures retain a scored raw witness."""
    policy = spec['deployment']
    samples = spec['density']['quadrature_samples']
    q,mass,length = quadrature_mass(boundary, rho, samples)
    attempts, candidates = [], []
    for phase in policy['phases']:
        t = perf_counter()
        if method == 'projected_mass':
            plan = plan_projected_mass(boundary,None,None,count,None,policy['gap_limit_m'],
                policy['separation_m'],phase,samples,policy['max_iterations'],density_override=rho)
        else:
            plan = plan_coverage(boundary,None,None,count,None,method,phase,samples,
                policy['max_iterations'],density_override=rho)
        geometry = pair_geometry(boundary,plan.arc_positions,policy['gap_limit_m'],policy['separation_m'])
        cost = coverage_statistics(q,mass,plan.arc_positions,length,np.inf)['weighted_cost']
        accepted = geometry['arc_box_ok'] and geometry['euclidean_ok']
        attempts.append({'legacy_phase':phase, 'geometry':geometry, 'raw_demand_cost':cost,
            'geometry_pass':accepted, 'iterations':plan.iterations, 'exit_reason':plan.exit_reason,
            'wall_s':perf_counter()-t, 'adjustments':list(plan.adjustments)})
        candidates.append(plan)
        if accepted:
            return plan, {'attempts':attempts,'selected_attempt':len(attempts)-1,'geometry_pass':True}
    index = min(range(len(attempts)),key=lambda i:attempts[i]['raw_demand_cost'])
    return candidates[index], {'attempts':attempts,'selected_attempt':index,'geometry_pass':False,
        'scope':'rejected raw witness, not an executable plan or infeasibility proof'}


def score(boundary, data, field, positions, spec):
    region = spec['hotspots'][data['scene']] if data['demand_name']=='hotspot5' else None
    report = excess_coverage(boundary,data['people'],field['demand'],field['hotspot_mask'],
        spec['density']['gaussian_bandwidth_m'],spec['density']['quadrature_samples'],positions,
        multiplier=None if region is None else region['multiplier'])
    from udc.evaluation.coverage import arc_positions
    sites = arc_positions(boundary,positions)
    # Additional background ARC cost, distinct from the existing background MASS cost.
    outside = ~field['excess_window']
    q,mass,length = field['quadrature'],field['mass'],boundary.length
    report['background_arc_cost'] = (coverage_statistics(q,outside.astype(float),sites,length,np.inf)['weighted_cost']
                                     if outside.any() and field['excess_mass'].sum()>0 else None)
    report['background_arc_applicable'] = bool(field['excess_mass'].sum()>0)
    report['frozen_mass_cost'] = coverage_statistics(q,mass,sites,length,np.inf)['weighted_cost']
    return report


def run_row(task):
    root_string,row = task
    root = Path(root_string)
    lock = locked(root)
    spec,base = lock['spec'],lock['base']
    ref = lock['sources'][row['physical_key']]
    physical = root/'physical'/row['physical_key']
    cell = root/'conditions'/row['condition_key']
    destination = root/'results'/row['task_id']
    artifacts = root/'plans'/row['task_id']
    if destination.exists() or artifacts.exists():
        raise FileExistsError('Never overwrite or rerun a protocol row')
    artifacts.mkdir(parents=True)
    data = arrays(Path(ref['source_cell'])/'input.npz')
    field = arrays(cell/'demand.npz')
    data.update(demand=field['demand'],scene=row['scene'],demand_name=row['demand'])
    cfg = yaml.safe_load((cell/'input.yaml').read_text(encoding='utf-8'))
    _official_monitor()
    started = perf_counter()
    detail = {**row,'stage':'input','motion':None,'raw_score':None,'final_score':None,
        'actual_score':None,'phase_triggered':False,'initial_paths_ok':False,
        'source_sha256':lock['provenance']['source_sha256'],'timing':{}}
    def reject(status,reason):
        return rejected_result(destination,row,Path(ref['source_cell']),status,reason,
                               snapshot=data,config_path=cell/'input.yaml')
    try:
        with threadpool_limits(limits=1):
            detail['worker'] = {'pid':os.getpid(),'priority_stall_counterfactual':os.environ['UDC_PRIORITY_STALL_COUNTERFACTUAL'],
                'numerical_pools':threadpool_info(), 'thread_environment':{k:os.environ.get(k) for k in
                ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS')}}
            if ref['status'] != 'ACCEPTED':
                raise ValueError(ref['reason'])
            boundary = load_boundary(physical)
            domain = navigation_domain(data['room'],.55,Polygon(boundary.crowd),.55)
            detail['domain_sha256'] = domain.geometry_sha256
            detail['stage'] = 'resource'
            bounds = feasibility_bounds(boundary.length,1.94,.75)
            certificate = infeasibility_certificate(bounds,row['count'])
            detail['resource'] = {**bounds,'certificate':certificate,'fixed_count':row['count']}
            if certificate:
                raise ValueError('CAPACITY_SHORTFALL: '+certificate)
            detail['stage'] = 'target_geometry'
            t = perf_counter()
            original, solver = plan_original(boundary,data,field['rho'],row['count'],row['method'],spec)
            detail['timing']['planning_s'] = perf_counter()-t
            save_plan(artifacts/'original',original,solver)
            _atomic_npz(artifacts/'original_geometry.npz',
                all_pair_distances=np.linalg.norm(original.positions[:,None]-original.positions[None,:],axis=2),
                gaps=periodic_gaps(original.arc_positions,boundary.length))
            detail['raw_score'] = score(boundary,data,field,original.positions,spec)
            detail['original_geometry'] = solver['attempts'][solver['selected_attempt']]['geometry']
            detail['original_domain'] = target_domain_check(domain,original.positions,original.arc_positions)
            if not solver['geometry_pass']:
                raise ValueError('PLAN_SEARCH_EXHAUSTED: all four legacy phases fail target geometry; raw witness saved')
            detail['stage'] = 'static_domain'
            detail['phase_triggered'] = not detail['original_domain']['accepted']
            t = perf_counter()
            candidate, phase = admissible_phase(boundary,original,domain,field['rho'],
                phase_bins=spec['deployment']['phase_bins'],gap_limit=1.94,separation=.75,
                quadrature_samples=spec['density']['quadrature_samples'])
            detail['timing']['phase_s'] = perf_counter()-t
            write(artifacts/'phase.json',phase)
            detail['phase'] = {k:v for k,v in phase.items() if k not in ('attempts','original')}
            if candidate is None:
                raise ValueError('PLAN_SEARCH_EXHAUSTED: fixed 256-phase domain policy exhausted, no infeasibility proof')
            save_plan(artifacts/'final',candidate,phase)
            geometry = independent_geometry(boundary,original,candidate,domain,base)
            detail['final_geometry'] = geometry
            if not geometry['accepted']:
                raise RuntimeError('Saved-coordinate geometry check disagrees with planner')
            distances = np.linalg.norm(candidate.positions[:,None]-candidate.positions[None,:],axis=2)
            _atomic_npz(artifacts/'geometry.npz',all_pair_distances=distances,
                        gaps=periodic_gaps(candidate.arc_positions,boundary.length))
            detail['final_score'] = score(boundary,data,field,candidate.positions,spec)
            detail['stage'] = 'initial_navigation'
            t = perf_counter()
            nav = navigate_and_assign(data['guides'],candidate.positions,data['room'],.55,Polygon(boundary.crowd),.55)
            detail['timing']['initial_navigation_s'] = perf_counter()-t
            _atomic_npz(artifacts/'assignment.npz',assignment=nav.assignment,lengths=nav.lengths,reachable=nav.reachable)
            write(artifacts/'initial_paths.json',{'paths':[p.tolist() for p in nav.paths]})
            detail['initial_paths_ok'] = True
            detail['stage'] = 'execution'
            result = experiment(cell/'input.yaml',destination,plots=False,protocol='fixed_n',fixed_n=row['count'],
                prescribed_boundary=boundary,prescribed_plan=candidate,
                boundary_source='estimated_disk_union',method_label=row['method_label'])
            actual = arrays(destination/'trajectory.npz')
            pairs = [('crowd_positions','people'),('crowd_radii','radii'),('crowd_demand','demand'),('room','room')]
            detail['exact_pairing'] = (all(np.array_equal(actual[a],data[b]) for a,b in pairs)
                and np.array_equal(actual['positions'][0],data['guides'])
                and np.array_equal(actual['targets'],candidate.positions)
                and np.array_equal(actual['crowd_hotspot_mask'],field['hotspot_mask']))
            if not detail['exact_pairing']:
                raise RuntimeError('Actual observation/initial guides/targets differ from freeze')
            detail['motion'] = common_motion_metrics(actual['positions'],actual['safe_velocity'],data['people'],
                data['radii'],data['room'],actual['targets'],actual['assignment'],cfg['controller'],
                Safety(**result['safety_limits']),cfg['simulation']['dt'])
            if detail['motion'] is not None:
                detail['actual_score'] = score(boundary,data,field,actual['positions'][-1],spec)
    except Exception as exc:
        status = str(exc).split(':')[0] if isinstance(exc,ValueError) else None
        if status in STATUSES:
            result = reject(status,str(exc))
            detail['failure_evidence'] = getattr(exc,'diagnosis',None)
        else:
            if not destination.exists():
                result = reject('EXPERIMENT_ERROR','EXPERIMENT_ERROR: see runner_error.txt')
            else:
                result = read(destination/'metrics.json')
                result.update(termination_status='EXPERIMENT_ERROR',success=False)
                write(destination/'metrics.json',result)
            _atomic_text(destination/'runner_error.txt',traceback.format_exc())
            write(destination/'state.json',{'status':'error','success':False})
    offline = reevaluate_result(destination)
    detail.update(termination_status=result['termination_status'],success=result['success'],
        replan_count=result.get('replan_count') if detail['motion'] else None,
        offline_consistent=offline['criteria_consistent'],
        offline_diagnostics_consistent=all(offline['diagnostics_consistent'].values()),
        failure_reason=result.get('failure_reason'),wall_s=perf_counter()-started)
    write(destination/'protocol_metrics.json',detail)
    return detail


def run(output, stage, workers):
    root = Path(output).resolve()
    lock = locked(root)
    if not 1<=workers<=2 or (stage=='first' and workers!=1):
        raise ValueError('First six rows serial; subsequent workers <= 2')
    rows = read(root/'manifest.json')['tasks']
    first_key = rows[0]['physical_key']
    if stage=='remaining':
        gate = read(root/'first_gate.json')
        if not gate['passed']:
            raise RuntimeError('First six rows failed the density/pairing/artifact gate')
    rows = [r for r in rows if (r['physical_key']==first_key)==(stage=='first')]
    if (root/(stage+'_freeze.json')).exists():
        raise FileExistsError('Stage already attempted')
    _official_monitor()
    for flag in ('UDC_COMPLETE_SEGMENT','UDC_VERTEX_ARRIVAL'):
        if os.environ.get(flag,'1')!='1':
            raise RuntimeError('Frozen segment/vertex behavior must stay enabled')
    os.environ.update(OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1')
    write(root/(stage+'_freeze.json'), {'workers':workers,'numerical_threads':1,
        'priority_stall_counterfactual':os.environ['UDC_PRIORITY_STALL_COUNTERFACTUAL'],
        'source_sha256':lock['provenance']['source_sha256'],'task_ids':[r['task_id'] for r in rows]})
    t = perf_counter()
    tasks = [(str(root),r) for r in rows]
    if workers==1:
        results = []
        for task in tasks:
            result = run_row(task)
            results.append(result)
            print(json.dumps({'task':result['task_id'],'status':result['termination_status']}),flush=True)
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            results = []
            for result in pool.map(run_row,tasks):
                results.append(result)
                print(json.dumps({'task':result['task_id'],'status':result['termination_status']}),flush=True)
    write(root/(stage+'_summary.json'),{'rows':results,'wall_s':perf_counter()-t,
        'outcomes':dict(Counter(r['termination_status'] for r in results))})
    if stage=='first':
        passed = len(results)==6 and all(r['offline_consistent'] and r['offline_diagnostics_consistent']
            and r.get('exact_pairing',True) and r['termination_status']!='EXPERIMENT_ERROR' for r in results)
        # Independent score from persisted fields must match the existing evaluator.
        for r in results:
            for label in ('raw_score','final_score'):
                s = r[label]
                if s and not np.isclose(s['frozen_mass_cost'],s['planned_demand_weighted_cost'],rtol=1e-12,atol=1e-12):
                    passed = False
        write(root/'first_gate.json',{'passed':passed,'counted_in_manifest':True,'rows':len(results),
            'checks':['frozen field versus independent evaluator','exact runtime pairing',
                      'all failure artifacts and offline acceptance','no program errors']})
        if not passed:
            raise RuntimeError('First-six gate failed; retain all evidence and stop')
    print(json.dumps({'stage':stage,'rows':len(results),'outcomes':dict(Counter(r['termination_status'] for r in results))}),flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',default=OUTPUT)
    p.add_argument('--spec',default=SPEC)
    p.add_argument('--stage',choices=['freeze','first','remaining'],required=True)
    p.add_argument('--workers',type=int,default=1)
    args = p.parse_args()
    if args.stage=='freeze':
        freeze(args.output,args.spec)
    else:
        run(args.output,args.stage,args.workers)


if __name__=='__main__':
    main()
