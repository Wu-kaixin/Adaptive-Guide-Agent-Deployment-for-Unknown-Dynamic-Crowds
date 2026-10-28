"""Independent fail-closed checks for serial paths, using observation only."""
import numpy as np
from shapely.geometry import LineString


def segment_gate(start, end, current, moving, free, room, config):
    start, end, current = np.asarray(start), np.asarray(end), np.asarray(current)
    step = end - start
    others = np.delete(current, moving, axis=0)
    if len(others):
        fraction = np.clip(((others - start) @ step) / max(float(step @ step), 1e-30), 0., 1.)
        distances = np.linalg.norm(start + fraction[:, None] * step - others, axis=1)
        if float(distances.min()) < config['guide_distance_m']:
            ids = np.delete(np.arange(len(current)), moving)
            index = int(np.argmin(distances))
            return {'reason': 'analytic_stationary_robot_clearance', 'blocking_guide': int(ids[index]),
                    'center_distance_m': float(distances[index]), 'threshold_m': config['guide_distance_m'],
                    'start': start.tolist(), 'end': end.tolist(), 'blocker': others[index].tolist()}
    wall = float(np.minimum(np.minimum(start, end), np.asarray(room) - np.maximum(start, end)).min())
    if wall < config['wall_distance_m']:
        return {'reason': 'analytic_wall_clearance', 'center_wall_distance_m': wall,
                'start': start.tolist(), 'end': end.tolist()}
    line = LineString([start, end])
    if not line.difference(free).is_empty:
        return {'reason': 'segment_difference_outside_serial_free_space',
                'outside_length_m': float(line.difference(free).length),
                'covers_predicate': bool(free.covers(line)), 'start': start.tolist(), 'end': end.tolist()}
    return None
