"""Fixed old-edge diagnostics of local interval contracts, without motion."""
from fractions import Fraction as F
from itertools import product
import math
from pathlib import Path

import numpy as np

from udc.multigroup.offset_interval import (Interval,UncertifiedArithmetic,point_box,offset_segment,
    rounded_exact_intersection,U,ETA)
from .closing_witness_diagnosis import rational_intersection
from .convex_transfer_audit import read,checked
from udc.experiments.static_multigroup import write_json,digest


def numeric_offset(a,b,distance,side):
    """Reproduce source expression and verify every observed rounding step."""
    operations=0
    def op(name,a,b=None):
        nonlocal operations
        if name=='sqrt':
            value=math.sqrt(a)
            lower=(F.from_float(math.nextafter(value,-math.inf))+F.from_float(value))/2
            upper=(F.from_float(value)+F.from_float(math.nextafter(value,math.inf)))/2
            if not lower**2<=F.from_float(a)<=upper**2:
                raise ValueError('Observed sqrt violates nearest-rounding interval')
        else:
            af,bf=F.from_float(a),F.from_float(b)
            exact={'add':lambda:af+bf,'sub':lambda:af-bf,'mul':lambda:af*bf,'div':lambda:af/bf}[name]()
            value={'add':lambda:a+b,'sub':lambda:a-b,'mul':lambda:a*b,'div':lambda:a/b}[name]()
            if not math.isfinite(value) or abs(F.from_float(value)-exact)>U*abs(exact)+ETA:
                raise ValueError('Observed binary64 operation violates declared contract')
        operations+=1
        return value
    dx,dy=op('sub',float(b[0]),float(a[0])),op('sub',float(b[1]),float(a[1]))
    norm=op('add',op('mul',dx,dx),op('mul',dy,dy))
    if norm==0:
        return None,operations
    length=op('sqrt',norm)
    signed=side*float(distance)
    ux,uy=op('div',op('mul',signed,dx),length),op('div',op('mul',signed,dy),length)
    output=tuple((op('sub',float(p[0]),uy),op('add',float(p[1]),ux)) for p in (a,b))
    return output,operations


def _inside_float(value,interval):
    q=float(value)
    if F.from_float(q)<interval.lo:
        q=math.nextafter(q,math.inf)
    if F.from_float(q)>interval.hi:
        q=math.nextafter(q,-math.inf)
    if not interval.contains(F.from_float(q)):
        raise ValueError('No representable sample at declared box endpoint')
    return q


def samples(points,error):
    boxes=[point_box(p,error) for p in points]
    yield [list(map(float,p)) for p in points]
    for signs in product((0,1),repeat=2*len(points)):
        yield [[_inside_float((box[j].lo,box[j].hi)[signs[2*i+j]],box[j]) for j in range(2)] for i,box in enumerate(boxes)]


def _zero_length_witness(a,b):
    point=[]
    for x,y in zip(a,b):
        lo,hi=max(x.lo,y.lo),min(x.hi,y.hi)
        if lo>hi:
            return None
        candidate=float(lo)
        if F.from_float(candidate)<lo:
            candidate=math.nextafter(candidate,math.inf)
        if F.from_float(candidate)>hi:
            return None
        point.append(candidate)
    return point


