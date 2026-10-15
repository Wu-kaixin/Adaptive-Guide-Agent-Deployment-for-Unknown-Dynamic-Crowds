"""Recompute hotspot-excess metrics from saved trajectories. Does not rewrite them."""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import yaml

from udc.analysis.summary import executed_metric, retained_improvement
from udc.boundary import Boundary
from udc.deployment import periodic_gaps, plan_coverage, plan_projected_mass
from udc.deployment.reference import optimize_reference
from udc.evaluation.coverage import excess_coverage


def _boundary(data):
    return Boundary(data['boundary_crowd'], data['boundary_deployment'],
                    data['boundary_arc'], float(data['boundary_length']))


def _report_row(directory):
    directory = Path(directory)
    data = np.load(directory / 'trajectory.npz', allow_pickle=False)
    config = yaml.safe_load((directory / 'config.yaml').read_text(encoding='utf-8'))
    metrics = json.loads((directory / 'metrics.json').read_text(encoding='utf-8'))
    if not len(data['boundary_deployment']):
        return {'excess_applicable': False}
    region = (config.get('crowd') or {}).get('demand_region') or {}
    targets = data['targets'] if len(data['targets']) else None
    final = None
    if metrics.get('plan_found') and len(data['positions']) and np.asarray(data['active_mask']).any():
        final = data['positions'][-1][data['active_mask']]
    report = excess_coverage(
        _boundary(data), data['crowd_positions'], data['crowd_demand'], data['crowd_hotspot_mask'],
        config['controller']['demand_bandwidth'], config['controller']['quadrature_samples'],
        targets if targets is not None else data['boundary_deployment'][:3],
        final, multiplier=region.get('multiplier'),
        curve_tolerance=config['controller']['curve_tolerance'],
        actual_curve_distance=metrics.get('mean_curve_distance'))
    if targets is None:
        for key in list(report):
            if key.startswith('planned_'):
                report[key] = None
    report['demand_clipped_count'] = metrics.get('demand_clipped_count')
    return report


def _free_mass(directory, count):
    """Best unconstrained Mass CVT attempt on the saved crowd. Not an executed trajectory."""
    directory = Path(directory)
    data = np.load(directory / 'trajectory.npz', allow_pickle=False)
    config = yaml.safe_load((directory / 'config.yaml').read_text(encoding='utf-8'))
    control = config['controller']
    boundary = _boundary(data)
    region = (config.get('crowd') or {}).get('demand_region') or {}
    best = None
    for phase in control['phases']:
        plan = plan_coverage(
            boundary, data['crowd_positions'], data['crowd_demand'], count,
            control['demand_bandwidth'], 'mass_cvt', phase=phase,
            samples=control['quadrature_samples'], max_iterations=control['planner_max_iterations'])
        scored = excess_coverage(
            boundary, data['crowd_positions'], data['crowd_demand'], data['crowd_hotspot_mask'],
            control['demand_bandwidth'], control['quadrature_samples'], plan.positions,
            multiplier=region.get('multiplier'))
        if best is None or scored['planned_demand_weighted_cost'] < best['planned_demand_weighted_cost']:
            best = {**scored, 'max_gap': plan.max_gap, 'phase': phase}
    saved = [item['max_gap'] for item in json.loads((directory / 'planning.json').read_text(encoding='utf-8'))]
    best['matches_saved_gap'] = any(abs(best['max_gap'] - gap) <= 1e-6 for gap in saved)
    return best


def _mean(values):
    values = [value for value in values if value is not None]
    if not values:
        return {'n': 0, 'mean': None}
    return {'n': len(values), 'mean': float(np.mean(values)),
            'min': float(np.min(values)), 'max': float(np.max(values))}


def _ratio_of_means(rescued, key_ref, key_candidate, key_free):
    ref, cand, free = (_mean([item[key] for item in rescued])['mean'] for key in (key_ref, key_candidate, key_free))
    return retained_improvement(ref, cand, free)


