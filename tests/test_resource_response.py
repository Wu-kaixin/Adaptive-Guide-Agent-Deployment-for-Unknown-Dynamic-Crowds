import numpy as np
import pytest
from udc.experiments.resource_response import extend_guides, quantity
from udc.analysis.resource_response import units, pairs, interactions, common_target, bootstrap


def test_nested_extension_keeps_ids_and_splits_largest_gap_with_fixed_tie_order():
    source={'streams':{'inset_line':[[0.,0.],[0.,4.],[4.,4.],[4.,0.],[0.,0.]]}}
    data={'guides':np.array([[0.,0.],[4.,4.]]),'guide_arcs':np.array([0.,8.])}
    guides,arcs,added=extend_guides(data,source)
    assert np.array_equal(guides[:2],data['guides'])
    assert arcs.tolist()==[0.,8.,4.,12.,2.,6.]
    assert [r['id'] for r in added]==[2,3,4,5]
    assert np.array_equal(extend_guides(data,source,2)[0],guides[:4])


def test_quantity_distinguishes_planning_from_acceptance_and_never_proves_feasibility():
    q=quantity(35.5,18)
    assert q['planning_lower']==19 and q['acceptance_lower']==18
    assert not q['planning_quantity_not_excluded'] and q['acceptance_quantity_not_excluded']
    assert quantity(35.5,19)['diagnosis']=='数量条件未排除'
    assert quantity(None,18)['curve_length_m'] is None


def sample():
    rows=[]
    for scene in ('elongated','tight_u','wide_u'):
        for seed in (75,76,77):
            for n in (0,2,4):
                for method in ('equal_arc','projected_mass'):
                    rows.append({'scene':scene,'seed':seed,'physical_key':f'{scene}{seed}',
                        'increment':n,'count':11+n,'method':method,'target_valid':True,'success':True,
                        'planned_cost':{'demand':.3-n*.01-(.01-n*.001 if method=='projected_mass' else 0)}})
    return rows


def test_own_and_common_target_subsets_keep_failed_execution_planning_cost():
    rows=sample(); rows[5].update(success=False)
    rows[11].update(target_valid=False)
    us=units(rows)
    assert len(pairs(us,0))==9 and len(pairs(us,4))==8
    assert len(pairs(us,0,common=True))==8
    assert len(pairs(us,4,common=True))==8
    assert len(interactions(us,4,True))==8
    assert not rows[5]['success'] and pairs(us,4)[0]['projected_success'] is False
    assert sum(common_target(u) for u in us)==8


def test_resource_interaction_is_difference_of_paired_differences():
    ps=interactions(units(sample()),4,True)
    assert all(r['difference']==pytest.approx(.004) for r in ps)
    settings={'bootstrap_rng_seed':2026102002,'bootstrap_repetitions':1000}
    assert bootstrap(units(sample()),settings)==bootstrap(units(sample()),settings)
    assert bootstrap(units(sample()),settings)['estimates']['interaction_N4']['interval']==pytest.approx([.004,.004])


def test_rejected_reference_rejudge_is_read_only_and_keeps_null_motion(tmp_path):
    from udc.experiments.nonconvex_rebuild import rejected_result, sha
    from udc.experiments.resource_response import reference_rejudge
    data={'people':np.array([[2.,2.]]),'radii':np.array([.2]),'demand':np.array([5.]),
        'guides':np.array([[1.,1.],[1.,4.],[4.,4.]]),'room':np.array([6.,6.])}
    cfg=tmp_path/'input.yaml'; cfg.write_text('step: 1\n')
    directory=tmp_path/'old'
    rejected_result(directory,{'task_id':'old','count':3,'input_sha256':'frozen'},tmp_path,
        'BOUNDARY_INVALID','BOUNDARY_INVALID: frozen refusal',snapshot=data,config_path=cfg)
    before={p.name:sha(p) for p in directory.iterdir() if p.is_file()}
    r=reference_rejudge(directory)
    assert r['criteria_consistent'] and r['diagnostics_consistent'] and r['success_consistent']
    assert not r['success'] and not r['executed'] and r['motion'] is None
    assert {p.name:sha(p) for p in directory.iterdir() if p.is_file()}==before


def test_bootstrap_whole_unit_preserves_correlated_missing_N():
    rows=sample()
    for r in rows:
        if r['seed']==77: r['target_valid']=False
    result=bootstrap(units(rows),{'bootstrap_rng_seed':2026102002,'bootstrap_repetitions':1000})
    # Same sampled indices carry all N, so missing-stratum replicates coincide.
    assert len({v['uncalculable'] for v in result['estimates'].values()})==1
    assert result['whole_physical_unit']
