"""Observation-only resource/phase update decisions for consecutive deployments."""
import json
import numpy as np
from shapely.geometry import LineString, Point

from .allocation import allocate
from .phase_retention import phase_pool, choose_groups, GREEDY, admissible
from .reference_support import certificate

METHODS = ('recompute_all', 'phase_guard', 'counts_phase_guard')


def actual_targets(execution, target_groups):
    count = int(np.count_nonzero(execution.assignment >= 0))
    points = np.empty((count, 2))
    active = execution.assignment >= 0
    points[execution.assignment[active]] = execution.positions[-1][active]
    return points, np.asarray(target_groups, int).copy()


def decide(measurement, groups, domain, catalogs, budget, previous_points, previous_ids, config, support, method, phase_cache=None):
    if method not in METHODS:raise ValueError('Unknown sequential update method')
    allocation = allocate(catalogs, groups, int(budget), 'minimax')
    counts = tuple(int(n) for n in allocation.counts)
    old_counts = tuple(int(n) for n in np.bincount(previous_ids, minlength=len(groups)))
    pools = []
    obs=measurement.observation
    context=(obs.positions.tobytes(),obs.radii.tobytes(),obs.demand.tobytes(),domain.geometry_sha256,json.dumps(config,sort_keys=True))
    for k,(group,n) in enumerate(zip(groups,counts)):
        anchor = previous_points[previous_ids == k]
        anchors = {key:anchor for key in GREEDY} if len(anchor)==n else None
        base=None
        if phase_cache is not None:
            key=(context,group.rows,group.boundary.deployment.tobytes(),group.boundary.arc.tobytes(),group.boundary.length,n)
            if key not in phase_cache:phase_cache[key]=phase_pool(group,measurement,domain,n,config)
            base=phase_cache[key]
        pools.append(phase_pool(group,measurement,domain,n,config,anchors,base_options=base))
    greedy,ids,_,_=choose_groups(pools,'continuous_euclidean_greedy')
    decisions=[]
    if method=='recompute_all':
        return greedy,ids,{'counts':counts,'baseline_counts':counts,'baseline_targets':greedy,
                          'decision':'recompute','certificates':decisions,'accepted_certificate':None}
    def certify(points, labels, kind):
        cert=certificate(support,points,greedy,config['max_gap_m']/2,config.get('true_service_delta',.002),config.get('tracking_bound_m',1e-10))
        decisions.append({'kind':kind,'certificate':cert,'targets':points,'target_groups':labels})
        return cert
    if method=='counts_phase_guard' and len(previous_points)<=budget and admissible(previous_points,domain,config['guide_distance_m']):
        curve_ok=all(LineString(np.vstack([g.boundary.deployment,g.boundary.deployment[0]])).distance(Point(p))<=config['curve_tolerance_m']
                     for k,g in enumerate(groups) for p in previous_points[previous_ids==k])
        if curve_ok:
            cert=certify(previous_points,previous_ids,'whole_actual_incumbent')
            if cert['accept_retained']:
                return previous_points.copy(),previous_ids.copy(),{'counts':old_counts,'baseline_counts':counts,
                    'baseline_targets':greedy,'decision':'hold_counts_and_phase','certificates':decisions,'accepted_certificate':cert}
    retained,retained_ids,_,_=choose_groups(pools,'euclidean_retain_0.002')
    cert=certify(retained,retained_ids,'projected_phase_anchor')
    if cert['accept_retained']:
        return retained,retained_ids,{'counts':counts,'baseline_counts':counts,'baseline_targets':greedy,
                    'decision':'retain_phase','certificates':decisions,'accepted_certificate':cert}
    return greedy,ids,{'counts':counts,'baseline_counts':counts,'baseline_targets':greedy,
                     'decision':'uncertified_fallback','certificates':decisions,'accepted_certificate':None}