def _rescued_summary(rescued):
    demand = [item['demand_retention'] for item in rescued]
    finite = [value for value in demand if value is not None]
    return {
        'n': len(rescued),
        'free_gap_matches_saved': sum(item['free_gap_matches_saved'] for item in rescued),
        'demand_retention_mean_of_ratios': _mean(finite),
        'demand_retention_median': None if not finite else float(np.median(finite)),
        'demand_retention_outside_unit_interval': sum(value < 0 or value > 1 for value in finite),
        'demand_retention_ratio_of_means': _ratio_of_means(
            rescued, 'equal_demand_cost', 'projected_demand_cost', 'free_demand_cost'),
        'excess_retention_ratio_of_means': _ratio_of_means(
            rescued, 'equal_excess_cost', 'projected_excess_cost', 'free_excess_cost'),
        'free_demand_cost': _mean([item['free_demand_cost'] for item in rescued]),
        'projected_demand_cost': _mean([item['projected_demand_cost'] for item in rescued]),
        'equal_demand_cost': _mean([item['equal_demand_cost'] for item in rescued]),
        'free_excess_cost': _mean([item['free_excess_cost'] for item in rescued]),
        'projected_excess_cost': _mean([item['projected_excess_cost'] for item in rescued]),
        'equal_excess_cost': _mean([item['equal_excess_cost'] for item in rescued]),
    }


