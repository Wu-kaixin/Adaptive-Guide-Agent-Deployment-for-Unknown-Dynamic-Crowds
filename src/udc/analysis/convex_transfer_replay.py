"""Separate arithmetic replay of every saved support-stage decision.

Uses direct Fraction dot products rather than common-denominator integer
supports, and an expanded error formula rather than the builder's staged sum.
This checks artifacts and arithmetic, not universal GEOS enclosure semantics.
"""
from concurrent.futures import ProcessPoolExecutor
from fractions import Fraction as F
from pathlib import Path
from math import gcd

import numpy as np

from udc.experiments.static_multigroup import digest, write_json, environment_record
from udc.experiments.worker_resources import initialize_worker
from .convex_transfer_audit import read, checked


def _supports(points, directions):
    rational = [tuple(F.from_float(float(v)) for v in point) for point in points]
    return [max(a*x+b*y for x,y in rational) for a,b in directions]


def _expanded_interpolation_error(points):
    u, eta = F(1,2**53), F(1,2**1075)
    result = [F(0), F(0)]
    for a,b in zip(points, np.roll(points,-1,axis=0)):
        for j in range(2):
            af,bf=F.from_float(float(a[j])),F.from_float(float(b[j]))
            dh=F.from_float(float(b[j])-float(a[j]))
            bound=abs(dh-(bf-af))+u*abs(af)+(2*u+u*u)*abs(dh)+(2+u)*eta
            result[j]=max(result[j],bound)
    return result


