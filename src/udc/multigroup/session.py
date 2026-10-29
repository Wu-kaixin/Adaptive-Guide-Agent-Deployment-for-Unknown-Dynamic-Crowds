"""Stateful observation-only Step 1 planning, delivery and actual-state feedback.

Controller delivery is deliberately distinct from evaluator full-task success.
Every accepted invocation, including scientific/program failures, is journaled.
This API simulates the verified executor; it is not a live robot backend.
"""
from dataclasses import dataclass
import copy
import traceback
import threading

import numpy as np

from udc.interfaces import Observation
from .foundation_contract import ObservationFrame, STATIC_CONTRACT, validate_static_model
from .observation import BoundedMeasurement, immutable_array
from .geometry import construct_groups, ScientificFailure
from .allocation import allocate, build_catalog
from .phase_retention import phase_pool, choose_groups
from .reference_support import build_support
from .stable_update import METHODS, decide, actual_targets
from .joint_reconfiguration import execute_hybrid
from .execution import Execution
from .session_artifacts import SessionJournal
from .session_recovery import implementation_binding, recover


@dataclass(frozen=True)
class DeliveryRecord:
    frame: ObservationFrame
    execution: Execution
    targets: np.ndarray
    target_groups: np.ndarray
    planning: dict
    metrics: dict


