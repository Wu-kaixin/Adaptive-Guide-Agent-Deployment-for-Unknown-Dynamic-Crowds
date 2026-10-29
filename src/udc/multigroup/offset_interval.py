"""Conditional rational interval contracts for local offset expressions.

The binary64 offset follows GEOS 3.14.1 computeOffsetSegment's association,
under finite round-to-nearest basic operations and correctly rounded sqrt.
The intersection helper instead encloses exact rational line intersections
plus final coordinate rounding. It does NOT model GEOS's DD intersector,
simplification, noding or whole buffers, and is not a runtime service gate.
"""
from dataclasses import dataclass
from fractions import Fraction as F
import math
import sys


PRECISION_BITS = 160
U = F(1,2**53)
ETA = F(1,2**1075)
SAFE_MAGNITUDE = F.from_float(sys.float_info.max)/2


class UncertifiedArithmetic(ValueError):
    """Expected inability to bound a declared input set, not a program retry."""


@dataclass(frozen=True)
class Interval:
    lo: F
    hi: F

    def __post_init__(self):
        object.__setattr__(self,'lo',F(self.lo))
        object.__setattr__(self,'hi',F(self.hi))
        if self.lo>self.hi:
            raise ValueError('Reversed interval endpoints')

    @classmethod
    def point(cls,value):
        value=float(value)
        if not math.isfinite(value):
            raise ValueError('Finite binary64 input required')
        exact=F.from_float(value)
        return cls(exact,exact)

    def contains(self,value):
        value=F(value)
        return self.lo<=value<=self.hi

    def serial(self):
        return [str(self.lo),str(self.hi)]


