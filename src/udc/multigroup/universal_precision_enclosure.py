"""Scale-independent coordinate bounds for finite fixed-precision maps.

With u=2**-53 and eta=2**-1075, every positive finite scale/grid map
Q(x)=RN(J(RN(x*S))/S) or RN(J(RN(x/G))*G) obeys
|Q(x)| <= 2*(1+u)**2*|x|+eta, conditional on finite operations.
J is nearest-integer, ties toward +infinity. A nonzero J(t) implies
|t|>=1/2 and |J(t)|<=2*|t|. Its pre-rounded multiplication/division is
therefore normal, so the first operation needs no eta/S or eta*G term.
Zero output and sign preservation are treated separately.

These are deliberately coarse postconditions, not an accurate service bound.
They neither prove library termination nor bound unreviewed XY producers.
"""
from .offset_interval import Interval,U,ETA
from .circle_source_enclosure import circular_source_box


UNIVERSAL_GAIN=2*(1+U)**2


def unknown_precision_box(box,*,stages=1):
    """Enclose finite outputs under at most stages unknown positive maps.

    Each stage may instead be FLOATING identity; parameters may change between
    stages. The input box can contain real endpoints, while the supplied
    geometry coordinates and every actual quantizer input must be binary64.
    Nonzero offsets, FLOATING_SINGLE and arbitrary geometry operations are
    outside this contract. Returning a box does not prove any input feasible.
    """
    if len(box)!=2 or any(not isinstance(q,Interval) for q in box) or type(stages) is not int or stages<0:
        raise ValueError('A 2D interval box and nonnegative integer stage count required')
    result=tuple(box)
    for _ in range(stages):
        result=tuple(Interval(UNIVERSAL_GAIN*q.lo-ETA if q.lo<0 else 0,
            UNIVERSAL_GAIN*q.hi+ETA if q.hi>0 else 0) for q in result)
    return result


def default_point_buffer_box(center_box,distance):
    """Conditional source-level box for default round Point.buffer XY output.

    Initial Point factory must be FLOATING, round cap and non-single-sided.
    Assume reviewed GEOS 3.14.1 source paths, unit-range finite trig, finite
    positive fallback models, and ordinary XY copy/storage semantics.
    Original precision uses axis-preserving floating noding. Reduced precision
    applies a point quantizer to raw arc coordinates, then a zero-offset scaled
    unit snap-rounding noder. Reviewed graph/ring assembly copies those XYs.
    Two unknown stages cover both routes without selecting fallback scale.
    Does not apply to polygon/line buffers, later unions/offsets, or service mass.
    """
    return unknown_precision_box(circular_source_box(center_box,distance),stages=2)