def worker(argument):
    study,case=argument
    study=Path(study)
    manifest=read(study/'manifest.json')
    checked(Path(manifest['source_geometry']),manifest['source_geometry_sha256'])
    with np.load(manifest['source_geometry'],allow_pickle=False) as data:
        positive=data['closing_positive'].copy()
        saved_witness=data['closed'][147].copy()
    indices=case['source_vertex_indices']
    points=[positive[i] for i in indices]
    error=case['coordinate_uncertainty_m']
    distance=manifest['design']['offset_distance_m']
    side=manifest['design']['side']
    tighten=manifest['design'].get('tighten_normal',False)
    boxes=[point_box(p,error) for p in points]
    folder=study/'cases'/case['case_id']
    folder.mkdir(parents=True,exist_ok=False)
    np.savez_compressed(folder/'input.npz',points=np.array(points))
    bounds,reason,zero=None,None,None
    try:
        if case['kind']=='offset':
            bounds=offset_segment(*boxes,Interval.point(distance),side,tighten_normal=tighten)
        else:
            first=offset_segment(boxes[0],boxes[1],Interval.point(distance),side,tighten_normal=tighten)
            second=offset_segment(boxes[1],boxes[2],Interval.point(distance),side,tighten_normal=tighten)
            point,t,s=rounded_exact_intersection(*first,*second)
            bounds=(point,)
    except UncertifiedArithmetic as exc:
        reason=str(exc)
        if case['kind']=='offset':
            zero=_zero_length_witness(*boxes)
    traces=[]
    violations=operations=undefined=0
    for sample in samples(points,error):
        if case['kind']=='offset':
            output,count=numeric_offset(*sample,distance,side)
        else:
            first,count1=numeric_offset(sample[0],sample[1],distance,side)
            second,count2=numeric_offset(sample[1],sample[2],distance,side)
            count=count1+count2
            intersection=None if first is None or second is None else rational_intersection(
                *[tuple(F.from_float(v) for v in p) for p in (*first,*second)])
            output=None if intersection is None else (tuple(float(v) for v in intersection[0]),)
        operations+=count
        if output is None:
            undefined+=1
        outside=bool(bounds is not None and (output is None or any(not bound.contains(F.from_float(v))
            for point,limit in zip(output,bounds) for v,bound in zip(point,limit))))
        violations+=outside
        traces.append({'input_points':sample,'output':output,'outside_enclosure':outside})
    width=max((float(v.hi-v.lo) for p in bounds for v in p),default=None) if bounds else None
    result=case|{'status':'ENCLOSED_LOCAL_OPERATION' if bounds else 'UNCERTIFIED','reason':reason,
        'bounds':[[v.serial() for v in p] for p in bounds] if bounds else None,
        'trace_count':len(traces),'observed_operation_checks':operations,'undefined_trace_count':undefined,
        'enclosure_violations':violations,'maximum_coordinate_interval_width_m':width,
        'admissible_zero_length_witness':zero,
        'saved_closing_witness_enclosed':(all(bounds[0][j].contains(F.from_float(float(saved_witness[j]))) for j in range(2))
            if case['kind']=='intersection' and error==0 and bounds else None),
        'scope':'endpoint-coordinate box experiment; not a person observation error certificate; exact-intersection model is not GEOS DD'}
    write_json(folder/'traces.json',traces)
    write_json(folder/'result.json',result)
    write_json(folder/'receipt.json',{name:digest(folder/name) for name in ('input.npz','traces.json','result.json')})
    if violations:
        raise ValueError('MODEL_BOUND_VIOLATION: local operation escaped preserved interval')
    return result