def outward(lo,hi):
    """Exact floor/ceil onto a dyadic grid; never narrow an interval."""
    denominator=1<<PRECISION_BITS
    lo,hi=F(lo),F(hi)
    return Interval(F((lo.numerator*denominator)//lo.denominator,denominator),
        F(-((-hi.numerator*denominator)//hi.denominator),denominator))


def add(a,b):
    return outward(a.lo+b.lo,a.hi+b.hi)


def neg(a):
    return Interval(-a.hi,-a.lo)


def sub(a,b):
    return add(a,neg(b))


def mul(a,b):
    values=[x*y for x in (a.lo,a.hi) for y in (b.lo,b.hi)]
    return outward(min(values),max(values))


def div(a,b):
    if b.lo<=0<=b.hi:
        raise UncertifiedArithmetic('denominator_interval_contains_zero')
    return mul(a,outward(min(1/b.lo,1/b.hi),max(1/b.lo,1/b.hi)))


def rn(a):
    magnitude=max(abs(a.lo),abs(a.hi))
    if magnitude>SAFE_MAGNITUDE:
        raise UncertifiedArithmetic('binary64_overflow_contract_not_established')
    error=U*magnitude+ETA
    return outward(a.lo-error,a.hi+error)


def fl_add(a,b):
    return rn(add(a,b))


def fl_sub(a,b):
    return rn(sub(a,b))


def fl_mul(a,b):
    return rn(mul(a,b))


def fl_div(a,b):
    return rn(div(a,b))


def fl_square(a):
    lower=F(0) if a.lo<=0<=a.hi else min(a.lo*a.lo,a.hi*a.hi)
    upper=max(a.lo*a.lo,a.hi*a.hi)
    value=rn(outward(lower,upper))
    return Interval(max(F(0),value.lo),value.hi)


def _sqrt_bracket(value):
    if value<0:
        raise UncertifiedArithmetic('negative_square_root_interval')
    denominator=1<<PRECISION_BITS
    scaled=value.numerator*denominator*denominator
    lower=math.isqrt(scaled//value.denominator)
    exact=lower*lower*value.denominator==scaled
    return F(lower,denominator),F(lower if exact else lower+1,denominator)


def fl_sqrt(a):
    if a.lo<0:
        raise UncertifiedArithmetic('negative_square_root_interval')
    lower,_=_sqrt_bracket(a.lo)
    _,upper=_sqrt_bracket(a.hi)
    value=rn(Interval(lower,upper))
    return Interval(max(F(0),value.lo),value.hi)


def point_box(point,error=0.):
    error=F.from_float(float(error))
    if error<0:
        raise ValueError('Nonnegative coordinate uncertainty required')
    if len(point)!=2:
        raise ValueError('Two coordinates required')
    return tuple(Interval(Interval.point(x).lo-error,Interval.point(x).hi+error) for x in point)


def normal_component_limit(norm_squared,length,distance):
    """A proved displacement cap retaining norm/component dependence.

    q >= (1-u)^2*x^2-(2-u)*eta for either computed component x. With
    L=RN(sqrt(q)), L>=alpha*sqrt(q), alpha=1-u-eta/sqrt_lower(q_min).
    Hence |x/L|<=K=sqrt(1+(2-u)*eta/q_min)/((1-u)*alpha).
    Rounding d*x and its division by L gives the returned absolute bound.
    If the auxiliary contract cannot be established, omit this tightening.
    """
    if norm_squared.lo<=0 or length.lo<=0:
        return None
    root_lower,_=_sqrt_bracket(norm_squared.lo)
    if root_lower<=0:
        return None
    alpha=1-U-ETA/root_lower
    if alpha<=0:
        return None
    _,root_upper=_sqrt_bracket(1+(2-U)*ETA/norm_squared.lo)
    ratio_limit=root_upper/((1-U)*alpha)
    bound=(1+U)**2*distance.hi*ratio_limit+(1+U)*ETA/length.lo+ETA
    return outward(bound,bound).hi


def offset_segment(a,b,distance,side,tighten_normal=False):
    """Enclose all outputs of the declared binary64 offset expression.

    a,b are endpoint coordinate boxes; their uncertainty is NOT automatically
    an observation/person error bound. A norm interval containing zero refuses
    the operation rather than choosing a preferred segment or shrinking inputs.
    """
    if len(a)!=2 or len(b)!=2 or side not in (-1,1) or distance.lo<0:
        raise ValueError('Two endpoint boxes, nonnegative distance and side +/-1 required')
    dx,dy=fl_sub(b[0],a[0]),fl_sub(b[1],a[1])
    norm_squared=fl_add(fl_square(dx),fl_square(dy))
    # A sum of squared finite binary64 values cannot be negative. Removing
    # the generic RN enclosure's negative tail uses this exact sign invariant.
    norm_squared=Interval(max(F(0),norm_squared.lo),norm_squared.hi)
    length=fl_sqrt(norm_squared)
    if length.lo<=0:
        raise UncertifiedArithmetic('segment_norm_interval_contains_zero')
    signed=distance if side==1 else neg(distance)
    ux,uy=fl_div(fl_mul(signed,dx),length),fl_div(fl_mul(signed,dy),length)
    if tighten_normal:
        limit=normal_component_limit(norm_squared,length,distance)
        if limit is not None:
            ux,uy=(Interval(max(v.lo,-limit),min(v.hi,limit)) for v in (ux,uy))
    return tuple((fl_sub(p[0],uy),fl_add(p[1],ux)) for p in (a,b))


def rounded_exact_intersection(a,b,c,d):
    """Exact geometric line-intersection interval, then one binary64 rounding.

    This conditional operation deliberately does not claim to reproduce the
    GEOS double-double intersector. Possible parallelism returns UNCERTIFIED.
    """
    cross=lambda x,y:sub(mul(x[0],y[1]),mul(x[1],y[0]))
    u,v,w=[tuple(sub(y,x) for x,y in zip(p,q)) for p,q in ((a,b),(c,d),(a,c))]
    determinant=cross(u,v)
    t,s=div(cross(w,v),determinant),div(cross(w,u),determinant)
    point=tuple(rn(add(a[j],mul(u[j],t))) for j in range(2))
    return point,t,s
