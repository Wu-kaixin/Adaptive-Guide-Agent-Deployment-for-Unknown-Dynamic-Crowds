"""Structural outer sets for GEOS 3.14.1 floating/no-precision intersectors.

The normal path checks the coordinate envelopes of both segments. Its failure
and null-result paths choose an input endpoint; endpoint/collinear paths also
copy endpoints. Thus returned finite XY values lie in the union below without
assuming correctly rounded exact intersection or a particular DD error bound.
This does not assert existence of an intersection or cover fixed precision.
"""
from dataclasses import dataclass
from fractions import Fraction as F
import math

from .offset_interval import Interval


@dataclass(frozen=True)
class IntersectorEnvelope:
    endpoint_boxes: tuple
    normal_box: tuple | None
    contract: str='GEOS 3.14.1 LineIntersector XY; null/failed envelope -> input endpoint; precision none or FLOATING'

    @property
    def components(self):
        return self.endpoint_boxes+((self.normal_box,) if self.normal_box is not None else ())

    def contains(self,point):
        if len(point)!=2 or not all(math.isfinite(float(v)) for v in point):
            return False
        p=tuple(F.from_float(float(v)) for v in point)
        return any(all(q.contains(v) for q,v in zip(box,p)) for box in self.components)

    def support_upper(self,direction):
        if len(direction)!=2 or not any(direction) or any(type(n) is not int for n in direction):
            raise ValueError('A nonzero integer 2D direction is required')
        return max(sum(n*(q.hi if n>=0 else q.lo) for n,q in zip(direction,box)) for box in self.components)

    def outer_axis_box(self):
        return tuple(Interval(min(box[j].lo for box in self.components),max(box[j].hi for box in self.components)) for j in range(2))

    def serial(self):
        return {'endpoint_boxes':[[q.serial() for q in p] for p in self.endpoint_boxes],
            'normal_box':[q.serial() for q in self.normal_box] if self.normal_box is not None else None,
            'intersection_may_be_empty':True,'contract':self.contract}


def enclosure(endpoints,*,precision_model):
    if precision_model not in ('none','floating'):
        raise ValueError('Fixed or unverified precision models are outside this contract')
    endpoints=tuple(tuple(p) for p in endpoints)
    if len(endpoints)!=4 or any(len(p)!=2 or any(not isinstance(q,Interval) for q in p) for p in endpoints):
        raise ValueError('Four endpoint boxes with two rational intervals each are required')
    a,b,c,d=endpoints
    lower=[max(min(a[j].lo,b[j].lo),min(c[j].lo,d[j].lo)) for j in range(2)]
    upper=[min(max(a[j].hi,b[j].hi),max(c[j].hi,d[j].hi)) for j in range(2)]
    normal=None if any(lo>hi for lo,hi in zip(lower,upper)) else tuple(Interval(lo,hi) for lo,hi in zip(lower,upper))
    return IntersectorEnvelope(endpoints,normal)
