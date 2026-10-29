"""Conditional binary64 axis bounds for circular-arc source coordinates.

For a binary64 center in the supplied box, nonnegative binary64 distance d
and finite trig result t in [-1,1], both RN(c + RN(d*t)) and RN(c+d*t) lie
between RN(c_min-d) and RN(c_max+d). This covers separate and fused arithmetic
without assuming correctly rounded sin/cos. It DOES assume their stated range.
The optional precision model represents OffsetSegmentString.makePrecise.
No BufferOp fallback selection, unions, later offsets or reference mass is
inferred from this local source-coordinate contract.
"""
from .offset_interval import Interval
from .precision_enclosure import PrecisionModel,_representable_inside,_rn


def circular_source_box(center_box,distance,*,precision: PrecisionModel | None=None):
    """Enclose all source vertices, deletion/closure copies and exact chords.

    Distance is the actual binary64 value passed to the native buffer, not an
    ideal body radius or an assumption about a circumscribed construction.
    Empty returned geometry, correct topology, and runtime certification are
    outside this postcondition. Binary64 interpolation needs its own contract.
    """
    distance=Interval.point(distance).lo
    if (len(center_box)!=2 or any(not isinstance(q,Interval) for q in center_box)
            or distance<0 or (precision is not None and not isinstance(precision,PrecisionModel))):
        raise ValueError('A 2D interval box and finite nonnegative binary64 distance required')
    result=[]
    for axis in center_box:
        lo,hi=_representable_inside(axis)
        raw=Interval(_rn(lo-distance),_rn(hi+distance))
        result.append(raw if precision is None else precision.range(raw))
    return tuple(result)
