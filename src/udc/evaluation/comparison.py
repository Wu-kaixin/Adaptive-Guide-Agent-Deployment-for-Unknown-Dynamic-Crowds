"""Common execution metrics: no estimator geometry is an evaluation input."""
import numpy as np

from udc.safety import Safety, path_clearances, is_safe


def common_motion_metrics(positions, velocities, people, radii, room, targets,
                          assignment, controller_config, limits, dt):
    """Evaluate actual straight execution intervals and generated-target arrival.

    Zero executed intervals means missing motion evidence, not zero travel or
    zero violations. People/radii/room are the paired input snapshot, never an
    estimated occupied polygon. Radii are conservatively represented by limits
    in the unchanged Step-1 centre-distance evaluator.
    """
    if len(positions) < 2:
        return None
    positions, velocities = np.asarray(positions), np.asarray(velocities)
    if np.asarray(radii).shape != (len(people),):
        raise ValueError('Physical radii must match the shared people snapshot')
    minima = {'crowd': np.inf, 'guides': np.inf, 'walls': np.inf}
    violations = 0
    for start,end in zip(positions[:-1],positions[1:]):
        distances = path_clearances(start,end,people,room)
        violations += not is_safe(distances,limits)
        for name,value in distances.items():
            if value is not None:
                minima[name] = min(minima[name],value)
    active = np.asarray(assignment)>=0
    hold = controller_config['hold_steps']
    enough = active.any() and len(velocities)>=hold and len(positions)>=hold+1
    arrival = False
    max_error = None
    if active.any():
        errors = np.linalg.norm(positions[:,active]-targets[assignment[active]][None,:,:],axis=2)
        max_error = float(errors[-1].max())
        arrival = bool(enough and np.all(errors[-hold:]<=controller_config['position_tolerance'])
                       and np.all(np.linalg.norm(velocities[-hold:,active],axis=2)<=controller_config['speed_tolerance']))
    thresholds = {'crowd':limits.crowd_distance,'guides':limits.guide_distance,'walls':limits.wall_distance}
    return {'executed_intervals':len(positions)-1,'simulation_time_s':float((len(positions)-1)*dt),
            'actual_trajectory_length_m':float(np.linalg.norm(np.diff(positions,axis=0),axis=2).sum()),
            'interval_safety_ok':violations==0, 'unsafe_interval_count':int(violations),
            'minimum_center_distances_m':{k:None if not np.isfinite(v) else float(v) for k,v in minima.items()},
            'minimum_net_clearances_m':{k:None if not np.isfinite(v) else float(v-thresholds[k]) for k,v in minima.items()},
            'terminal_arrival_ok':arrival,'final_max_target_error_m':max_error}
