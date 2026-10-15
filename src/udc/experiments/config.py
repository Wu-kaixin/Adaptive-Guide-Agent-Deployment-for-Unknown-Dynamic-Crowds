"""Strict Step 1 configuration contract."""
from pathlib import Path
import numpy as np
import yaml

METHODS = {"equal_arc", "average_cvt", "mass_cvt", "distmesh", "ra_abdc"}
BASELINES = {"equal_arc", "average_cvt", "mass_cvt", "distmesh"}
PROTOCOLS = {"fixed_n", "adaptive_resource"}


def demand_kind(crowd):
    """Name the demand field: uniform, independent per person, or spatial hotspot."""
    independent = float(crowd['demand']['std']) > 0
    if crowd.get('demand_region') is None:
        return 'independent' if independent else 'uniform'
    return 'hotspot_on_independent' if independent else 'hotspot'

def load_config(path, seed=None):
    with Path(path).open(encoding='utf-8') as stream:
        config = yaml.safe_load(stream)
    sections = {
        'scene': {'type', 'width', 'height', 'openings'},
        'crowd': {'count', 'spawn_vertices', 'spacing', 'radius', 'demand'},
        'guides': {'count', 'radius', 'max_speed', 'initial_inset'},
        'controller': {
            'method', 'offset', 'sample_spacing', 'budget_gap', 'max_gap_limit',
            'position_tolerance', 'speed_tolerance', 'curve_tolerance',
            'arc_tolerance', 'safety_numeric_tolerance', 'demand_bandwidth',
            'gain', 'hold_steps', 'clearance', 'waypoint_tolerance', 'phases',
            'quadrature_samples', 'planner_max_iterations', 'stall_window',
            'max_replans',
        },
        'simulation': {'seed', 'dt', 'max_steps'},
    }
    if not isinstance(config, dict) or set(config) != {'step', *sections} or config['step'] != 1:
        raise ValueError('Only strict Step-1 schema is supported')
    for section, fields in sections.items():
        optional = ({'source', 'grid', 'trajectory_file', 'demand_region'} if section == 'crowd'
                    else {'concentration'} if section == 'controller' else set())
        if not isinstance(config[section], dict) or not fields <= set(config[section]) or set(config[section]) - fields - optional:
            raise ValueError(f'{section} requires exactly: {sorted(fields)}')
    if seed is not None:
        config['simulation']['seed'] = seed
    integers = [
        ('crowd', 'count', 3), ('guides', 'count', 3),
        ('simulation', 'seed', 0), ('simulation', 'max_steps', 1),
        ('controller', 'hold_steps', 1), ('controller', 'quadrature_samples', 32),
        ('controller', 'planner_max_iterations', 1), ('controller', 'stall_window', 2),
        ('controller', 'max_replans', 0),
    ]
    for section, key, minimum in integers:
        if type(config[section][key]) is not int or config[section][key] < minimum:
            raise ValueError(f'{section}.{key} must be integer >= {minimum}')
    numeric = {
        'scene': ['width', 'height'], 'crowd': ['spacing'],
        'guides': ['radius', 'max_speed', 'initial_inset'],
        'controller': [
            'offset', 'sample_spacing', 'budget_gap', 'max_gap_limit',
            'position_tolerance', 'speed_tolerance', 'curve_tolerance',
            'arc_tolerance', 'safety_numeric_tolerance', 'demand_bandwidth',
            'gain', 'clearance', 'waypoint_tolerance',
        ],
        'simulation': ['dt'],
    }
    for section, keys in numeric.items():
        for key in keys:
            value = config[section][key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not np.isfinite(value) or value <= 0:
                raise ValueError(f'{section}.{key} must be finite positive')
    if config['controller']['method'] not in METHODS:
        raise ValueError('Invalid method')
    concentration = config['controller'].get('concentration', 1.0)
    if isinstance(concentration, bool) or not isinstance(concentration, (int, float)) \
            or not np.isfinite(concentration) or not 0 < concentration <= 1:
        raise ValueError('controller.concentration must lie in (0, 1]; it only affects ra_abdc')
    phases = config['controller']['phases']
    if not isinstance(phases, list) or not phases or not np.isfinite(phases).all():
        raise ValueError('phases must be a nonempty finite list')
    for name in ['radius', 'demand']:
        distribution = config['crowd'][name]
        if not isinstance(distribution, dict) or set(distribution) != {'mean', 'std', 'min', 'max'}:
            raise ValueError(f'Invalid crowd.{name}')
        values = [distribution[key] for key in ['mean', 'std', 'min', 'max']]
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values) \
                or not np.isfinite(values).all() or distribution['std'] < 0 \
                or distribution['min'] <= 0 \
                or not distribution['min'] <= distribution['mean'] <= distribution['max']:
            raise ValueError(f'Invalid crowd.{name} distribution')
    vertices = np.asarray(config['crowd']['spawn_vertices'])
    if vertices.ndim != 2 or vertices.shape[1] != 2 or len(vertices) < 3 \
            or not np.issubdtype(vertices.dtype, np.number) or not np.isfinite(vertices).all():
        raise ValueError('crowd.spawn_vertices must be finite (N,2), N >= 3')
    crowd = config['crowd']
    source = crowd.get('source', 'jupedsim')
    if source not in {'jupedsim', 'grid', 'sqlite'}:
        raise ValueError('crowd.source must be jupedsim, grid or sqlite')
    if source == 'grid':
        grid = crowd.get('grid')
        if not isinstance(grid, dict) or set(grid) != {'rows', 'columns', 'origin', 'spacing'}:
            raise ValueError('grid requires rows/columns/origin/spacing')
        if any(type(grid[k]) is not int or grid[k] < 1 for k in ['rows', 'columns']):
            raise ValueError('grid dimensions must be positive integers')
        if grid['rows'] * grid['columns'] != crowd['count']:
            raise ValueError('grid dimensions must match crowd.count')
        origin = np.asarray(grid['origin'], dtype=float)
        if origin.shape != (2,) or not np.isfinite(origin).all() or not np.isfinite(grid['spacing']) or grid['spacing'] <= 0:
            raise ValueError('Invalid grid origin or spacing')
    elif 'grid' in crowd:
        raise ValueError('grid settings are only valid for crowd.source=grid')
    if source == 'sqlite':
        filename = crowd.get('trajectory_file')
        if not isinstance(filename, str) or not filename:
            raise ValueError('sqlite source requires trajectory_file')
        filename = Path(filename)
        if not filename.is_absolute():
            filename = Path(path).resolve().parent / filename
        crowd['trajectory_file'] = str(filename.resolve())
    elif 'trajectory_file' in crowd:
        raise ValueError('trajectory_file is only valid for crowd.source=sqlite')
    region = crowd.get('demand_region')
    if region is not None:
        if not isinstance(region, dict) or set(region) != {'center', 'radius', 'multiplier'}:
            raise ValueError('demand_region requires center, radius and multiplier')
        center = np.asarray(region['center'], dtype=float)
        if center.shape != (2,) or not np.isfinite(center).all():
            raise ValueError('demand_region.center must be a finite point')
        radius, multiplier = region['radius'], region['multiplier']
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               for value in (radius, multiplier)) or not np.isfinite([radius, multiplier]).all() \
                or radius <= 0 or multiplier <= 1:
            raise ValueError('demand_region radius and multiplier must be finite, with radius > 0 and multiplier > 1')
    if config['simulation']['dt'] * config['controller']['gain'] > 1:
        raise ValueError('Conservative tracker requires dt * gain <= 1')
    return config