def reanalyze(comparison_json, output):
    rows = json.loads(Path(comparison_json).read_text(encoding='utf-8'))
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    enriched = []
    for row in rows:
        extra = _report_row(row['output']) if (Path(row['output']) / 'trajectory.npz').exists() else {}
        enriched.append({**{key: row.get(key) for key in (
            'cell', 'shape', 'demand', 'resource', 'fixed_n', 'seed', 'method', 'success',
            'plan_found', 'stage_diagnosis', 'output')}, **extra,
            'simulation_time': executed_metric(row, 'simulation_time')})
    by = {(item['cell'], item['seed'], item['method']): item for item in enriched}
    rescued = []
    for (cell, seed, method), mass in list(by.items()):
        if method != 'mass_cvt' or mass['success']:
            continue
        projected = by[(cell, seed, 'projected_mass')]
        equal = by[(cell, seed, 'equal_arc')]
        free = _free_mass(Path(mass['output']), projected['fixed_n'])
        rescued.append({
            'cell': cell, 'seed': seed, 'shape': projected['shape'], 'demand': projected['demand'],
            'fixed_n': projected['fixed_n'],
            'free_gap': free['max_gap'], 'free_gap_matches_saved': free['matches_saved_gap'],
            'free_demand_cost': free['planned_demand_weighted_cost'],
            'free_excess_cost': free['planned_excess_weighted_cost'],
            'projected_demand_cost': projected['planned_demand_weighted_cost'],
            'equal_demand_cost': equal['planned_demand_weighted_cost'],
            'projected_excess_cost': projected['planned_excess_weighted_cost'],
            'equal_excess_cost': equal['planned_excess_weighted_cost'],
            'demand_retention': retained_improvement(
                equal['planned_demand_weighted_cost'], projected['planned_demand_weighted_cost'],
                free['planned_demand_weighted_cost']),
            'excess_retention': retained_improvement(
                equal['planned_excess_weighted_cost'], projected['planned_excess_weighted_cost'],
                free['planned_excess_weighted_cost']),
        })
    def paired(metric, left='projected_mass', right='equal_arc', require_mass_success=False):
        deltas = []
        for (cell, seed, method), item in by.items():
            if method != left:
                continue
            other = by[(cell, seed, right)]
            if require_mass_success and not by[(cell, seed, 'mass_cvt')]['success']:
                continue
            mine, theirs = item.get(metric), other.get(metric)
            if mine is None or theirs is None:
                continue
            deltas.append(mine - theirs)
        return _mean(deltas)

    summary = {
        'tasks': len(enriched),
        'excess_applicable': sum(bool(item.get('excess_applicable')) for item in enriched if item['method'] == 'equal_arc'),
        'uniform_marked_not_applicable': sum(
            item['demand'] == 'uniform' and item.get('excess_applicable') is False
            for item in enriched if item['method'] == 'equal_arc'),
        'off_curve_actual': sum(bool(item.get('actual_off_curve')) for item in enriched),
        'projected_minus_equal_demand_cost': paired('planned_demand_weighted_cost'),
        'projected_minus_equal_excess_cost': paired('planned_excess_weighted_cost'),
        'projected_minus_mass_demand_cost_where_mass_succeeded': paired(
            'planned_demand_weighted_cost', right='mass_cvt', require_mass_success=True),
        'rescued': _rescued_summary(rescued),
    }
    (output / 'excess_summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    (output / 'rescued.json').write_text(json.dumps(rescued, indent=2) + '\n', encoding='utf-8')
    (output / 'rows.json').write_text(json.dumps(enriched, indent=2) + '\n', encoding='utf-8')
    _figures(enriched, output / 'figures')
    return summary


def _figures(rows, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle
    for shape in ('circle', 'ellipse'):
        equal = next(item for item in rows if item['shape'] == shape and item['demand'] == 'strong'
                     and item['resource'] == 'near' and item['seed'] == 0 and item['method'] == 'equal_arc')
        projected = next(item for item in rows if item['shape'] == shape and item['demand'] == 'strong'
                         and item['resource'] == 'near' and item['seed'] == 0 and item['method'] == 'projected_mass')
        data = np.load(Path(equal['output']) / 'trajectory.npz')
        config = yaml.safe_load((Path(equal['output']) / 'config.yaml').read_text(encoding='utf-8'))
        boundary = _boundary(data)
        region = config['crowd']['demand_region']
        fig, axes = plt.subplots(1, 2, figsize=(11, 5), constrained_layout=True)
        ax = axes[0]
        ax.scatter(*data['crowd_positions'].T, c=data['crowd_demand'], cmap='YlOrRd', s=16)
        ax.add_patch(Circle(region['center'], region['radius'], fill=False, color='#c0392b', linestyle=':'))
        ax.plot(*np.vstack([boundary.deployment, boundary.deployment[0]]).T, color='#159a8c', lw=1)
        ax.scatter(*data['targets'].T, marker='+', color='#1d4ed8', label='Equal Arc')
        other = np.load(Path(projected['output']) / 'trajectory.npz')
        ax.scatter(*other['targets'].T, marker='x', color='#c0392b', label='projected_mass')
        ax.set(aspect='equal', title=f'{shape} strong, near, seed 0')
        ax.legend(fontsize=8)
        ax = axes[1]
        from udc.evaluation.coverage import _log_gaussian_mass, excess_weights
        excess, _ = excess_weights(data['crowd_demand'], data['crowd_hotspot_mask'], region['multiplier'])
        log_total = _log_gaussian_mass(boundary.deployment, data['crowd_positions'], data['crowd_demand'], 1.2)
        shift = float(np.max(log_total))
        total = np.exp(log_total - shift)
        extra = np.exp(np.clip(_log_gaussian_mass(
            boundary.deployment, data['crowd_positions'], excess, 1.2) - shift, -80, None))
        ax.plot(boundary.arc, total / total.max(), color='#24344d', label='original demand')
        ax.plot(boundary.arc, extra / max(total.max(), 1e-12), color='#c0392b', label='hotspot excess')
        ax.set(xlabel='arc [m]', ylabel='mass / max original', title='Frozen curve contributions')
        ax.legend(fontsize=8)
        fig.savefig(destination / f'{shape}_strong_near_seed0.png', dpi=140)
        plt.close(fig)


def reference_study(comparison_json, output, seeds=(0, 1), maxiter=40):
    """Planning-only numerical reference on the near-bound tier. No new simulation."""
    rows = json.loads(Path(comparison_json).read_text(encoding='utf-8'))
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    selected = [row for row in rows if row['method'] == 'projected_mass' and row['resource'] == 'near'
                and row['seed'] in set(seeds)]
    reports = []
    for row in selected:
        directory = Path(row['output'])
        data = np.load(directory / 'trajectory.npz')
        config = yaml.safe_load((directory / 'config.yaml').read_text(encoding='utf-8'))
        control = config['controller']
        boundary = _boundary(data)
        limit = control['max_gap_limit'] - 2 * control['arc_tolerance']
        separation = 0.75
        count = int(row['fixed_n'])
        length = boundary.length
        equal_gaps = np.full(count, length / count)
        projected = plan_projected_mass(
            boundary, data['crowd_positions'], data['crowd_demand'], count,
            control['demand_bandwidth'], limit, separation, samples=control['quadrature_samples'],
            max_iterations=control['planner_max_iterations'])
        proj_gaps = periodic_gaps(projected.arc_positions, length)
        starts = [
            ('equal_arc', 0.0, equal_gaps),
            ('projected_mass', float(projected.arc_positions[0]), proj_gaps),
            ('equal_arc_shifted', 0.5 * length / count, equal_gaps),
            ('projected_rotated', float((projected.arc_positions[0] + 0.5 * length / count) % length), proj_gaps),
        ]
        result = optimize_reference(
            boundary, data['crowd_positions'], data['crowd_demand'], count,
            control['demand_bandwidth'], limit, separation, starts,
            samples=control['quadrature_samples'], maxiter=maxiter)
        saved = {}
        for method in ('equal_arc', 'projected_mass'):
            sibling = directory.parent.parent / method / f"seed_{row['seed']}"
            targets = np.load(sibling / 'trajectory.npz')['targets']
            scored = excess_coverage(
                boundary, data['crowd_positions'], data['crowd_demand'], data['crowd_hotspot_mask'],
                control['demand_bandwidth'], control['quadrature_samples'], targets,
                multiplier=(config.get('crowd') or {}).get('demand_region', {}).get('multiplier'))
            saved[method] = scored['planned_demand_weighted_cost']
        reports.append({
            'cell': row['cell'], 'seed': row['seed'], 'shape': row['shape'], 'demand': row['demand'],
            'fixed_n': count,
            'equal_cost': saved['equal_arc'],
            'projected_cost': saved['projected_mass'],
            'reference_cost': result['best_cost'],
            'reference_feasible': result['best_feasible'],
            'gain_over_projected': (None if result['best_cost'] is None else saved['projected_mass'] - result['best_cost']),
            'attempts': [{key: value for key, value in attempt.items()} for attempt in result['attempts']],
        })
        print(row['shape'], row['demand'], row['seed'], 'projected', round(saved['projected_mass'], 5),
              'reference', None if result['best_cost'] is None else round(result['best_cost'], 5), flush=True)
    (output / 'reference.json').write_text(json.dumps(reports, indent=2) + '\n', encoding='utf-8')
    return reports


def sensitivity(comparison_json, output):
    rows = json.loads(Path(comparison_json).read_text(encoding='utf-8'))
    row = next(item for item in rows if item['method'] == 'projected_mass' and item['shape'] == 'circle'
               and item['demand'] == 'strong' and item['resource'] == 'near' and item['seed'] == 0)
    directory = Path(row['output'])
    data = np.load(directory / 'trajectory.npz')
    config = yaml.safe_load((directory / 'config.yaml').read_text(encoding='utf-8'))
    control = config['controller']
    boundary = _boundary(data)
    limit = control['max_gap_limit'] - 2 * control['arc_tolerance']
    count = int(row['fixed_n'])
    length = boundary.length
    projected = plan_projected_mass(
        boundary, data['crowd_positions'], data['crowd_demand'], count, control['demand_bandwidth'],
        limit, 0.75, samples=2048, max_iterations=control['planner_max_iterations'])
    gaps = periodic_gaps(projected.arc_positions, length)
    found = []
    for samples in (1024, 2048, 4096):
        result = optimize_reference(
            boundary, data['crowd_positions'], data['crowd_demand'], count, control['demand_bandwidth'],
            limit, 0.75, [('projected_mass', float(projected.arc_positions[0]), gaps)],
            samples=samples, maxiter=40)
        scored = excess_coverage(
            boundary, data['crowd_positions'], data['crowd_demand'], data['crowd_hotspot_mask'],
            control['demand_bandwidth'], samples, projected.positions,
            multiplier=config['crowd']['demand_region']['multiplier'])
        found.append({
            'samples': samples,
            'projected_cost': scored['planned_demand_weighted_cost'],
            'reference_cost': result['best_cost'],
            'difference': None if result['best_cost'] is None else scored['planned_demand_weighted_cost'] - result['best_cost'],
        })
    Path(output).mkdir(parents=True, exist_ok=True)
    (Path(output) / 'sensitivity.json').write_text(json.dumps(found, indent=2) + '\n', encoding='utf-8')
    return found
