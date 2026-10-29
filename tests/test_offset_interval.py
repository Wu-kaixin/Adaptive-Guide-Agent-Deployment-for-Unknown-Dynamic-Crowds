from fractions import Fraction as F
import math

import pytest

from udc.multigroup.offset_interval import (Interval,UncertifiedArithmetic,outward,fl_add,fl_square,
    fl_sqrt,point_box,offset_segment,rounded_exact_intersection)


def test_outward_projection_keeps_negative_values_and_subnormal_rounding_terms():
    for lo,hi in [(F(-1,3),F(2,7)),(F(1,2**1075),F(1,2**1074)),(F(-1,2**1075),F(0))]:
        value=outward(lo,hi)
        assert value.lo<=lo<=hi<=value.hi
    exact=F.from_float(2.**-1074)
    assert fl_add(Interval(exact,exact),Interval(-exact,-exact)).contains(0)


def test_squared_interval_remembers_same_operand_and_sqrt_brackets_are_exact():
    value=fl_square(Interval(F(-2),F(3)))
    assert value.lo==0 and value.hi>=9
    for q in [F(2),F(1,3),F(2**200),F(0)]:
        bracket=fl_sqrt(Interval(q,q))
        assert bracket.lo**2<=q<=bracket.hi**2


def test_offset_enclosure_contains_all_endpoint_box_corners():
    a,b=[1.,2.],[4.,6.]
    bounds=offset_segment(point_box(a,.001),point_box(b,.001),Interval.point(.4),-1)
    error=F.from_float(.001)
    from itertools import product
    for signs in product((-1,1),repeat=4):
        # Exact dyadic points inside the declared rational boxes, then ordinary
        # binary64 source expression; no random generator or geometry oracle.
        aa=[float(F.from_float(v)+signs[j]*error/2) for j,v in enumerate(a)]
        bb=[float(F.from_float(v)+signs[2+j]*error/2) for j,v in enumerate(b)]
        dx,dy=bb[0]-aa[0],bb[1]-aa[1]
        length=math.sqrt(dx*dx+dy*dy)
        ux,uy=-.4*dx/length,-.4*dy/length
        for enclosure,p in zip(bounds,(aa,bb)):
            assert enclosure[0].contains(F.from_float(p[0]-uy))
            assert enclosure[1].contains(F.from_float(p[1]+ux))


def test_possible_zero_length_refuses_the_full_endpoint_set():
    with pytest.raises(UncertifiedArithmetic,match='norm_interval_contains_zero'):
        offset_segment(point_box([0.,0.],.1),point_box([.1,0.],.1),Interval.point(.4),1)


def test_exact_intersection_and_possible_parallelism_have_distinct_outcomes():
    box=point_box
    point,t,s=rounded_exact_intersection(box([0.,0.]),box([1.,1.]),box([0.,1.]),box([1.,0.]))
    assert all(q.contains(F(1,2)) for q in point)
    assert t.contains(F(1,2)) and s.contains(F(1,2))
    with pytest.raises(UncertifiedArithmetic,match='denominator_interval_contains_zero'):
        rounded_exact_intersection(box([0.,0.],.1),box([1.,0.],.1),box([0.,1.],.1),box([1.,1.],.1))


def test_overflow_and_nonfinite_inputs_cannot_produce_a_bound():
    from udc.multigroup.offset_interval import rn
    with pytest.raises(UncertifiedArithmetic,match='overflow_contract'):
        rn(Interval(F(2**1023),F(2**1023)))
    with pytest.raises(ValueError,match='Finite'):
        Interval.point(float('inf'))


def test_analytic_normal_cap_tightens_dependency_blowup_without_excluding_traces():
    a,b=[12.101687521832964,17.37917814865127],[12.14,17.367]
    aa,bb=point_box(a,.001),point_box(b,.001)
    loose=offset_segment(aa,bb,Interval.point(.4),-1)
    tight=offset_segment(aa,bb,Interval.point(.4),-1,tighten_normal=True)
    assert all(t.lo>=l.lo and t.hi<=l.hi for p,q in zip(tight,loose) for t,l in zip(p,q))
    from itertools import product
    for signs in product((-1,1),repeat=4):
        pa=[v+signs[j]*.0009 for j,v in enumerate(a)]
        pb=[v+signs[2+j]*.0009 for j,v in enumerate(b)]
        dx,dy=pb[0]-pa[0],pb[1]-pa[1]
        length=math.sqrt(dx*dx+dy*dy)
        ux,uy=-.4*dx/length,-.4*dy/length
        for enclosure,p in zip(tight,(pa,pb)):
            assert enclosure[0].contains(F.from_float(p[0]-uy))
            assert enclosure[1].contains(F.from_float(p[1]+ux))
