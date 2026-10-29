"""Controlled exact-mass study; independent vertex enumeration checks the LP.

No GEOS, service integrator, crowd generator or controller is executed.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
from fractions import Fraction as F
from hashlib import sha256
from itertools import product
import json
from pathlib import Path
import shutil
import traceback

import psutil
import yaml

from udc.multigroup.mass_ambiguity import declared_mass_budget


def vertices(low, high, tl, th):
    """Enumerate axis-box corners and each total-plane/box-edge intersection.

    This uses active-constraint geometry, not the production sorted-fill rule.
    Every vertex has either n active box faces or a total face and n-1 box
    faces (coincident faces/zero-width coordinates may repeat candidates).
    """
    result = {v for v in product(*zip(low, high)) if tl <= sum(v) <= th}
    for free in range(len(low)):
        others = [i for i in range(len(low)) if i != free]
        for ends in product(*[(low[i], high[i]) for i in others]):
            for total in (tl, th):
                value = total - sum(ends)
                if low[free] <= value <= high[free]:
                    v = [F(0)] * len(low)
                    v[free] = value
                    for i, endpoint in zip(others, ends):
                        v[i] = endpoint
                    result.add(tuple(v))
    return sorted(result)


def _dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')


def run(protocol, output):
    config = yaml.safe_load(protocol.read_text(encoding='utf-8'))
    output.mkdir(parents=True, exist_ok=False)
    p = psutil.Process()
    _dump(output/'process.json', dict(pid=p.pid, create_time=p.create_time(), exe=p.exe(), cmdline=p.cmdline()))
    sources = [protocol, Path(__file__), Path(__file__).parents[1]/'multigroup/mass_ambiguity.py']
    hashes = {}
    for source in sources:
        target = output/'source_snapshot'/source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        hashes[str(source)] = sha256(source.read_bytes()).hexdigest()
    _dump(output/'manifest.json', dict(protocol=config, sources=hashes, started_at=datetime.now(timezone.utc).isoformat()))
    counts = Counter()
    try:
        with (output/'cases.jsonl').open('w', encoding='utf-8') as stream:
            for n in config['dimensions']:
                for family in range(config['interval_families']):
                    low = tuple(F(i+1, 10) if family in (1, 3) else F(0) for i in range(n))
                    high = tuple(l + (F(0) if family == 2 and i == 0 else F(i%3+1, 7))
                                 for i, l in enumerate(low))
                    a, b = sum(low), sum(high)
                    total_ranges = [(a,b), (a+(b-a)/3,a+2*(b-a)/3),
                                    ((a+b)/2,(a+b)/2), (b+1,b+2), (F(0),F(0))]
                    assert len(total_ranges) == config['total_ranges_per_family']
                    for range_id, (tl, th) in enumerate(total_ranges):
                        oracle = vertices(low, high, tl, th)
                        for signs in product((-1,0,1), repeat=n):
                            for raw_budget in config['budgets']:
                                delta = F(raw_budget)
                                r = declared_mass_budget(signs, low, high, tl, th, delta)
                                counts['conditions'] += 1
                                counts[r['status']] += 1
                                if not oracle:
                                    assert r['status'] == 'EMPTY_DECLARED_SET'
                                elif min(map(sum, oracle)) <= 0:
                                    assert r['status'] == 'NO_POSITIVE_TOTAL_LOWER'
                                else:
                                    assert r['status'] == 'OPTIMAL_DECLARED_SET'
                                    expected = max(sum((F(s)-delta)*m for s,m in zip(signs,v)) for v in oracle)
                                    normalized = max(sum(F(s)*m for s,m in zip(signs,v))/sum(v) for v in oracle)
                                    assert r['max_budget_residual'] == expected
                                    assert r['budget_holds_for_declared_mass_set'] == (normalized <= delta)
                                    vector = r['maximizing_masses']
                                    assert all(l <= m <= h for l,m,h in zip(low,vector,high))
                                    assert tl <= sum(vector) <= th
                                    assert sum((F(s)-delta)*m for s,m in zip(signs,vector)) == expected
                                    counts['exact_vertex_objective_checks'] += len(oracle)
                                    counts['budget_holds'] += r['budget_holds_for_declared_mass_set']
                                assert not r['runtime_service_certificate'] and not r['original_reference_enclosed']
                                stream.write(json.dumps(dict(n=n,family=family,total_range_id=range_id,
                                    signs=signs,lower=low,upper=high,total_bounds=(tl,th),budget=delta,
                                    enumerated_vertices=len(oracle),result=r),default=str)+'\n')
        # Explicit cancellation/common-total example, not a crowd sample.
        fixture = declared_mass_budget([1,-1,0],[0,0,0],[1,100,98],100,100,F(1,500))
        assert fixture['max_budget_residual'] == F(-1,5)
        summary = dict(counts=counts, all_match=True, stopped=False,
            coupling_fixture=dict(separate_numerator_denominator_bound='1/100',
                exact_max_normalized_loss='0', joint_budget_result=fixture),
            new_independent_crowd_inputs=0,new_motion_executions=0,
            original_reference_enclosed=False,runtime_service_gate_changed=False,
            novelty_claim=False,finished_at=datetime.now(timezone.utc).isoformat())
        _dump(output/'summary.json',summary)
        _dump(output/'receipt.json',dict(files={name:sha256((output/name).read_bytes()).hexdigest()
            for name in ('manifest.json','cases.jsonl','summary.json')}, completed=True))
    except BaseException:
        _dump(output/'error.json',dict(traceback=traceback.format_exc(),counts=counts,stopped=True))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--protocol',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    run(args.protocol,args.output)
