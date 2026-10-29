"""Uniform finite-output bounds for the pinned binary64 offset expression.

This bounds finite outputs only. It does not prove every endpoint pair has a
positive computed norm or a defined offset. Existing all-input refusals and
zero-length witnesses must remain in force. No runtime service gate uses it.
"""
from dataclasses import dataclass
from fractions import Fraction as F

from .offset_interval import Interval,U,ETA,_sqrt_bracket
from .precision_enclosure import _rn,_representable_inside


MIN_POSITIVE_SQUARED_NORM=F(1,2**1074)
MIN_POSITIVE_LENGTH=F(1,2**537)


def finite_normal_limit(distance_upper):
    """Bound |RN(RN(d*x)/L)| for either component of any finite q>0 norm.

    q=RN(RN(dx*dx)+RN(dy*dy)) dominates each rounded nonnegative square;
    the same domination holds with one fused square/add. Thus
    x*x <= (q+eta)/(1-u), and eta/q<=1/2. sqrt(q) is normal and
    L=RN(sqrt(q)) >= (1-u)*sqrt(q), L>=2**-537. Propagating the
    product and quotient gives the bound below without an input edge minimum.
    Correctly rounded sqrt and finite nearest-rounding operations are premises.
    """
    distance_upper=F(distance_upper)
    if distance_upper<0:raise ValueError('Nonnegative distance upper bound required')
    if distance_upper==0:return F(0)
    _,root=_sqrt_bracket(F(3,2)/(1-U))
    ratio=root/(1-U)
    return (1+U)**2*distance_upper*ratio+(1+U)*ETA/MIN_POSITIVE_LENGTH+ETA


@dataclass(frozen=True)
class FiniteOffsetEnclosure:
    endpoints: tuple
    normal_displacement_limit: F

    def serial(self):
        return {'status':'CONDITIONAL_FINITE_OUTPUT_ENCLOSURE',
            'bounds':[[q.serial() for q in p] for p in self.endpoints],
            'normal_displacement_limit':str(self.normal_displacement_limit),
            'all_inputs_defined':False,'original_reference_enclosed':False,'service_certificate':False,
            'condition':'positive computed squared norm; finite basic operations; correctly rounded sqrt; declared source expression'}


def finite_offset_box(a,b,distance,side):
    """Enclose returned endpoints when the source expression is defined/finite.

    Undefined members of the endpoint-box family are not silently removed or
    declared safe. This is a conditional postcondition for geometry transfer,
    separate from any whole-family operation-domain certificate.
    """
    if len(a)!=2 or len(b)!=2 or side not in (-1,1) or not isinstance(distance,Interval) or distance.lo<0:
        raise ValueError('2D endpoint boxes, nonnegative distance interval and side +/-1 required')
    cap=finite_normal_limit(distance.hi);out=[]
    for endpoint in (a,b):
        bounds=[]
        for axis in endpoint:
            lo,hi=_representable_inside(axis)
            bounds.append(Interval(_rn(lo-cap),_rn(hi+cap)))
        out.append(tuple(bounds))
    return FiniteOffsetEnclosure(tuple(out),cap)
