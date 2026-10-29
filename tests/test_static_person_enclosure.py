from fractions import Fraction as F

import numpy as np
import pytest

from udc.interfaces import Observation
from udc.multigroup.foundation_contract import ObservationFrame
from udc.multigroup.observation import BoundedMeasurement
from udc.multigroup.offset_interval import Interval
from udc.multigroup.static_person_enclosure import static_person_boxes


def frame(name,points,error=1.,ids=(10,),radii=None,demand=None,time=0.):
    n=len(points)
    obs=Observation(np.asarray(points,float),np.array(radii if radii is not None else [.25]*n),
        np.array(demand if demand is not None else [1.]*n))
    return ObservationFrame(name,time,ids,BoundedMeasurement(obs,error,'test'))


def test_tangent_error_disks_identify_point_without_zero_sensor_error():
    result=static_person_boxes([frame('a',[[3,3]]),frame('b',[[5,3]])],[10,10])
    assert result.witness is None
    assert result.boxes==((Interval(4,4),Interval(3,3)),)
    assert result.serial()['service_certificate'] is False


def test_disjoint_diagonal_disks_have_exact_inconsistency_witness():
    result=static_person_boxes([frame('a',[[3,3]],.5),frame('b',[[3.75,3.75]],.5)],[10,10])
    assert result.witness is not None
    assert result.serial()['status']=='INCONSISTENT_STATIC_OBSERVATIONS'
    assert result.boxes==()


def test_id_reordering_and_body_wall_bounds_preserve_all_real_candidates():
    first=frame('a',[[.5,2],[7,7]],.5,ids=(10,20),radii=[.4,.25])
    second=frame('b',[[7,7],[.5,2]],.5,ids=(20,10),radii=[.25,.4])
    result=static_person_boxes([first,second],[10,10])
    assert result.person_ids==(10,20)
    assert result.boxes[0][0].lo==F.from_float(.4)
    for x,y in [[.4,2],[.5,2],[.75,2.25],[.5,1.5]]:
        assert result.boxes[0][0].contains(F.from_float(x))
        assert result.boxes[0][1].contains(F.from_float(y))


def test_finite_sweeps_only_tighten_the_outer_box_and_do_not_prove_feasibility():
    history=[frame('a',[[3,3]]),frame('b',[[4,3.5]])]
    one=static_person_boxes(history,[10,10],max_sweeps=1)
    many=static_person_boxes(history,[10,10],max_sweeps=8)
    for outer,inner in zip(one.boxes[0],many.boxes[0]):
        assert outer.lo<=inner.lo<=inner.hi<=outer.hi
    assert many.serial()['feasibility_proved'] is False


def test_changed_static_metadata_or_scope_and_duplicate_frames_refuse():
    original=frame('a',[[3,3]])
    for bad in [frame('b',[[3,3]],demand=[2.]),frame('b',[[3,3]],ids=(20,)),original]:
        with pytest.raises(ValueError):static_person_boxes([original,bad],[10,10])
    moving=ObservationFrame('b',0.,(10,),original.measurement,contract='udc.step2.dynamic')
    with pytest.raises(ValueError):static_person_boxes([original,moving],[10,10])


def test_pairwise_lens_preserves_coupling_lost_by_coordinate_propagation():
    history=[frame('a',[[3,3]]),frame('b',[[3,4]])]
    separate=static_person_boxes(history,[10,10])
    joint=static_person_boxes(history,[10,10],use_pairwise_lenses=True)
    assert separate.boxes[0][0]==Interval(2,4)
    assert 2<joint.boxes[0][0].lo<3<joint.boxes[0][0].hi<4
    assert any(r['kind']=='disk_pair_tightening' for r in joint.trace)
