"""Rational outer axis box of the intersection of two closed error disks.

Partial-overlap extrema occur at an axis-stationary circle point or a circle
intersection. Only a rational upper bracket of the latter's square root is
needed. Empty intersection, containment, coincident centers and zero radii
are decided exactly. A pair enclosure is not a complete static-world model.
"""
from fractions import Fraction as F

from .offset_interval import Interval,_sqrt_bracket


def disk_pair_box(first,radius,second,other_radius):
    first=tuple(map(F,first));second=tuple(map(F,second));radius=F(radius);other_radius=F(other_radius)
    if len(first)!=2 or len(second)!=2 or min(radius,other_radius)<0:
        raise ValueError('Finite two-dimensional centers and nonnegative radii required')
    delta=tuple(b-a for a,b in zip(first,second));distance2=sum(v*v for v in delta)
    evidence={'distance_squared':str(distance2),'radii_sum_squared':str((radius+other_radius)**2)}
    if distance2>(radius+other_radius)**2:
        return None,evidence|{'kind':'disjoint_disks'}
    if distance2<=(radius-other_radius)**2:
        center,r=(first,radius) if radius<=other_radius else (second,other_radius)
        return tuple(Interval(v-r,v+r) for v in center),evidence|{'kind':'contained_or_coincident_disks'}
    # The remaining case has two boundary intersections, possibly coincident
    # at an external tangency. Parameters avoid dividing by sqrt(distance2).
    along=(radius*radius-other_radius*other_radius+distance2)/(2*distance2)
    radicand=radius*radius/distance2-along*along
    if radicand<0:raise ArithmeticError('Exact disk intersection classification inconsistent')
    _,root=_sqrt_bracket(radicand)
    base=tuple(a+along*d for a,d in zip(first,delta));perpendicular=(-delta[1],delta[0]);bounds=[]
    for axis in (0,1):
        candidates=[]
        for center,r,other,s in [(first,radius,second,other_radius),(second,other_radius,first,radius)]:
            for sign in (-1,1):
                point=list(center);point[axis]+=sign*r
                if sum((v-w)**2 for v,w in zip(point,other))<=s*s:candidates.append(point[axis])
        bounds.append(Interval(min([base[axis]-abs(perpendicular[axis])*root]+candidates),
            max([base[axis]+abs(perpendicular[axis])*root]+candidates)))
    return tuple(bounds),evidence|{'kind':'intersecting_circles','along':str(along),
        'radicand':str(radicand),'root_upper':str(root)}