def audit(study,output):
    """Reproduce traces and compare serialized rational bounds directly.

    Does not invoke the interval builder. Finite trace checks supplement the
    analytic conditional contract; they are not an exhaustive floating model.
    """
    study,output=Path(study).resolve(),Path(output).resolve()
    manifest=read(study/'manifest.json')
    checked(study/'manifest.json',read(study/'seal.json')['manifest_sha256'])
    for name,checksum in manifest['sources'].items():
        checked(study/'source_snapshot'/name,checksum)
    checked(Path(manifest['source_geometry']),manifest['source_geometry_sha256'])
    with np.load(manifest['source_geometry'],allow_pickle=False) as data:
        source=data['closing_positive'].copy()
    summary=read(study/'summary.json')
    ledger=read(study/'results.json')
    if len(ledger)!=len(manifest['cases']) or {r['case_id'] for r in ledger}!={r['case_id'] for r in manifest['cases']}:
        raise ValueError('Incomplete local arithmetic ledger')
    traces_checked=operations_checked=rounding_checked=zero_witnesses=bound_cases=refused=0
    widths=[]
    for case in manifest['cases']:
        folder=study/'cases'/case['case_id']
        for name,checksum in read(folder/'receipt.json').items():
            checked(folder/name,checksum)
        saved=read(folder/'result.json')
        if any(saved[k]!=v for k,v in case.items()):
            raise ValueError('Case condition changed')
        expected=source[case['source_vertex_indices']]
        with np.load(folder/'input.npz',allow_pickle=False) as data:
            if not np.array_equal(data['points'],expected):
                raise ValueError('Old source coordinates changed')
        bounds=saved['bounds']
        if (bounds is not None)!=(saved['status']=='ENCLOSED_LOCAL_OPERATION'):
            raise ValueError('Uncertified case gained an enclosure')
        if bounds is not None:
            bound_cases+=1
            if len(bounds)!=(2 if case['kind']=='offset' else 1) or any(len(p)!=2 for p in bounds):
                raise ValueError('Wrong output enclosure shape')
            bounds=[[tuple(map(F,q)) for q in p] for p in bounds]
            if any(lo>hi for p in bounds for lo,hi in p):raise ValueError('Reversed saved interval')
        else:refused+=1
        error=F.from_float(float(case['coordinate_uncertainty_m']))
        def within(points):
            return len(points)==len(expected) and all(len(p)==2 and all(
                abs(F.from_float(float(v))-F.from_float(float(c)))<=error for v,c in zip(p,center))
                for p,center in zip(points,expected))
        traces=read(folder/'traces.json')
        if len(traces)!=1+2**(2*len(expected)) or len(traces)!=saved['trace_count']:
            raise ValueError('Trace denominator changed')
        local_ops=0
        for trace in traces:
            sample=trace['input_points']
            if not within(sample):raise ValueError('Sample outside declared endpoint boxes')
            if case['kind']=='offset':
                result,count=numeric_offset(*sample,manifest['design']['offset_distance_m'],manifest['design']['side'])
            else:
                first,count1=numeric_offset(sample[0],sample[1],manifest['design']['offset_distance_m'],manifest['design']['side'])
                second,count2=numeric_offset(sample[1],sample[2],manifest['design']['offset_distance_m'],manifest['design']['side'])
                count=count1+count2
                intersection=None if first is None or second is None else rational_intersection(
                    *[tuple(F.from_float(v) for v in p) for p in (*first,*second)])
                result=None if intersection is None else (tuple(float(v) for v in intersection[0]),)
                if result:
                    for exact,value in zip(intersection[0],result[0]):
                        if abs(F.from_float(value)-exact)>U*abs(exact)+ETA:
                            raise ValueError('Final exact-intersection coordinate rounding differs')
                        rounding_checked+=1
            if (result is None)!=(trace['output'] is None) or (result is not None and not np.array_equal(result,trace['output'])):
                raise ValueError('Numeric expression output not reproduced bitwise')
            if bounds is not None and (result is None or any(not lo<=F.from_float(v)<=hi
                    for p,b in zip(result,bounds) for v,(lo,hi) in zip(p,b))):
                raise ValueError('MODEL_BOUND_VIOLATION: saved output escapes rational enclosure')
            traces_checked+=1;local_ops+=count
        if local_ops!=saved['observed_operation_checks']:
            raise ValueError('Observed operation denominator differs')
        operations_checked+=local_ops
        witness=saved['admissible_zero_length_witness']
        if witness is not None:
            if case['kind']!='offset' or not within([witness,witness]):
                raise ValueError('Invalid zero-length witness')
            value,_=numeric_offset(witness,witness,manifest['design']['offset_distance_m'],manifest['design']['side'])
            if value is not None:raise ValueError('Coincident endpoints did not reproduce undefined offset')
            zero_witnesses+=1
    result={'study_manifest_sha256':digest(study/'manifest.json'),'cases_replayed':len(ledger),
        'enclosed_local_cases':bound_cases,'uncertified_cases':refused,'expression_traces_replayed':traces_checked,
        'binary64_operation_checks':operations_checked,'final_intersection_rounding_checks':rounding_checked,
        'exact_zero_length_witnesses_verified':zero_witnesses,
        'all_match':traces_checked==summary['sampled_expression_traces'] and operations_checked==summary['observed_operation_checks']
            and zero_witnesses==summary['admissible_zero_length_witnesses'] and bound_cases==summary['enclosed_local_cases']
            and refused==summary['uncertified_cases'],'runtime_certificate_restored':False,
        'scope':'all saved local traces, observed operation rounding checks and rational bound comparisons; not GEOS whole-kernel verification',
        'new_independent_samples':0,'new_motion_executions':0}
    output.mkdir(parents=True,exist_ok=False)
    write_json(output/'audit.json',result)
    if not result['all_match']:raise ValueError('Local arithmetic replay denominator mismatch')
    return result