def _verify_hull_certificate(points,certificate,directions):
    """Verify convex hull facets without using the monotone-chain constructor.

    Hull vertices must be distinct source vertices, each CCW turn strict, and
    every source point must lie on the left of every edge. These finite exact
    predicates certify the proposed hull; rational edge normals are derived
    separately and compared with the recorded primitive integer directions.
    """
    rational=[tuple(F.from_float(float(v)) for v in point) for point in points]
    indices=certificate['source_hull_vertex_indices']
    if len(indices)<3 or len(indices)!=len(set(indices)) or any(i<0 or i>=len(points) for i in indices):
        raise ValueError('Invalid convex hull vertex inventory')
    hull=[rational[i] for i in indices]
    cross=lambda a,b,c:(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    expected=[]
    for k,(a,b) in enumerate(zip(hull,hull[1:]+hull[:1])):
        if cross(a,b,hull[(k+2)%len(hull)])<=0 or any(cross(a,b,p)<0 for p in rational):
            raise ValueError('Direct rational verification rejects source hull facet')
        dx,dy=b[0]-a[0],b[1]-a[1]
        normal=(dy.numerator*dx.denominator,-dx.numerator*dy.denominator)
        divisor=gcd(abs(normal[0]),abs(normal[1]))
        expected.append([normal[0]//divisor,normal[1]//divisor])
    if certificate['axes_added']:
        expected.extend(n for n in ([1,0],[0,1],[-1,0],[0,-1]) if n not in expected)
    if expected!=directions:
        raise ValueError('All-facet directions differ from independently verified hull')


def _replay_input(argument):
    study, task, output = argument
    study, output = Path(study), Path(output)
    manifest=read(study/'manifest.json')
    folder=study/'inputs'/task['task_id']
    for name,checksum in read(folder/'receipt.json').items():
        checked(folder/name,checksum)
    checked(Path(manifest['original'])/task['input'],task['input_sha256'])
    saved=read(folder/'result.json')
    if saved['source'] != task:
        raise ValueError('Source task changed')
    records,violations,checks=0,0,0
    for name in sorted({r['stage_geometry'] for r in saved['records']}):
        with np.load(folder/name,allow_pickle=False) as data:
            stages={n:data[n].copy() for n in data.files}
        tests=[r for r in saved['records'] if r['stage_geometry']==name]
        expected=['closed_in_input_hull','filled_occupied_in_input_hull','offset_in_occupied_dilation',
            'sampled_curve_in_binary64_interpolation_support' if manifest['design'].get('interpolation_enclosure') else 'sampled_curve_in_offset_hull']
        if [r['test'] for r in tests] != expected[:len(tests)]:
            raise ValueError('Stage decision order changed')
        for test in tests:
            checked(folder/name,test['stage_geometry_sha256'])
            directions=test.get('directions',manifest['design']['directions'])
            if manifest['design'].get('direction_mode')=='all_source_hull_facets':
                _verify_hull_certificate(stages[test['source']],test['hull_certificate'],directions)
            h0,h1=_supports(stages[test['source']],directions),_supports(stages[test['target']],directions)
            radius=F.from_float(test['radius_m'])
            envelope=test.get('interpolation_enclosure')
            if envelope:
                error=_expanded_interpolation_error(stages['offset'])
                if list(map(str,error)) != envelope['coordinate_error_fractions']:
                    raise ValueError('Independent expanded interpolation bound differs')
                upper=[h+abs(a)*error[0]+abs(b)*error[1] for h,(a,b) in zip(h0,directions)]
                if list(map(str,upper)) != envelope['upper_support_fractions']:
                    raise ValueError('Recorded half-plane enclosure differs')
                failed=[direction for direction,target,limit in zip(directions,h1,upper) if target>limit]
            else:
                failed=[direction for direction,source,target in zip(directions,h0,h1)
                    if target>source and (target-source)**2 > radius**2*sum(n*n for n in direction)]
            if failed != [v['direction'] for v in test['violations']]:
                raise ValueError('Exact finite-direction decision differs')
            for witness in test['violations']:
                i=directions.index(witness['direction'])
                if F(witness['excess_fraction']) != h1[i]-(upper[i] if envelope else h0[i]):
                    raise ValueError('Saved rational witness excess differs')
            records+=1
            checks+=len(directions)
            violations+=bool(failed)
    if records != len(saved['records']) or bool(violations) != saved['proposed_enclosure_refuted']:
        raise ValueError('Input denominator or status differs')
    result={'task_id':task['task_id'],'groups_checked':saved['groups_checked'],
        'stage_groups_checked':records,'exact_directional_checks':checks,
        'counterexample_stage_groups':violations,'all_match':True}
    write_json(output/(task['task_id']+'.json'),result)
    return result


def audit(study,output):
    study,output=Path(study).resolve(),Path(output).resolve()
    manifest=read(study/'manifest.json')
    checked(study/'manifest.json',read(study/'seal.json')['manifest_sha256'])
    for name,checksum in manifest['sources'].items():
        checked(study/'source_snapshot'/name,checksum)
    checked(Path(manifest['original'])/'manifest.json',manifest['original_manifest_sha256'])
    if environment_record()!=manifest['environment']:
        raise ValueError('Different numerical environment for arithmetic replay')
    saved,summary=read(study/'results.json'),read(study/'summary.json')
    task_map={t['task_id']:t for t in manifest['tasks']}
    identifiers=[r['task_id'] for r in saved]
    if len(identifiers)!=len(set(identifiers)) or len(saved)!=summary['completed_inputs']:
        raise ValueError('Completed input ledger is inconsistent')
    output.mkdir(parents=True,exist_ok=False)
    with ProcessPoolExecutor(max_workers=manifest['design']['workers'],initializer=initialize_worker) as pool:
        results=list(pool.map(_replay_input,[(str(study),task_map[name],str(output)) for name in identifiers]))
    stages=sum(r['stage_groups_checked'] for r in results)
    groups=sum(r['groups_checked'] for r in results)
    counterexamples=sum(r['counterexample_stage_groups'] for r in results)
    result={'study_manifest_sha256':digest(study/'manifest.json'),'inputs_replayed':len(results),
        'groups_replayed':groups,'stage_groups_replayed':stages,
        'exact_directional_checks':sum(r['exact_directional_checks'] for r in results),
        'counterexample_stage_groups':counterexamples,
        'all_match':groups==summary['checked_groups'] and stages==summary['tested_stage_groups']
            and counterexamples==summary['counterexample_stage_groups'],
        'scope':'all saved stage coordinate hashes and independent exact arithmetic replay; not kernel regeneration or universal geometric proof',
        'new_independent_samples':0,'new_motion_executions':0,'runtime_certificate_restored':False}
    write_json(output/'audit.json',result)
    if not result['all_match']:
        raise ValueError('Support-transfer summary differs from independent arithmetic replay')
    return result
