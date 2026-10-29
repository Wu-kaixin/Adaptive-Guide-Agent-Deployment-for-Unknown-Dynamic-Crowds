"""Exact hull and facet normals of finite binary-coordinate point sets.

No GEOS hull or floating orientation predicate is used. This is an evaluator
utility, not an enclosure of the unknown crowd or a runtime service guard.
"""
from math import gcd

import numpy as np


def integer_points(points):
    points = np.asarray(points, float)
    if points.ndim != 2 or points.shape[1] != 2 or not len(points) or not np.isfinite(points).all():
        raise ValueError('Finite nonempty 2D points required')
    ratios = [[float(v).as_integer_ratio() for v in row] for row in points]
    denominator = max(d for row in ratios for _,d in row)
    return [(row[0][0]*(denominator//row[0][1]),row[1][0]*(denominator//row[1][1])) for row in ratios],denominator


def hull_indices(points):
    integer,_ = integer_points(points)
    first = {}
    for i,p in enumerate(integer):
        first.setdefault(p,i)
    ordered = sorted(first)
    cross = lambda a,b,c:(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    lower,upper = [],[]
    for p in ordered:
        while len(lower)>1 and cross(lower[-2],lower[-1],p)<=0:
            lower.pop()
        lower.append(p)
    for p in reversed(ordered):
        while len(upper)>1 and cross(upper[-2],upper[-1],p)<=0:
            upper.pop()
        upper.append(p)
    hull = lower[:-1]+upper[:-1] if len(ordered)>1 else ordered
    return tuple(first[p] for p in hull)


def facet_directions(points,include_axes=False):
    integer,_ = integer_points(points)
    indices = hull_indices(points)
    if len(indices)<3:
        raise ValueError('A nondegenerate 2D hull is required for facet audit')
    directions=[]
    for i,j in zip(indices,indices[1:]+indices[:1]):
        a,b = integer[i],integer[j]
        normal = (b[1]-a[1],a[0]-b[0])
        divisor = gcd(abs(normal[0]),abs(normal[1]))
        directions.append((normal[0]//divisor,normal[1]//divisor))
    if include_axes:
        directions.extend(n for n in ((1,0),(0,1),(-1,0),(0,-1)) if n not in directions)
    return tuple(directions),indices
