"""Frozen-input nonconvex Step-1 reconstruction using the existing experiment stack.

No old host snapshot is reconstructed. Each cell is drawn once and shared by its
three methods. Targets are planned once, then reused verbatim by regressions.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import traceback
from time import perf_counter

import numpy as np
import yaml
from shapely.geometry import Polygon
from threadpoolctl import threadpool_limits

from udc.boundary import Boundary, estimate_disk_union
from udc.deployment import Plan
from udc.deployment.constrained import optimize_pairwise
from udc.deployment.geometry import pair_geometry
from udc.deployment.planners import plan_coverage, quadrature_mass
from udc.deployment.risk_aware import plan_projected_mass
from udc.experiments.provenance import provenance
from udc.experiments.runner import experiment, reevaluate_result, _atomic_npz, _atomic_text, _empty_criteria
from udc.experiments.scene_closure import scene_people, _execution_config, _people_report


def write(path, value):
    _atomic_text(path, json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def array_hash(**arrays):
    digest = hashlib.sha256()
    for name, array in sorted(arrays.items()):
        array = np.ascontiguousarray(array)
        digest.update(name.encode())
        digest.update(str(array.dtype).encode())
        digest.update(str(array.shape).encode())
        digest.update(array.tobytes())
    return digest.hexdigest()


def load_protocol(path):
    path = Path(path)
    spec = yaml.safe_load(path.read_text(encoding='utf-8'))
    protocols={'nonconvex_record_rebuild_v1':list(range(20,25)),
               'nonconvex_confirmation_v2':list(range(25,35))}
    if spec['protocol'] not in protocols or spec['seeds']!=protocols[spec['protocol']]:
        raise ValueError('Unsupported or changed frozen nonconvex protocol seeds')
    base_path = Path(spec['base_spec'])
    base = yaml.safe_load(base_path.read_text(encoding='utf-8'))
    spec['protocol_sha256'] = sha(path)
    spec['base_spec_sha256'] = sha(base_path)
    return spec, base


def draw_cell(spec, base, scene, seed):
    """Separate streams; no rejection loop and no use of planner outcomes."""
    scene_index = spec['scenes'].index(scene['name'])
    stream = np.random.SeedSequence([spec['random_namespace'], scene_index, seed]).spawn(3)
    original, radii = scene_people(scene, base['person_radius_m'])
    people = original + np.random.default_rng(stream[0]).normal(
        0.0, spec['people_jitter_std_m'], original.shape)
    count = int(spec['guide_counts'][scene['name']])
    room = float(base['room_m'])
    inset = float(base['guides']['initial_inset'])
    # Same orientation, start vertex and 0.012 phase as the stable Environment.
    line = Polygon([(0, 0), (room, 0), (room, room), (0, room)]).buffer(-inset, join_style='mitre').exterior
    jitter = np.random.default_rng(stream[1]).uniform(
        -spec['guide_arc_jitter_half_width_m'], spec['guide_arc_jitter_half_width_m'], count)
    arcs = ((np.arange(count) / count + spec['guide_phase_fraction']) * line.length + jitter) % line.length
    guides = np.array([line.interpolate(value).coords[0] for value in arcs])
    return dict(people=people, radii=radii, demand=np.ones(len(people)), guides=guides,
                room=np.array([room, room]), guide_arcs=arcs, guide_arc_jitter=jitter,
                base_people=original), {
        'entropy': list(stream[0].entropy),
        'people_spawn_key': list(stream[0].spawn_key),
        'guides_spawn_key': list(stream[1].spawn_key),
        'optimizer_spawn_key': list(stream[2].spawn_key),
        'optimizer_uses_randomness': False,
        'inset_line': np.asarray(line.coords).tolist(), 'inset_line_length_m': line.length,
    }


def freeze(spec_path, output):
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('Freeze requires a new empty output directory')
    output.mkdir(parents=True, exist_ok=True)
    spec, base = load_protocol(spec_path)
    write(output / 'protocol.json', {'spec': spec, 'base_spec': base,
          'provenance': provenance({'rebuild': spec, 'base': base}, None, 'fixed_n', None),
          'historical_input_recovery': False})
    rows = []
    scenes = {scene['name']: scene for scene in base['scenes']}
    for scene_name in spec['scenes']:
        for seed in spec['seeds']:
            cell = output / 'inputs' / scene_name / f'seed_{seed}'
            cell.mkdir(parents=True)
            data, stream = draw_cell(spec, base, scenes[scene_name], seed)
            _atomic_npz(cell / 'input.npz', **data)
            # Config source remains SQLite: all methods read exactly this snapshot.
            room_polygon = np.array([[0., 0.], [base['room_m'], 0.],
                                     [base['room_m'], base['room_m']], [0., base['room_m']]])
            cfg_path = _execution_config(cell, data['people'], room_polygon,
                                         spec['guide_counts'][scene_name], seed, base)
            cfg = yaml.safe_load(cfg_path.read_text(encoding='utf-8'))
            cfg['guides']['initial_positions'] = data['guides'].tolist()
            _atomic_text(cfg_path, yaml.safe_dump(cfg, sort_keys=False))
            quality = _people_report(data['people'], data['radii'], base['room_m'], base['minimum_center_distance_m'])
            status = 'ACCEPTED' if quality['meets_stated_separation'] and quality['inside_room'] else 'INITIALIZATION_INVALID'
            error = '' if status == 'ACCEPTED' else 'INITIALIZATION_INVALID: frozen pedestrian input violates 0.40 m separation or room'
            boundary = None
            if status == 'ACCEPTED':
                try:
                    boundary = estimate_disk_union(data['people'], base['primary_offset_m'], base['sample_spacing_m'],
                        data['room'], base['wall_distance_m'], base['crowd_distance_m'], data['radii'],
                        closing_radius=base['closing_radius_m'], quad_segs=base['quad_segs'])
                except ValueError as exc:
                    status, error = str(exc).split(':')[0], str(exc)
            if boundary is not None:
                _atomic_npz(cell / 'boundary.npz', crowd=boundary.crowd, deployment=boundary.deployment,
                            arc=boundary.arc, length=np.array(boundary.length))
            digest = array_hash(**{key: data[key] for key in ('people', 'radii', 'demand', 'guides', 'room')})
            record = {'scene': scene_name, 'seed': seed, 'count': spec['guide_counts'][scene_name],
                      'input_sha256': digest, 'input_file_sha256': sha(cell / 'input.npz'),
                      'config_sha256': sha(cfg_path), 'people_quality': quality, 'random_streams': stream,
                      'status': status, 'error': error,
                      'boundary_sha256': None if boundary is None else sha(cell / 'boundary.npz')}
            write(cell / 'input_record.json', record)
            for method in spec['methods']:
                rows.append({'task_id': f'{scene_name}__seed_{seed}__{method}', 'scene': scene_name,
                    'seed': seed, 'method': method, 'count': record['count'],
                    'cell': cell.relative_to(output).as_posix(), 'input_sha256': digest,
                    'input_status': status})
    write(output / 'manifest.json', {'tasks': rows, 'task_count': len(rows), 'cell_count': len(spec['scenes'])*len(spec['seeds']),
                                   'protocol_sha256': spec['protocol_sha256']})
    print(json.dumps({'frozen_tasks': len(rows), 'input_status_rows': dict(Counter(row['input_status'] for row in rows))}))


def load_boundary(cell):
    with np.load(Path(cell) / 'boundary.npz', allow_pickle=False) as data:
        return Boundary(data['crowd'], data['deployment'], data['arc'], float(data['length']))


def plan_target(boundary, method, count, spec, base):
    density = np.ones(len(boundary.deployment))
    limit, separation = base['gap_limit_m'], base['center_separation_m']
    # Reuse exactly the stable scene-closure constant-density planning paths.
    equal = projected = None
    for phase in base['legacy_phases']:
        if equal is None:
            candidate = plan_coverage(boundary, None, None, count, None, 'equal_arc', phase,
                base['quadrature_samples'], base['legacy_max_iterations'], density_override=density)
            if pair_geometry(boundary, candidate.arc_positions, limit, separation)['accepted_by_controller_rule']:
                equal = candidate
        if projected is None:
            candidate = plan_projected_mass(boundary, None, None, count, None, limit, separation, phase,
                base['quadrature_samples'], base['legacy_max_iterations'], density_override=density)
            if pair_geometry(boundary, candidate.arc_positions, limit, separation)['accepted_by_controller_rule']:
                projected = candidate
        if equal is not None and projected is not None:
            break
    if equal is None:
        equal = plan_coverage(boundary, None, None, count, None, 'equal_arc', 0.,
            base['quadrature_samples'], base['legacy_max_iterations'], density_override=density)
    if projected is None:
        projected = plan_projected_mass(boundary, None, None, count, None, limit, separation, 0.,
            base['quadrature_samples'], base['legacy_max_iterations'], density_override=density)
    if method != 'pairwise_euclidean':
        plan = equal if method == 'equal_arc' else projected
        return plan, {'geometry': pair_geometry(boundary, plan.arc_positions, limit, separation),
                      'density': spec['density']}
    q, mass, length = quadrature_mass(boundary, density, base['quadrature_samples'])
    starts = [('projected_mass_phase0', projected.arc_positions), ('equal_arc_phase0', equal.arc_positions)]
    starts.extend((f'projected_mass_rotate_{fraction:.2f}L', (projected.arc_positions + fraction * length) % length)
                  for fraction in spec['phase_fractions'] if fraction != 0.)
    result = optimize_pairwise(boundary, starts, q, mass, limit, separation,
                              spec['solver_maxiter'], spec['per_start_timeout_s'])
    return result.pop('plan'), result


def load_plan(directory):
    info = json.loads((directory / 'plan.json').read_text(encoding='utf-8'))
    with np.load(directory / 'targets.npz', allow_pickle=False) as data:
        return Plan(data['targets'], data['arc_positions'], data['cost'],
                    info['max_gap'], info['method'], info['iterations'], info['exit_reason'], info['density_total'])


def rejected_result(directory, row, cell, status, reason):
    directory.mkdir(parents=True, exist_ok=False)
    data = np.load(cell / 'input.npz', allow_pickle=False)
    metrics = {**row, 'termination_status': status, 'success': False, 'failure_reason': reason,
               'failure_stage': 'initialization' if status == 'INITIALIZATION_INVALID' else 'planning',
               'schema_version': '1.2.0', 'steps': 0, 'executed': False,
               'wall_time': None, 'criteria': _empty_criteria(),
               'task_sha256': row['input_sha256'],
               'provenance': provenance({}, None, 'fixed_n', row['count'])}
    write(directory / 'metrics.json', metrics)
    write(directory / 'state.json', {'status': 'completed', 'success': False})
    write(directory / 'planning.json', [])
    _atomic_text(directory / 'config.yaml', (cell / 'input.yaml').read_text(encoding='utf-8'))
    _atomic_npz(directory / 'trajectory.npz', positions=data['guides'][None], targets=np.empty((0, 2)),
                assignment=np.full(row['count'], -1), crowd_positions=data['people'],
                crowd_radii=data['radii'], crowd_demand=data['demand'], room=data['room'], time=np.array([0.]),
                safe_velocity=np.empty((0, row['count'], 2)), active_mask=np.zeros(row['count'], bool),
                boundary_crowd=np.empty((0, 2)), boundary_deployment=np.empty((0, 2)),
                boundary_arc=np.empty(0), boundary_length=np.array(np.nan))
    reevaluate_result(directory)
    return metrics


def run_task(arguments):
    output, row, tag = arguments[:3]
    resume_interrupted=bool(arguments[3]) if len(arguments)>3 else False
    output = Path(output)
    spec_record = json.loads((output / 'protocol.json').read_text(encoding='utf-8'))
    spec, base = spec_record['spec'], spec_record['base_spec']
    cell = output / row['cell']
    directory = output / tag / row['scene'] / f"seed_{row['seed']}" / row['method']
    partial=False
    if (directory/'state.json').is_file() and not (directory/'metrics.json').is_file():
        state=json.loads((directory/'state.json').read_text(encoding='utf-8'))
        if not resume_interrupted or state.get('status') not in ('started','interrupted'):
            raise FileExistsError('Incomplete attempt requires explicit --resume-interrupted; evidence is unchanged.')
        partial=True
    if (directory / 'state.json').is_file() and not partial:
        # Never overwrite old evidence or silently reuse it for a new source.
        record=json.loads((cell/'input_record.json').read_text(encoding='utf-8'))
        old=json.loads((directory/'metrics.json').read_text(encoding='utf-8'))
        old_source=old.get('source_sha256',old.get('provenance',{}).get('source_sha256'))
        if (sha(cell/'input.npz')!=record['input_file_sha256']
                or sha(cell/'input.yaml')!=record['config_sha256']
                or old_source!=provenance({},None,'fixed_n',row['count'])['source_sha256']):
            raise FileExistsError('Existing tag has different source or inputs; choose a new --tag. Evidence is unchanged.')
        return old
    try:
        with threadpool_limits(limits=1):
            record = json.loads((cell / 'input_record.json').read_text(encoding='utf-8'))
            if sha(cell / 'input.npz') != record['input_file_sha256'] or sha(cell / 'input.yaml') != record['config_sha256']:
                raise RuntimeError('Frozen input/config changed')
            if record['status'] != 'ACCEPTED':
                return rejected_result(directory, row, cell, record['status'], record['error'])
            boundary = load_boundary(cell)
            plans = output / 'targets' / row['scene'] / f"seed_{row['seed']}" / row['method']
            if (plans / 'targets.npz').is_file():
                plan = load_plan(plans)
            else:
                if tag != 'baseline':
                    raise RuntimeError('Regression must reuse frozen targets')
                started = perf_counter()
                plan, details = plan_target(boundary, row['method'], row['count'], spec, base)
                planning_wall_s=perf_counter()-started
                details['planning_wall_s']=planning_wall_s
                plans.mkdir(parents=True, exist_ok=True)
                write(plans / 'solver.json', details)
                if plan is None:
                    return rejected_result(directory, row, cell, 'PLAN_SEARCH_EXHAUSTED',
                                           'PLAN_SEARCH_EXHAUSTED: pairwise bounded solver found no accepted target set')
                _atomic_npz(plans / 'targets.npz', targets=plan.positions, arc_positions=plan.arc_positions, cost=plan.cost)
                write(plans / 'plan.json', {'method': plan.method, 'iterations': plan.iterations,
                      'exit_reason': plan.exit_reason, 'max_gap': float(plan.max_gap),
                      'density_total': float(plan.density_total), 'planning_wall_s': planning_wall_s,
                      'targets_sha256': sha(plans / 'targets.npz')})
            metrics = experiment(cell / 'input.yaml', directory, plots=False, protocol='fixed_n',
                fixed_n=row['count'], prescribed_boundary=boundary, prescribed_plan=plan,
                boundary_source='estimated_disk_union', method_label=row['method'],
                resume=partial,restart_interrupted=partial)
            offline = reevaluate_result(directory)
            with np.load(directory / 'trajectory.npz', allow_pickle=False) as actual, np.load(cell / 'input.npz', allow_pickle=False) as frozen:
                installed=metrics.get('plan_found',False)
                paired = (np.array_equal(actual['positions'][0], frozen['guides'])
                          and np.array_equal(actual['crowd_positions'], frozen['people'])
                          and (not installed or np.array_equal(actual['targets'], plan.positions)))
            write(directory / 'pairing.json', {'input_sha256': row['input_sha256'], 'exact_input_and_targets': paired,
                  'targets_installed':installed,'targets_sha256': sha(plans / 'targets.npz'),
                  'offline_criteria_consistent': offline['criteria_consistent']})
            return metrics
    except KeyboardInterrupt:
        directory.mkdir(parents=True,exist_ok=True)
        state_path=directory/'state.json'
        state=json.loads(state_path.read_text()) if state_path.is_file() else {}
        write(directory/'interruption.json',{'type':'external_interrupt','state':state,'task_id':row['task_id']})
        write(state_path,{**state,'status':'interrupted'})
        raise
    except Exception:
        directory.mkdir(parents=True, exist_ok=True)
        _atomic_text(directory / 'runner_error.txt', traceback.format_exc())
        write(directory / 'metrics.json', {**row, 'termination_status': 'EXPERIMENT_ERROR', 'success': False})
        write(directory / 'state.json', {'status': 'error', 'success': False})
        return json.loads((directory / 'metrics.json').read_text(encoding='utf-8'))


def summarize(output, tag):
    output = Path(output)
    manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
    rows = []
    for row in manifest['tasks']:
        directory = output / tag / row['scene'] / f"seed_{row['seed']}" / row['method']
        if not (directory / 'metrics.json').is_file():
            continue
        metrics = json.loads((directory / 'metrics.json').read_text(encoding='utf-8'))
        pairing = json.loads((directory / 'pairing.json').read_text(encoding='utf-8')) if (directory / 'pairing.json').is_file() else None
        rows.append({**row, **{key: metrics.get(key) for key in
            ('termination_status', 'success', 'steps', 'failure_stage', 'failure_reason', 'wall_time',
             'replan_count', 'simulation_time', 'path_length', 'minimum_net_clearances')}, 'pairing': pairing})
    summary = {'tasks_expected': manifest['task_count'], 'tasks_completed': len(rows),
               'outcomes': dict(Counter(row['termination_status'] for row in rows)), 'rows': rows}
    write(output / f'{tag}_summary.json', summary)
    print(json.dumps({key: value for key, value in summary.items() if key != 'rows'}))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', default='configs/step1/nonconvex_record_rebuild_v1.yaml')
    parser.add_argument('--output', default='runs/step1_nonconvex_record_rebuild_v1')
    parser.add_argument('--freeze', action='store_true')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--seeds', nargs='+', type=int)
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--tag', default='baseline')
    parser.add_argument('--resume-interrupted',action='store_true')
    args = parser.parse_args()
    if args.freeze:
        freeze(args.spec, args.output)
    if args.run:
        output = Path(args.output).resolve()
        manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
        seeds=sorted({row['seed'] for row in manifest['tasks']}) if args.seeds is None else args.seeds
        if not set(seeds)<={row['seed'] for row in manifest['tasks']}:
            raise ValueError('Requested seeds are outside the frozen manifest')
        if not 1<=args.workers<=2:
            raise ValueError('This notebook protocol uses one or two episode workers')
        tasks = [(str(output), row, args.tag,args.resume_interrupted) for row in manifest['tasks'] if row['seed'] in seeds]
        if args.workers == 1:
            for task in tasks:
                result = run_task(task)
                print(json.dumps({'task': task[1]['task_id'], 'status': result['termination_status']}), flush=True)
        else:
            with ProcessPoolExecutor(max_workers=args.workers) as pool:
                futures = {pool.submit(run_task, task): task for task in tasks}
                for future in as_completed(futures):
                    task = futures[future]
                    result = future.result()
                    print(json.dumps({'task': task[1]['task_id'], 'status': result['termination_status']}), flush=True)
    if Path(args.output, 'manifest.json').is_file():
        summarize(args.output, args.tag)


if __name__ == '__main__':
    main()
