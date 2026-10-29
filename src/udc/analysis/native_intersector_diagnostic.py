"""Actual GEOS C API evidence for a structural returned-point outer set."""
from fractions import Fraction as F
from pathlib import Path

import numpy as np

from .convex_transfer_audit import read,checked
from .closing_witness_diagnosis import rational_intersection
from .local_interval_diagnostic import numeric_offset
from .native_geos_segment import NativeSegmentIntersector
from udc.multigroup.offset_interval import Interval,UncertifiedArithmetic,point_box,offset_segment
from udc.multigroup.intersector_enclosure import enclosure
from udc.experiments.static_multigroup import write_json,digest


def exact_relationship(points):
    a,b,c,d=[tuple(F.from_float(float(x)) for x in p) for p in points]
    cross=lambda x,y:x[0]*y[1]-x[1]*y[0]
    minus=lambda x,y:tuple(v-w for v,w in zip(x,y))
    def on(p,x,y):
        return cross(minus(p,x),minus(y,x))==0 and all(min(u,v)<=q<=max(u,v) for q,u,v in zip(p,x,y))
    if a==b:
        return ('POINT',a) if on(a,c,d) else ('NONE',None)
    if c==d:
        return ('POINT',c) if on(c,a,b) else ('NONE',None)
    result=rational_intersection(a,b,c,d)
    if result is not None:
        p,t,s=result
        return ('POINT',p) if 0<=t<=1 and 0<=s<=1 else ('NONE',None)
    if cross(minus(c,a),minus(b,a))!=0:return 'NONE',None
    axis=0 if a[0]!=b[0] else 1
    lo=max(min(a[axis],b[axis]),min(c[axis],d[axis]))
    hi=min(max(a[axis],b[axis]),max(c[axis],d[axis]))
    if lo>hi:return 'NONE',None
    matches=[p for p in (a,b,c,d) if on(p,a,b) and on(p,c,d)]
    return ('POINT',matches[0]) if lo==hi else ('OVERLAP',None)


def _family_and_inputs(manifest,case):
    if case['kind']=='fixture':
        points=case['points']
        return enclosure(tuple(point_box(p) for p in points),precision_model='none'),None,[points]
    parent=Path(manifest['parent'])
    folder=parent/'cases'/case['parent_case_id']
    for name,checksum in read(folder/'receipt.json').items():checked(folder/name,checksum)
    checked(folder/'traces.json',case['traces_sha256'])
    source_case=next(c for c in read(parent/'manifest.json')['cases'] if c['case_id']==case['parent_case_id'])
    with np.load(folder/'input.npz',allow_pickle=False) as data:points=data['points'].copy()
    spec=read(parent/'manifest.json')['design']
    try:
        boxes=[point_box(p,source_case['coordinate_uncertainty_m']) for p in points]
        first=offset_segment(boxes[0],boxes[1],Interval.point(spec['offset_distance_m']),spec['side'],tighten_normal=True)
        second=offset_segment(boxes[1],boxes[2],Interval.point(spec['offset_distance_m']),spec['side'],tighten_normal=True)
        family=enclosure((*first,*second),precision_model='none');reason=None
    except UncertifiedArithmetic as exc:
        family=None;reason=str(exc)
    samples=[]
    for row in read(folder/'traces.json'):
        p=row['input_points']
        first,_=numeric_offset(p[0],p[1],spec['offset_distance_m'],spec['side'])
        second,_=numeric_offset(p[1],p[2],spec['offset_distance_m'],spec['side'])
        if first is None or second is None:raise ValueError('Preserved parent trace has undefined offset')
        samples.append((*first,*second))
    return family,reason,samples


