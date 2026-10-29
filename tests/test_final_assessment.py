from udc.analysis.final_assessment import paired_interval


def test_final_paired_interval_weights_strata_and_preserves_zero_effect():
    strata={(1,'circle'),(5,'concave')}
    values={(1,'circle',1):0.,(1,'circle',2):0.,(5,'concave',1):0.}
    r=paired_interval(values,strata,100,7)
    assert r['estimate']==0 and r['ci_95']==[0.,0.] and r['paired_blocks']==3


def test_missing_prespecified_stratum_refuses_an_optimistic_interval():
    r=paired_interval({(1,'circle',1):-100.},{(1,'circle'),(5,'concave')},100,7)
    assert r['estimate'] is None and r['ci_95'] is None and r['reason']=='missing_prespecified_strata'


def test_physical_strata_not_individual_observations_determine_weight():
    values={(1,'circle',1):2.,(1,'circle',2):2.,(5,'concave',1):0.}
    r=paired_interval(values,{(1,'circle'),(5,'concave')},100,7)
    assert r['estimate']==1. and r['ci_95']==[1.,1.]
