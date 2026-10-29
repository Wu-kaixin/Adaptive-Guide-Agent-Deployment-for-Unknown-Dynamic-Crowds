"""Conditional coordinate error when internal normalized scales have a floor.

No GEOS selector or observation-to-scale premise is inferred here. Every
operation in the actual branch must be finite. Runtime guards do not import it.
"""
from fractions import Fraction as F

from .offset_interval import Interval,U,ETA


def lower_scale_precision_box(box,scale_lower,*,stages=1):
    """Bound up to stages maps RN(J(RN(x*S))/S), S >= scale_lower >= 1.

    FLOATING identity is also allowed. Point precision with internal S>=1
    uses the same scaled-fine expression; unknown S may vary across stages.
    J is nearest integer with ties +infinity and satisfies |J(w)-w|<=1/2
    for binary64 w (large binary64 values are already integral).
    """
    if (len(box)!=2 or any(not isinstance(q,Interval) for q in box)
            or type(scale_lower) not in (int,F) or scale_lower<1
            or type(stages) is not int or stages<0):
        raise ValueError('2D box, exact internal scale floor >=1 and nonnegative stage count required')
    floor=F(scale_lower);gain=(1+U)**2
    additive=(1+U)*(ETA+F(1,2))/floor+ETA
    # |Q(x)-x| <= (gain-1)*|x|+additive. Summing this
    # recurrence bounds displacement without a quantizer idempotence premise.
    power=gain**stages;series=(power-1)/(gain-1)
    errors=tuple((power-1)*max(abs(q.lo),abs(q.hi))+additive*series for q in box)
    return tuple(Interval(q.lo-e,q.hi+e) for q,e in zip(box,errors)),errors
