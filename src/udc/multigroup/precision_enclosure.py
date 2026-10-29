"""Exact-value contracts for a pinned GEOS fixed-precision scalar model.

Models positive scale construction, the library's near-integer normalization,
Java-style half ties toward +infinity, and binary64 nearest basic operations.
It does not infer BufferOp's selected scale or certify a whole snap-rounder.
The rational values intentionally identify +0 and -0 as the same XY value.
"""
from dataclasses import dataclass
from fractions import Fraction as F
import math

from .offset_interval import Interval,UncertifiedArithmetic,U,ETA


def _rn(value):
    try:result=float(F(value))
    except OverflowError as exc:raise UncertifiedArithmetic('precision_binary64_overflow') from exc
    if not math.isfinite(result):raise UncertifiedArithmetic('precision_binary64_overflow')
    return F.from_float(result)


def _java_round(value):
    # Compute the integer from exact binary input, never fl(value + .5).
    integer=(value+F(1,2)).numerator//(value+F(1,2)).denominator
    return _rn(integer)


def _snap(value):
    # Constructor scale/grid is positive, so std::round's ties-away rule has
    # the same value as Java's ties-up rule here. The strict 1e-5 is an
    # existing GEOS constructor parameter, not a new acceptance tolerance.
    integer=_java_round(value)
    return integer if abs(_rn(value-integer))<F.from_float(1e-5) else value


def _representable_inside(interval):
    try:lo,hi=float(interval.lo),float(interval.hi)
    except OverflowError as exc:raise UncertifiedArithmetic('precision_input_overflow') from exc
    if not math.isfinite(lo) or not math.isfinite(hi):raise UncertifiedArithmetic('precision_input_overflow')
    if F.from_float(lo)<interval.lo:lo=math.nextafter(lo,math.inf)
    if F.from_float(hi)>interval.hi:hi=math.nextafter(hi,-math.inf)
    if lo>hi:raise UncertifiedArithmetic('interval_has_no_binary64_input')
    return F.from_float(lo),F.from_float(hi)


@dataclass(frozen=True)
class PrecisionModel:
    scale: F
    grid: F
    requested_grid: F | None=None

    @classmethod
    def from_scale(cls,scale):
        scale=Interval.point(scale).lo
        if scale<=0:raise ValueError('Positive finite fixed scale required')
        if scale<1:
            grid=_snap(_rn(1/scale))
        else:
            scale=_snap(scale);grid=_rn(1/scale)
        return cls(scale,grid)

    @classmethod
    def from_grid(cls,grid):
        requested=Interval.point(grid).lo
        if requested<0:raise ValueError('Nonnegative finite grid required')
        if requested==0:return cls(F(0),F(0),requested)
        base=cls.from_scale(float(_rn(1/requested)))
        return cls(base.scale,base.grid,requested)

    @property
    def branch(self):
        return 'floating' if self.scale==0 else 'coarse_grid' if self.grid>1 else 'scaled_fine'

    @property
    def reported_grid(self):
        return F(0) if self.scale==0 else _rn(1/self.scale)

    def quantize(self,value,*,map_kind='point'):
        value=F(value)
        if _rn(value)!=value:raise ValueError('Scalar quantizer inputs must be binary64 values')
        if map_kind not in ('point','scaled_noder'):raise ValueError('Unknown precision map')
        if self.scale==0:return value
        if map_kind=='point' and self.grid>1:
            return _rn(_java_round(_rn(value/self.grid))*self.grid)
        return _rn(_java_round(_rn(value*self.scale))/self.scale)

    def range(self,interval,*,map_kind='point'):
        lo,hi=_representable_inside(interval)
        return Interval(self.quantize(lo,map_kind=map_kind),self.quantize(hi,map_kind=map_kind))

    def displacement_bound(self,interval):
        # Establish finite operation ranges before applying the absolute bound.
        self.range(interval)
        if self.scale==0:return F(0)
        magnitude=max(abs(interval.lo),abs(interval.hi))
        if self.grid>1:
            first=U*magnitude+ETA*self.grid+self.grid/2
        else:
            first=U*magnitude+ETA/self.scale+1/(2*self.scale)
        return first+U*(magnitude+first)+ETA

    def serial(self):
        return {'scale_fraction':str(self.scale),'actual_grid_fraction':str(self.grid),
            'requested_grid_fraction':str(self.requested_grid) if self.requested_grid is not None else None,
            'reported_grid_fraction':str(self.reported_grid),'branch':self.branch}


def invariant_axis_box(box,models,*,max_steps=8,include_scaled_noder=False):
    """Enlarge until every declared monotone quantizer maps the box into itself.

    Returning a box proves closure for arbitrarily many repetitions of these
    given maps and operations already known to preserve an axis box. It does
    not prove that an uninspected GEOS algorithm uses only those operations.
    """
    if len(box)!=2 or not models or type(max_steps) is not int or max_steps<1:
        raise ValueError('2D box, nonempty precision models and finite iteration budget required')
    current=tuple(box)
    maps=('point','scaled_noder') if include_scaled_noder else ('point',)
    for step in range(max_steps):
        ranges=[[model.range(q,map_kind=kind) for model in models for kind in maps] for q in current]
        expanded=tuple(Interval(min([q.lo]+[r.lo for r in values]),max([q.hi]+[r.hi for r in values]))
            for q,values in zip(current,ranges))
        if expanded==current:return current,step+1
        current=expanded
    raise UncertifiedArithmetic('quantizer_axis_invariance_not_established_within_budget')
