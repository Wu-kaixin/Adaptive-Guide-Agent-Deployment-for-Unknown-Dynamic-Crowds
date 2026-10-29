"""Read-only mass LP audit via scalar Lagrange dual bounds, not sorted fill.

Does not import the candidate solver or its vertex enumeration checker.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
from fractions import Fraction as F
from hashlib import sha256
import json
from pathlib import Path
import shutil
import traceback

import psutil


def _hash(path):
    return sha256(path.read_bytes()).hexdigest()


def _read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _write(path, data):
    path.write_text(json.dumps(data,indent=2,default=str)+'\n',encoding='utf-8')


def audit(studies, output):
    output.mkdir(parents=True,exist_ok=False)
    p = psutil.Process()
    _write(output/'process.json',dict(pid=p.pid,create_time=p.create_time(),exe=p.exe(),cmdline=p.cmdline()))
    snapshot = output/'source_snapshot'
    snapshot.mkdir()
    shutil.copyfile(__file__,snapshot/'mass_budget_dual_audit.py')
    sources = {}
    for folder in studies:
        receipt = _read(folder/'receipt.json')
        assert receipt['completed'] and not (folder/'error.json').exists()
        for leaf, locked in receipt['files'].items():
            assert _hash(folder/leaf) == locked
        manifest = _read(folder/'manifest.json')
        for source, locked in manifest['sources'].items():
            assert _hash(folder/'source_snapshot'/Path(source).name) == locked
        sources[str(folder)] = dict(receipt_sha256=_hash(folder/'receipt.json'),files=receipt['files'])
    _write(output/'manifest.json',dict(inputs=sources,auditor_sha256=_hash(Path(__file__)),
        started_at=datetime.now(timezone.utc).isoformat(),solver_imported=False,vertex_enumerator_imported=False))
    counters = {}
    try:
        for folder in studies:
            counts = Counter()
            with (folder/'cases.jsonl').open(encoding='utf-8') as rows:
                for line in rows:
                    row = json.loads(line)
                    low, high = [tuple(map(F,row[k])) for k in ('lower','upper')]
                    tl, th = map(F,row['total_bounds'])
                    signs, delta = tuple(row['signs']), F(row['budget'])
                    assert len(signs) == len(low) == len(high) == row['n']
                    assert all(s in (-1,0,1) for s in signs)
                    assert all(0 <= l <= h for l,h in zip(low,high)) and 0 <= tl <= th and 0 <= delta <= 1
                    feasible_low, feasible_high = max(sum(low),tl), min(sum(high),th)
                    result = row['result']
                    assert tuple(map(F,result['feasible_total_interval'])) == (feasible_low,feasible_high)
                    assert result['original_reference_enclosed'] is False and result['runtime_service_certificate'] is False
                    counts['conditions'] += 1
                    counts[result['status']] += 1
                    if feasible_low > feasible_high:
                        assert result['status'] == 'EMPTY_DECLARED_SET'
                        assert result['budget_holds_for_declared_mass_set'] is False
                        continue
                    if feasible_low <= 0:
                        assert result['status'] == 'NO_POSITIVE_TOTAL_LOWER'
                        assert result['budget_holds_for_declared_mass_set'] is False
                        continue
                    assert result['status'] == 'OPTIMAL_DECLARED_SET'
                    c = tuple(F(s)-delta for s in signs)
                    point = tuple(map(F,result['maximizing_masses']))
                    assert len(point) == len(signs)
                    assert all(l <= m <= h for l,m,h in zip(low,point,high))
                    assert tl <= sum(point) <= th
                    primal = sum(v*m for v,m in zip(c,point))
                    # For ANY lambda, c*m <= lambda*T_bound + box support
                    # of (c-lambda). Equality to a feasible primal suffices;
                    # no assumption about candidate optimality is required.
                    duals = []
                    for lam in set(c) | {F(0)}:
                        bound = lam * (th if lam >= 0 else tl)
                        bound += sum((v-lam)*(h if v-lam >= 0 else l) for v,l,h in zip(c,low,high))
                        duals.append(bound)
                    assert primal == min(duals) == F(result['max_budget_residual'])
                    assert result['budget_holds_for_declared_mass_set'] == (primal <= 0)
                    counts['dual_upper_bound_evaluations'] += len(duals)
                    counts['primal_dual_equalities'] += 1
            expected = _read(folder/'summary.json')['counts']
            for key in ('conditions','EMPTY_DECLARED_SET','NO_POSITIVE_TOTAL_LOWER','OPTIMAL_DECLARED_SET'):
                assert counts[key] == expected[key]
            counters[str(folder)] = dict(counts)
        _write(output/'audit.json',dict(all_match=True,counts=counters,solver_imported=False,
            vertex_enumerator_imported=False,new_geometry_or_crowd_or_motion_executions=0,
            finished_at=datetime.now(timezone.utc).isoformat()))
        _write(output/'receipt.json',dict(completed=True,files={leaf:_hash(output/leaf) for leaf in ('manifest.json','audit.json')}))
    except BaseException:
        _write(output/'error.json',dict(stopped=True,counts=counters,traceback=traceback.format_exc()))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--studies',type=Path,nargs='+',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    audit(args.studies,args.output)
