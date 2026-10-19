"""Static admission, positive-length curve conflicts, and explicit phase repair."""
import json

import numpy as np
import pytest
import yaml
from shapely.geometry import Point, Polygon

from udc.boundary import estimate_disk_union
from udc.deployment.admissible import admissible_phase
from udc.deployment.planners import periodic_gaps
from udc.experiments.nonconvex_rebuild import draw_cell, load_protocol, plan_target
from udc.experiments.scene_closure import _execution_config
from udc.experiments.runner import experiment, reevaluate_result
from udc.experiments.target_domain import curve_domain_relation, independent_geometry
from udc.navigation import navigation_domain, navigate_and_assign, route_assigned
from udc.navigation.paths import target_domain_check, TargetDomainFailure


@pytest.fixture(scope='module')
def reconstructed_seed51():
    spec, base = load_protocol('configs/step1/nonconvex_execution_confirmation_v3.yaml')
    scene = next(item for item in base['scenes'] if item['name'] == 'elongated')
    data, _ = draw_cell(spec, base, scene, 51)
    b = estimate_disk_union(data['people'], 1.3, .1, data['room'], .55, .75,
                            data['radii'], closing_radius=.3, quad_segs=16)
    plan, _ = plan_target(b, 'equal_arc', 11, spec, base)
    d = navigation_domain(data['room'], .55, Polygon(b.crowd), .55)
    return data, b, plan, d, base


def test_mitre_conflict_is_positive_length_not_simplification_or_roundoff(reconstructed_seed51):
    _, b, plan, d, _ = reconstructed_seed51
    target = Point(plan.positions[0])
    assert Polygon(b.deployment).exterior.distance(target) == 0.
    assert d.physical_free.covers(target)
    assert not d.physical_obstacle.simplify(.1, preserve_topology=True).covers(target)
    assert d.signed_free_distance(plan.positions[0]) == pytest.approx(-.00546199584443784, abs=1e-12)
    assert d.physical_obstacle.buffer(.11, join_style='mitre').covers(target)
    relation = curve_domain_relation(b, d)
    assert relation['wraps_seam'] and len(relation['intervals']) == 2
    assert relation['strict_illegal_length_m'] == pytest.approx(.053147609654287976, abs=1e-10)
    assert relation['intersection_length_residual_m'] < 1e-10
    assert relation['max_penetration_upper_m'] - relation['max_penetration_lower_m'] <= 5e-6


def test_static_check_before_assignment_and_replan_uses_existing_tolerance(monkeypatch):
    import udc.navigation.paths as paths
    crowd = Polygon([(4, 3), (6, 3), (6, 7), (4, 7)])
    domain = navigation_domain([10, 10], .2, crowd, .2)
    x = domain.conservative_obstacle.bounds[0]
    targets = np.array([[x, 5], [x + .5e-7, 5], [x + 2e-7, 5]])
    report = target_domain_check(domain, targets, np.array([0., 1., 2.]))
    assert report['invalid_target_indices'] == [2]
    assert report['targets'][2]['arc_m'] == 2.
    assert report['geometry_sha256'] == domain.geometry_sha256
    monkeypatch.setattr(paths, '_visibility_graph', lambda *a, **k: pytest.fail('searched before static rejection'))
    for call in (
        lambda: navigate_and_assign(np.array([[1., 1.]]), targets, [10, 10], .2, crowd, .2),
        lambda: route_assigned(np.array([[1., 1.]]), targets, np.array([2]), [10, 10], .2, crowd, .2),
    ):
        with pytest.raises(TargetDomainFailure) as rejected:
            call()
        assert rejected.value.diagnosis['invalid_target_indices'] == [2]


def test_explicit_phase_preserves_curve_count_and_cyclic_gaps(reconstructed_seed51):
    _, b, plan, domain, base = reconstructed_seed51
    candidate, detail = admissible_phase(b, plan, domain, np.ones(len(b.deployment)),
        phase_bins=256, gap_limit=1.94, separation=.75, quadrature_samples=base['quadrature_samples'])
    assert candidate is not None and detail['changed'] and len(detail['attempts']) == 256
    assert candidate.method == 'equal_arc+static_domain_phase_v1'
    assert len(candidate.positions) == len(plan.positions) == 11
    assert independent_geometry(b, plan, candidate, domain, base)['accepted']
    np.testing.assert_allclose(np.sort(periodic_gaps(plan.arc_positions, b.length)),
                               np.sort(periodic_gaps(candidate.arc_positions, b.length)), atol=1e-12)
    assert detail['constant_density_exact_cost'] == pytest.approx(
        np.sum(periodic_gaps(plan.arc_positions, b.length)**3)/(12*b.length))
    np.testing.assert_array_equal(plan.positions[0], b.deployment[0])