def worker(argument):
    study,case=argument;study=Path(study);manifest=read(study/'manifest.json')
    folder=study/'cases'/case['case_id'];folder.mkdir(parents=True,exist_ok=False)
    family,reason,inputs=_family_and_inputs(manifest,case)
    rows=[];points=empty=violations=topology=round_differences=0
    with NativeSegmentIntersector() as native:
        if native.identity!=manifest['native_binary_identity']:raise ValueError('Native library identity changed')
        for endpoints in inputs:
            value=native.intersect(endpoints)
            exact_kind,exact_point=exact_relationship(endpoints)
            if case['kind']=='fixture' and (exact_kind!='NONE')!=case['intersects']:
                raise ValueError('Declared fixture topology disagrees with exact coordinates')
            mismatch=(value is not None)!=(exact_kind!='NONE')
            topology+=mismatch
            single=enclosure(tuple(point_box(p) for p in endpoints),precision_model='none')
            single_ok=value is None or single.contains(value)
            family_ok=None if family is None else value is None or family.contains(value)
            bad=not single_ok or family_ok is False
            violations+=bad
            rounded_same=None;square=None
            if value is not None:
                points+=1
                if exact_point is not None:
                    rounded_same=np.array_equal(value,[float(q) for q in exact_point])
                    round_differences+=not rounded_same
                    square=str(sum((F.from_float(v)-q)**2 for v,q in zip(value,exact_point)))
            else:empty+=1
            rows.append({'input_endpoints':[list(p) for p in endpoints],'native_point':value,
                'exact_relationship':exact_kind,'topology_disagreement':mismatch,
                'singleton_postcondition_holds':single_ok,'family_postcondition_holds':family_ok,
                'native_equals_correctly_rounded_exact_point':bool(rounded_same) if rounded_same is not None else None,
                'native_squared_error_from_exact_point_fraction':square,
                'equals_input_endpoint':bool(value is not None and any(np.array_equal(value,p) for p in endpoints))})
            if bad or mismatch:break
    result=case|{'family_status':'BOUNDED_RETURNED_POINT_MAY_BE_EMPTY' if family else 'UNCERTIFIED_UPSTREAM',
        'upstream_reason':reason,'family_envelope':family.serial() if family else None,
        'scheduled_native_calls':len(inputs),'completed_native_calls':len(rows),'native_point_outputs':points,
        'native_empty_outputs':empty,'postcondition_violations':violations,'topology_disagreements':topology,
        'not_correctly_rounded_exact_point':round_differences,'runtime_certificate_restored':False,
        'scope':'actual GEOS returned-point outer set; not intersection existence, accuracy, whole buffer or service proof'}
    write_json(folder/'native_calls.json',rows);write_json(folder/'result.json',result)
    write_json(folder/'receipt.json',{n:digest(folder/n) for n in ('native_calls.json','result.json')})
    if violations or topology:raise ValueError('MODEL_OR_NATIVE_ERROR: inspect preserved case, do not retry')
    return result


def _serialized_contains(envelope,point):
    if point is None:return True
    boxes=list(envelope['endpoint_boxes'])
    if envelope['normal_box'] is not None:boxes.append(envelope['normal_box'])
    p=list(map(lambda v:F.from_float(float(v)),point))
    return any(all(F(q[0])<=v<=F(q[1]) for q,v in zip(box,p)) for box in boxes)


