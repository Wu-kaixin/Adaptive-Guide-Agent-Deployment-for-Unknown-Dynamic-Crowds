"""Conditional rational enclosure of binary64 segment interpolation.

This bounds ONLY the coordinate expression (b-a)*t+a, t in [0,1], with
round-to-nearest binary64 basic operations (a fused multiply-add also fits).
It does not enclose an unknown crowd reference, GEOS buffer, or true demand.
It is deliberately not connected to the quarantined runtime service guard.
"""
from dataclasses import dataclass
from fractions import Fraction as F
import math

import numpy as np


UNIT_ROUNDOFF = F(1, 2**53)
HALF_MIN_SUBNORMAL = F(1, 2**1075)


def _fraction(value):
    return F.from_float(float(value))


@dataclass(frozen=True)
class InterpolationEnvelope:
    directions: tuple
    upper_supports: tuple
    coordinate_errors: tuple
    contract: str = 'finite binary64 endpoints |coordinate|<=2**500; nearest rounding; separate or fused multiply/add; t clipped to [0,1]'


def coordinate_error_bounds(vertices):
    """Uniform exact error relative to a true convex combination of endpoints.

    Let d_hat=fl(b-a), e_d=|d_hat-(b-a)|, e_p=u|d_hat|+eta,
    e_a=u(|a|+|d_hat|+e_p)+eta. Then |fl(d_hat*t+a)-(a+(b-a)*t)|
    is at most e_d+e_p+e_a. The same expression covers a fused multiply/add.
    Here eta is half the minimum positive binary64 subnormal. All comparisons
    and bound arithmetic use exact rational values, including subnormal terms.
    """
    vertices = np.asarray(vertices, float)
    if (vertices.ndim != 2 or vertices.shape[1] != 2 or len(vertices) < 2
            or not np.isfinite(vertices).all() or np.any(np.abs(vertices) > 2.**500)):
        raise ValueError('Finite binary64 ring coordinates bounded by 2**500 required')
    errors = [F(0), F(0)]
    for a, b in zip(vertices, np.roll(vertices, -1, axis=0)):
        for j in range(2):
            av, bv = float(a[j]), float(b[j])
            difference = bv - av
            if not math.isfinite(difference):
                raise ValueError('Nonfinite subtraction in interpolation bound')
            ah, bh, dh = _fraction(av), _fraction(bv), _fraction(difference)
            subtraction_error = abs(dh - (bh - ah))
            product_error = UNIT_ROUNDOFF * abs(dh) + HALF_MIN_SUBNORMAL
            addition_error = UNIT_ROUNDOFF * (abs(ah) + abs(dh) + product_error) + HALF_MIN_SUBNORMAL
            errors[j] = max(errors[j], subtraction_error + product_error + addition_error)
    return tuple(errors)


def support_envelope(vertices, directions):
    """Half-plane bounds for ANY clamped segment interpolation on this ring.

    Every connected chord between such outputs is in the same half-planes by
    convexity. The source ring and upstream support must be supplied correctly.
    """
    vertices = np.asarray(vertices, float)
    error = coordinate_error_bounds(vertices)
    directions = tuple(tuple(direction) for direction in directions)
    if any(len(n) != 2 or not any(n) or any(type(v) is not int for v in n) for n in directions):
        raise ValueError('Nonzero integer support directions required')
    if not directions:
        raise ValueError('At least one support direction required')
    points = [tuple(_fraction(v) for v in point) for point in vertices]
    upper = tuple(max(a*x+b*y for x, y in points) + abs(a)*error[0] + abs(b)*error[1]
                  for a, b in directions)
    return InterpolationEnvelope(directions, upper, error)
