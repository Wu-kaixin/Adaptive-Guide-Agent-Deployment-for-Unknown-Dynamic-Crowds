"""Observation-only connectivity from collective uncertainty-disk coverage.

Several already connected neighbours can jointly guarantee an attachment even
when none is individually a guaranteed edge. No true groups or reference
construction success condition is used in this sufficient certificate.
"""
import numpy as np
from shapely.geometry import Point
from shapely.ops import unary_union

from .geometry import outer_disk


def certificate(observation, partition, link, epsilon, base):
    if link <= epsilon or epsilon <= 0 or not base['cross_groups_separated_at_expanded_link']:
        return {**base, 'collective_coverage_attempted': False}
    radius = link - epsilon - 1e-9
    reach = [Point(p).buffer(radius, quad_segs=32) for p in observation.positions]
    uncertain = [outer_disk(p, epsilon, quad_segs=32) for p in observation.positions]
    distance = np.linalg.norm(observation.positions[:, None] - observation.positions[None], axis=2)
    adjacent = distance <= link - 2 * epsilon
    evidence, all_connected = [], True
    for rows in partition:
        components, remaining = [], {int(row) for row in rows}
        while remaining:
            first = min(remaining)
            component, todo = {first}, [first]
            remaining.remove(first)
            while todo:
                node = todo.pop()
                neighbors = {int(value) for value in np.flatnonzero(adjacent[node])} & remaining
                remaining -= neighbors
                component |= neighbors
                todo.extend(sorted(neighbors))
            components.append(component)
        components.sort(key=lambda group: (-len(group), min(group)))
        connected = set(components.pop(0))
        initial = sorted(connected)
        attachments = []
        while components:
            available = unary_union([reach[row] for row in sorted(connected)])
            accepted = None
            for index, component in enumerate(components):
                for row in sorted(component):
                    # Strict full support containment; no area threshold and
                    # no Boolean covers-only admission.
                    if uncertain[row].difference(available).is_empty and available.covers(uncertain[row]):
                        accepted = index, row
                        break
                if accepted is not None:
                    break
            if accepted is None:
                break
            index, row = accepted
            added = components.pop(index)
            attachments.append({'witness_row': row, 'added_component': sorted(added),
                                'connected_before': sorted(connected)})
            connected |= added
        passed = not components
        all_connected &= passed
        evidence.append({'rows': [int(row) for row in rows], 'initial_guaranteed_component': initial,
                         'attachments': attachments, 'certified': passed,
                         'unresolved_components': [sorted(c) for c in components]})
    return {**base, 'certified': bool(all_connected), 'collective_coverage_attempted': True,
        'collective_coverage_groups': evidence,
        'collective_coverage_within_connected': bool(all_connected),
        'identity_method': 'collective_uncertainty_coverage_v1',
        'scope': 'observation-only robust connected components under independent bounded moves; grouping only'}