def audit(study,output):
    study,output=Path(study).resolve(),Path(output).resolve();m=read(study/'manifest.json')
    checked(study/'manifest.json',read(study/'seal.json')['manifest_sha256'])
    for name,checksum in m['sources'].items():checked(study/'source_snapshot'/name,checksum)
    for name,checksum in m['upstream_sources'].items():checked(study/'upstream_sources'/name,checksum['sha256'])
    checked(Path(m['parent'])/'manifest.json',m['parent_manifest_sha256'])
    summary=read(study/'summary.json');calls=points=empty=bound_cases=refused=0
    with NativeSegmentIntersector() as native:
        if native.identity!=m['native_binary_identity']:raise ValueError('Native replay library differs')
        for case in m['cases']:
            folder=study/'cases'/case['case_id']
            for name,checksum in read(folder/'receipt.json').items():checked(folder/name,checksum)
            result=read(folder/'result.json');rows=read(folder/'native_calls.json')
            if any(result[k]!=v for k,v in case.items()) or len(rows)!=result['scheduled_native_calls']:
                raise ValueError('Native case denominator changed')
            family=result['family_envelope']
            reconstructed,reason,expected_inputs=_family_and_inputs(m,case)
            if (reconstructed.serial() if reconstructed else None)!=family or reason!=result['upstream_reason']:
                raise ValueError('Family contract reconstruction differs')
            if len(expected_inputs)!=len(rows) or any(not np.array_equal(expected,row['input_endpoints'])
                    for expected,row in zip(expected_inputs,rows)):
                raise ValueError('Native inputs are not the sealed old traces/fixtures')
            if family is not None:
                if len(family['endpoint_boxes'])!=4 or any(len(p)!=2 or any(len(q)!=2 or F(q[0])>F(q[1]) for q in p)
                        for p in family['endpoint_boxes']):raise ValueError('Invalid serialized endpoint boxes')
                boxes=[[(F(q[0]),F(q[1])) for q in p] for p in family['endpoint_boxes']]
                low=[max(min(boxes[0][j][0],boxes[1][j][0]),min(boxes[2][j][0],boxes[3][j][0])) for j in (0,1)]
                high=[min(max(boxes[0][j][1],boxes[1][j][1]),max(boxes[2][j][1],boxes[3][j][1])) for j in (0,1)]
                expected_normal=None if any(a>b for a,b in zip(low,high)) else [[str(a),str(b)] for a,b in zip(low,high)]
                if family['normal_box']!=expected_normal:raise ValueError('Normal-path envelope intersection differs')
            bound_cases+=family is not None;refused+=family is None
            for row in rows:
                endpoints=row['input_endpoints'];value=native.intersect(endpoints)
                if (value is None)!=(row['native_point'] is None) or (value is not None and not np.array_equal(value,row['native_point'])):
                    raise ValueError('Actual GEOS output not reproduced bitwise')
                kind,_=exact_relationship(endpoints)
                if case['kind']=='fixture' and (kind!='NONE')!=case['intersects']:
                    raise ValueError('Fixture contract changed')
                if (value is not None)!=(kind!='NONE') or kind!=row['exact_relationship']:
                    raise ValueError('Native/exact topology comparison differs')
                if value is not None:
                    # Direct coordinate-envelope or endpoint postcondition,
                    # independent of the core outer-set builder.
                    p=tuple(F.from_float(v) for v in value)
                    rational=[tuple(F.from_float(float(v)) for v in q) for q in endpoints]
                    in_both=all(min(rational[i][j],rational[i+1][j])<=p[j]<=max(rational[i][j],rational[i+1][j])
                        for i in (0,2) for j in (0,1))
                    if not (in_both or p in rational):raise ValueError('Native structural postcondition violated')
                    if family is not None and not _serialized_contains(family,value):raise ValueError('Native family enclosure violated')
                    points+=1
                else:empty+=1
                calls+=1
    result={'study_manifest_sha256':digest(study/'manifest.json'),'cases_replayed':len(m['cases']),
        'actual_native_calls_replayed':calls,'native_point_outputs':points,'native_empty_outputs':empty,
        'bounded_returned_point_families':bound_cases,'upstream_uncertified_families':refused,
        'all_match':calls==summary['native_calls'] and points==summary['native_point_outputs'] and empty==summary['native_empty_outputs'],
        'scope':'actual C API replay and exact XY postcondition checks; not full-kernel binary verification or service certification',
        'runtime_certificate_restored':False,'new_motion_executions':0,'new_independent_samples':0}
    output.mkdir(parents=True,exist_ok=False);write_json(output/'audit.json',result)
    if not result['all_match']:raise ValueError('Native replay summary differs')
    return result