def test_static_rejection_has_original_targets_and_failure_artifacts(tmp_path, reconstructed_seed51):
    data, boundary, plan, domain, base = reconstructed_seed51
    cfg = _execution_config(tmp_path, data['people'], np.array([[0,0],[30,0],[30,30],[0,30]]), 11, 51, base)
    config = yaml.safe_load(cfg.read_text())
    config['guides']['initial_positions'] = data['guides'].tolist()
    cfg.write_text(yaml.safe_dump(config), encoding='utf-8')
    result_dir = tmp_path / 'result'
    result = experiment(cfg, result_dir, plots=False, protocol='fixed_n', fixed_n=11,
        prescribed_boundary=boundary, prescribed_plan=plan, boundary_source='estimated_disk_union')
    assert result['termination_status'] == 'TARGET_DOMAIN_INVALID'
    assert result['steps'] == 0 and not result['assignment_found'] and not result['success']
    assert result['failure_diagnosis']['targets'][0]['arc_m'] == 0.
    assert result['failure_diagnosis']['geometry_sha256'] == domain.geometry_sha256
    assert result['target_domain']['invalid_target_indices'] == [0]
    assert not result['planning_attempts'][0]['accepted']
    assert all((result_dir / file).is_file() for file in
        ('metrics.json', 'state.json', 'planning.json', 'trajectory.npz', 'config.yaml'))
    with np.load(result_dir / 'trajectory.npz') as saved:
        np.testing.assert_array_equal(saved['targets'], plan.positions)
        np.testing.assert_array_equal(saved['positions'][0], data['guides'])
    assert reevaluate_result(result_dir)['criteria_consistent']


def test_exhausted_phase_budget_is_not_an_infeasibility_proof(reconstructed_seed51):
    _, b, plan, _, _ = reconstructed_seed51
    domain = navigation_domain([2, 2], .55, Polygon(b.crowd), .55)
    candidate, details = admissible_phase(b, plan, domain, np.ones(len(b.deployment)),
        phase_bins=8, gap_limit=1.94, separation=.75, quadrature_samples=64)
    assert candidate is None and len(details['attempts']) == 8
    assert 'no infeasibility proof' in details['proof_scope']


def test_original_planner_exhaustion_keeps_scientific_failure_accounting(tmp_path, monkeypatch, reconstructed_seed51):
    from udc.experiments import target_domain as protocol
    from udc.experiments.nonconvex_rebuild import sha, array_hash, write
    data, boundary, _, _, base = reconstructed_seed51
    source = tmp_path/'reconstructed_v3'
    cell = source/'inputs/elongated/seed_51'
    cell.mkdir(parents=True)
    np.savez(cell/'input.npz', **data)
    np.savez(cell/'boundary.npz', crowd=boundary.crowd, deployment=boundary.deployment,
              arc=boundary.arc, length=np.array(boundary.length))
    _execution_config(cell, data['people'], np.array([[0,0],[30,0],[30,30],[0,30]]), 11, 51, base)
    write(cell/'input_record.json', {'status':'ACCEPTED', 'input_file_sha256':sha(cell/'input.npz'),
        'config_sha256':sha(cell/'input.yaml')})
    write(source/'protocol.json', {'spec':{}, 'base_spec':base})
    monkeypatch.setattr(protocol, 'plan_target', lambda *args: (None, {'accepted':False,'bounded':True}))
    row={'task_id':'exhausted','scene':'elongated','seed':51,'method':'pairwise_euclidean',
         'count':11,'cell':'inputs/elongated/seed_51','input_sha256':array_hash(people=data['people'])}
    result=protocol.run_case((str(tmp_path), row, 'regression', {}))
    assert result['termination_status']=='PLAN_SEARCH_EXHAUSTED' and not result['success']
    directory=tmp_path/'regression/elongated/seed_51/pairwise_euclidean'
    assert not (directory/'runner_error.txt').exists()
    assert (directory/'trajectory.npz').is_file() and result['offline_consistent']
    assert json.loads((directory/'original_solver.json').read_text())['bounded']