class StaticDeploymentSession:
    def __init__(self, guide_positions, room_m, config, artifact_root, method='counts_phase_guard', provenance=None):
        validate_static_model(config)
        guides = np.asarray(guide_positions, float)
        room = np.asarray(room_m, float)
        if (guides.ndim != 2 or guides.shape[1:] != (2,) or not len(guides)
                or not np.isfinite(guides).all() or room.shape != (2,)
                or not np.isfinite(room).all() or np.any(room <= 0) or method not in METHODS):
            raise ValueError('Invalid static session geometry, guide state or update method')
        self._positions, self._room = guides.copy(), room.copy()
        self._config, self._method = copy.deepcopy(config), method
        self._ids = self._radii = self._demand = self._partition = None
        self._points, self._labels = np.empty((0, 2)), np.empty(0, int)
        self._time, self._index, self._halted = 0., 0, False
        self._frame_ids = set()
        self._step_lock = threading.Lock()
        self._binding = implementation_binding()
        self._journal = SessionJournal(artifact_root, {'contract': STATIC_CONTRACT,
            'room_m': room, 'initial_guides': guides, 'model': config, 'method': method,
            'execution_policy': 'joint_reassigned_then_reverse_v1', 'truth_input': False,
            'delivery_is_full_task_success': False,
            'journal_schema_version': 3, 'implementation_binding': self._binding,
            'provenance': provenance or {'scientific_source_snapshot': 'not_bound_by_caller'}})

    @classmethod
    def restore(cls, artifact_root, actual_positions, observed_time_s):
        header, state, journal = recover(artifact_root, actual_positions, observed_time_s)
        self = cls.__new__(cls)
        self._config, self._method = header['model'], header['method']
        self._room = np.asarray(header['room_m'], float)
        self._positions = np.array(actual_positions, float, copy=True)
        self._binding, self._journal = header['implementation_binding'], journal
        self._time, self._index = float(observed_time_s), journal._next_index
        self._step_lock = threading.Lock()
        if state is None:
            self._ids = self._radii = self._demand = self._partition = None
            self._points, self._labels = np.empty((0, 2)), np.empty(0, int)
            self._frame_ids, self._halted = set(), False
        else:
            self._ids = tuple(state['person_ids']) if state['person_ids'] is not None else None
            self._radii = np.asarray(state['radii'], float) if state['radii'] is not None else None
            self._demand = np.asarray(state['demand'], float) if state['demand'] is not None else None
            self._partition = tuple(tuple(rows) for rows in state['partition']) if state['partition'] is not None else None
            self._points = np.asarray(state['points'], float).reshape(-1, 2)
            self._labels = np.asarray(state['labels'], int)
            self._frame_ids, self._halted = set(state['consumed_frame_ids']), bool(state['halted'])
        return self

    def close(self):
        if not self._step_lock.acquire(blocking=False):
            raise RuntimeError('Cannot close a session while a step is in progress')
        try:
            self._journal.close()
        finally:
            self._step_lock.release()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _checkpoint(self):
        return {'positions': self._positions, 'time_s': self._time,
            'person_ids': self._ids, 'radii': self._radii, 'demand': self._demand,
            'partition': self._partition, 'points': self._points, 'labels': self._labels,
            'consumed_frame_ids': sorted(self._frame_ids), 'halted': self._halted}

    @property
    def positions(self):
        return immutable_array(self._positions)

    @property
    def next_observation_time_s(self):
        return self._time

    @property
    def halted(self):
        return self._halted

    def _canonical_measurement(self, frame):
        if frame.contract != STATIC_CONTRACT:
            raise ScientificFailure('STEP_SCOPE_VIOLATION', 'Contract requires an explicit stage adapter')
        if frame.frame_id in self._frame_ids:
            raise ScientificFailure('FRAME_ALREADY_CONSUMED', 'Observation frame cannot be silently replayed')
        if frame.time_s < self._time:
            raise ScientificFailure('OBSERVATION_RELEASE_VIOLATION', 'Acquire next frame after actual delivery ends')
        observation = frame.measurement.observation
        ids = frame.person_ids
        if self._ids is None:
            self._ids = tuple(sorted(ids))
        if set(ids) != set(self._ids):
            raise ScientificFailure('STATIC_IDENTITY_CHANGED', 'Person insertion or removal requires a new protocol')
        index = np.array([ids.index(identity) for identity in self._ids])
        canonical = Observation(observation.positions[index], observation.radii[index], observation.demand[index])
        if self._radii is None:
            self._radii, self._demand = canonical.radii.copy(), canonical.demand.copy()
        elif not np.array_equal(canonical.radii, self._radii) or not np.array_equal(canonical.demand, self._demand):
            raise ScientificFailure('STATIC_ATTRIBUTES_CHANGED', 'Exact static radii and planning demand must remain frozen')
        return BoundedMeasurement(canonical, frame.measurement.position_error_bound_m, frame.measurement.model)

    def step(self, frame):
        if not self._step_lock.acquire(blocking=False):
            raise RuntimeError('Concurrent step calls cannot share a physical session')
        try:
            return self._step(frame)
        finally:
            self._step_lock.release()

    def _step(self, frame):
        if self._journal.closed:
            raise RuntimeError('Closed session cannot release motion without a journal writer')
        if not isinstance(frame, ObservationFrame):
            raise TypeError('Use an explicit versioned ObservationFrame')
        index, start = self._index, self._positions.copy()
        try:
            self._journal.begin(index, frame)
        except Exception:
            self._halted = True
            raise
        self._index += 1
        result = Execution(np.asarray([start]), np.empty((0, len(start), 2)),
                           np.full(len(start), -1, int), 'NOT_EXECUTED', (), {})
        targets, labels, planning = np.empty((0, 2)), np.empty(0, int), {}
        metrics = {'contract': STATIC_CONTRACT, 'event': index, 'frame_id': frame.frame_id,
            'observation_time_s': frame.time_s, 'start_time_s': self._time,
            'deployment_completed': False, 'full_task_success': None,
            'full_task_success_requires_independent_evaluator': True}
        try:
            self._journal.check_head()
            if implementation_binding() != self._binding:
                raise RuntimeError('Session source or environment changed before motion')
            if self._halted:
                raise ScientificFailure('SESSION_HALTED', 'Preserved actual state requires an explicit recovery protocol')
            measurement = self._canonical_measurement(frame)
            self._frame_ids.add(frame.frame_id)
            groups, domain = construct_groups(measurement, self._room, self._config)
            partition = tuple(g.rows for g in groups)
            if self._partition is not None and partition != self._partition:
                raise ScientificFailure('PARTITION_CHANGED', 'Static observed group identity changed')
            self._partition = partition
            catalog = build_catalog(groups, measurement, domain, len(start), 'projected_mass', self._config)
            if not len(self._points):
                allocation = allocate(catalog, groups, len(start), 'minimax')
                pools = [phase_pool(g, measurement, domain, int(n), self._config)
                         for g, n in zip(groups, allocation.counts)]
                targets, labels, _, _ = choose_groups(pools, 'continuous_euclidean_greedy')
                planning = {'counts': allocation.counts, 'decision': 'initial_deployment',
                            'baseline_targets': targets, 'accepted_certificate': None}
            else:
                support = build_support(measurement, groups, self._config)
                targets, labels, planning = decide(measurement, groups, domain, catalog, len(start),
                    self._points, self._labels, self._config, support, self._method)
            result = execute_hybrid(start, targets, domain, self._room, self._config, measurement)
            self._positions = result.positions[-1].copy()
            self._time = frame.time_s + len(result.velocities) * self._config['dt_s']
            metrics['start_time_s'] = frame.time_s
            active = result.assignment >= 0
            error = float(np.linalg.norm(self._positions[active] - targets[result.assignment[active]], axis=1).max()) if active.any() else 0.
            delivered = result.status == 'ARRIVED'
            if delivered and error > self._config.get('tracking_bound_m', 1e-10):
                raise RuntimeError('Delivered state violated the declared tracking bound')
            metrics.update(status='DELIVERED_OBSERVATION_PLAN' if delivered else result.status,
                deployment_completed=delivered, tracking_error_m=error,
                simulation_time_s=len(result.velocities) * self._config['dt_s'],
                executed_intervals=len(result.velocities), canonical_person_ids=self._ids,
                accepted_service_certificate=bool(planning.get('accepted_certificate')))
            if delivered:
                self._points, self._labels = actual_targets(result, labels)
            else:
                self._halted = True
        except ScientificFailure as error:
            metrics.update(status=error.status, failure_message=str(error), failure_evidence=error.evidence,
                           deployment_completed=False)
            self._halted = True
        except Exception:
            metrics.update(status='EXPERIMENT_ERROR', traceback=traceback.format_exc(), deployment_completed=False)
            self._halted = True
        # Even an exception after partial motion must retain the actual state.
        self._positions = result.positions[-1].copy()
        metrics.update(end_time_s=self._time, session_halted=self._halted,
                       executed_intervals=len(result.velocities),
                       simulation_time_s=len(result.velocities) * self._config['dt_s'])
        frozen = Execution(immutable_array(result.positions), immutable_array(result.velocities),
            immutable_array(result.assignment, int), result.status, copy.deepcopy(result.paths), copy.deepcopy(result.evidence))
        record = DeliveryRecord(frame, frozen, immutable_array(targets), immutable_array(labels, int),
                                copy.deepcopy(planning), metrics)
        try:
            self._journal.append(index, record, self._checkpoint())
        except Exception:
            self._halted = True
            raise
        return record
