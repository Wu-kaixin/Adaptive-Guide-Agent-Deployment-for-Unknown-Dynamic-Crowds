"""Exact budget test on DECLARED cell-mass intervals and a common total.

This is a small robust linear program, not a new optimization principle or an
observation-to-reference enclosure. Callers must prove that every original
world induces a mass vector in this set and that cell signs bound signed loss.
No runtime controller imports this module.
"""
from fractions import Fraction as F


def _rational(value):
    if type(value) not in (int, F):
        raise ValueError('Use exact int/Fraction masses and budget')
    return F(value)


def declared_mass_budget(signs, lower, upper, total_lower, total_upper, budget):
    """Maximize sum((sign_i-budget)*mass_i) over a box with total bounds.

    Signs are -1 (gain), 0 (neutral), +1 (loss), or pointwise upper
    classifications of signed coverage on a common disjoint partition.
    A nonpositive maximum proves the normalized budget ONLY for this declared
    mass set. Empty sets and no positive uniform total lower bound refuse.
    """
    signs, low, high = tuple(signs), tuple(map(_rational, lower)), tuple(map(_rational, upper))
    tl, th, delta = map(_rational, (total_lower, total_upper, budget))
    if (not signs or len(signs) != len(low) or len(low) != len(high)
            or any(type(s) is not int or s not in (-1, 0, 1) for s in signs)
            or any(l < 0 or h < l for l, h in zip(low, high))
            or tl < 0 or th < tl or not 0 <= delta <= 1):
        raise ValueError('Invalid signed cell-mass ambiguity set')
    feasible_low, feasible_high = max(sum(low), tl), min(sum(high), th)
    common = {'original_reference_enclosed': False, 'runtime_service_certificate': False,
              'budget_holds_for_declared_mass_set': False,
              'feasible_total_interval': (feasible_low, feasible_high)}
    if feasible_low > feasible_high:
        return dict(common, status='EMPTY_DECLARED_SET')
    if feasible_low <= 0:
        return dict(common, status='NO_POSITIVE_TOTAL_LOWER')

    coefficients = tuple(F(s) - delta for s in signs)
    order = sorted(range(len(signs)), key=lambda i: (-coefficients[i], i))
    masses = list(low)
    # For the mandatory minimum total, fill highest coefficients first.
    need = feasible_low - sum(masses)
    for i in order:
        amount = min(high[i] - masses[i], need)
        masses[i] += amount
        need -= amount
    assert need == 0
    # Above the minimum, only strictly positive coefficients improve the LP.
    room = feasible_high - sum(masses)
    for i in order:
        if coefficients[i] <= 0:
            break
        amount = min(high[i] - masses[i], room)
        masses[i] += amount
        room -= amount
    maximum = sum(c * m for c, m in zip(coefficients, masses))
    return dict(common, status='OPTIMAL_DECLARED_SET', max_budget_residual=maximum,
                maximizing_masses=tuple(masses),
                budget_holds_for_declared_mass_set=maximum <= 0)
