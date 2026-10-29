"""Versioned observation contract; future stages must opt into other scopes."""
from dataclasses import dataclass
from numbers import Real
import math

import numpy as np

from udc.interfaces import Observation, Perception
from .geometry import ScientificFailure
from .observation import BoundedMeasurement

STATIC_CONTRACT = 'udc.step1.static_global.v1'


def validate_static_model(config):
    """Validate the public executor model before creating a scientific event.

    Configuration errors are not scientific deployment failures. Distances may
    be made more conservative, but may not contradict the declared body/clearance.
    The v1 numerical acceptance tolerance cannot exceed the original 1e-7 m.
    """
    def finite_real(value):
        return isinstance(value, Real) and not isinstance(value, (bool, np.bool_)) and math.isfinite(float(value))
    positive = ('group_link_m', 'offset_m', 'sample_spacing_m', 'guide_radius_m', 'clearance_m',
        'guide_distance_m', 'wall_distance_m', 'planned_gap_m', 'max_gap_m', 'bandwidth_m',
        'dt_s', 'max_speed_m_s', 'deadline_s', 'position_tolerance_m', 'curve_tolerance_m', 'speed_tolerance_m_s')
    nonnegative = ('closing_radius_m', 'construction_margin_m', 'numeric_tolerance_m')
    integers = ('planning_samples', 'evaluation_samples', 'phase_bins', 'lloyd_iterations', 'hold_steps')
    if not isinstance(config, dict) or any(key not in config for key in positive + nonnegative + integers):
        raise ValueError('Static session model is missing required configuration fields')
    for key in positive + nonnegative:
        value = config[key]
        if not finite_real(value) or (value <= 0 if key in positive else value < 0):
            raise ValueError('Invalid static session model field: ' + key)
    for key in integers:
        value = config[key]
        if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)) or value < 1:
            raise ValueError('Invalid positive integer session model field: ' + key)
    if (config['guide_distance_m'] < 2 * config['guide_radius_m'] + config['clearance_m']
            or config['wall_distance_m'] < config['guide_radius_m'] + config['clearance_m']):
        raise ValueError('Session safety distances contradict robot radius and clearance')
    if config['planned_gap_m'] >= config['max_gap_m'] or config['numeric_tolerance_m'] > 1e-7:
        raise ValueError('Session planning or numerical acceptance tolerance violates the v1 contract')
    tracking = config.get('tracking_bound_m', 1e-10)
    delta = config.get('true_service_delta', .002)
    if (not finite_real(tracking) or not finite_real(delta) or tracking < 0 or delta < 0 or delta > 1
            or tracking > config['position_tolerance_m'] or tracking >= config['max_gap_m'] / 2):
        raise ValueError('Invalid tracking or service-loss model contract')
    sides = config.get('routing_sides', (16, 32, 64))
    if not isinstance(sides, (tuple, list)) or not sides or any(
            isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)) or value < 4 for value in sides):
        raise ValueError('Invalid finite routing representation levels')
    for name, default in (('joint_assignment_candidates', 64), ('routing_max_vertices', 20000)):
        value = config.get(name, default)
        if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)) or value < 1:
            raise ValueError('Invalid finite routing budget: ' + name)
    for name, default in (('routing_pad_m', 1e-9), ('routing_interior_margin_m', 1e-6)):
        value = config.get(name, default)
        if not finite_real(value) or value <= 0:
            raise ValueError('Invalid conservative routing construction margin: ' + name)
    if config.get('execution_policy', 'joint_reassigned_then_reverse_v1') != 'joint_reassigned_then_reverse_v1':
        raise ValueError('Static session v1 requires its explicit hybrid execution policy')


@dataclass(frozen=True)
class ObservationFrame:
    frame_id: str
    time_s: float
    person_ids: tuple[int, ...]
    measurement: BoundedMeasurement
    contract: str = STATIC_CONTRACT

    def __post_init__(self):
        ids = tuple(self.person_ids)
        if (not self.frame_id or not isinstance(self.frame_id, str)
                or not np.isfinite(self.time_s) or self.time_s < 0
                or not isinstance(self.measurement, BoundedMeasurement)
                or len(ids) != len(self.measurement.observation.positions)
                or any(isinstance(i, (bool, np.bool_)) or not isinstance(i, (int, np.integer)) for i in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError('Invalid static observation frame identity, time or person IDs')
        object.__setattr__(self, 'person_ids', tuple(int(i) for i in ids))
        object.__setattr__(self, 'time_s', float(self.time_s))

    @classmethod
    def from_perception(cls, frame_id, perception: Perception, error_bound_m):
        people = perception.pedestrians
        velocities = np.asarray(people.velocities)
        if velocities.shape != np.asarray(people.positions).shape or not np.isfinite(velocities).all():
            raise ValueError('Invalid pedestrian velocity metadata')
        if np.any(velocities != 0):
            raise ScientificFailure('STEP_SCOPE_VIOLATION', 'Moving people require an explicit Step 2 protocol')
        return cls(frame_id, perception.time, tuple(people.ids),
            BoundedMeasurement(Observation(people.positions, people.radii, people.demand),
                               error_bound_m, 'static_global_perception_adapter'))
