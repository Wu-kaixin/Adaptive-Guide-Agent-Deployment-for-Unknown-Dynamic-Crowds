"""Native point quantization and fixed-overlay diagnostics on exposed inputs."""
from fractions import Fraction as F
import math
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import Point,LineString

from .convex_transfer_audit import read,checked
from .native_geos_segment import NativeSegmentIntersector
from udc.multigroup.offset_interval import Interval,UncertifiedArithmetic,point_box
from udc.multigroup.precision_enclosure import PrecisionModel,invariant_axis_box,_representable_inside
from udc.experiments.static_multigroup import write_json,digest


def parent_points(manifest):
    parent=Path(manifest['parent']);points=[]
    for name,checksum in manifest['parent_receipts'].items():
        checked(parent/name,checksum)
        folder=(parent/name).parent
        for artifact,value in read(parent/name).items():checked(folder/artifact,value)
        points.extend(row['native_point'] for row in read(folder/'native_calls.json') if row['native_point'] is not None)
    return points


def _samples(point,error):
    boxes=point_box(point,error)
    lows,highs=zip(*[_representable_inside(q) for q in boxes])
    return [list(map(float,point)),list(map(float,lows)),list(map(float,highs))],boxes


def _closure(box,model,design):
    try:
        closed,steps=invariant_axis_box(box,[model],max_steps=design['axis_closure_max_steps'],
            include_scaled_noder=design['include_scaled_noder_map'])
        return closed,steps,None
    except UncertifiedArithmetic as exc:
        return None,None,str(exc)


def worker(argument):
    study,case=argument;study=Path(study);manifest=read(study/'manifest.json');design=manifest['design']
    model=PrecisionModel.from_grid(case['grid']);error=case['coordinate_uncertainty_m']
    points=parent_points(manifest)
    step=model.grid if model.grid>1 else 1/model.scale if model.scale else F(1)
    for integer in design['half_tie_integers']:
        center=float((integer+F(1,2))*step)
        points.extend([[x,-x] for x in (math.nextafter(center,-math.inf),center,math.nextafter(center,math.inf))])
    folder=study/'cases'/case['case_id'];folder.mkdir(parents=True,exist_ok=False)
    traces=[];naive=None;displacements=0;overlays=[]
    for index,point in enumerate(points):
        samples,boxes=_samples(point,error)
        bounds=[model.range(q) for q in boxes];drift=[model.displacement_bound(q) for q in boxes]
        for sample in samples:
            native=shapely.set_precision(Point(sample),case['grid'],mode='pointwise')
            value=[native.x,native.y]
            metadata=float(shapely.get_precision(native))
            predicted=[model.quantize(F.from_float(v)) for v in sample]
            inside=all(q.contains(F.from_float(v)) for q,v in zip(bounds,value))
            equal=all(F.from_float(v)==p for v,p in zip(value,predicted))
            bounded=all(abs(F.from_float(v)-F.from_float(x))<=d for v,x,d in zip(value,sample,drift))
            matches_grid=F.from_float(metadata)==model.reported_grid
            traces.append({'point_index':index,'input':sample,'native_output':value,'native_hex':[v.hex() for v in value],
                'reported_grid':metadata,'range_bounds':[q.serial() for q in bounds],
                'drift_bounds':[str(q) for q in drift],'model_value_equal':equal,
                'inside_range':inside,'inside_drift_bound':bounded,'reported_grid_matches':matches_grid})
            displacements+=2
            if not (inside and equal and bounded and matches_grid):
                write_json(folder/'failed_trace.json',traces[-1]);raise ValueError('MODEL_BOUND_VIOLATION: native fixed precision disagrees')
            if naive is None and case['grid']>0:
                for coordinate,(v,x) in enumerate(zip(value,sample)):
                    delta=abs(F.from_float(v)-F.from_float(x))
                    if delta>F.from_float(case['grid'])/2:
                        naive={'input':sample,'native_output':value,'coordinate':coordinate,
                            'absolute_displacement_fraction':str(delta),'half_requested_grid_fraction':str(F.from_float(case['grid'])/2),
                            'diagnostic_only_not_the_active_bound':True}
                        break
    # Separate native fixed-overlay evidence. Zero-length LineString inputs are
    # excluded by this protocol; their segment-API tests remain in the parent.
    parent_manifest=read(Path(manifest['parent'])/'manifest.json')
    fixtures=[c for c in parent_manifest['cases'] if c['kind']=='fixture' and c['name'] in design['overlay_fixture_names']]
    for fixture in fixtures:
        endpoints=fixture['points'];boxes=[point_box(p,error) for p in endpoints]
        axis=tuple(Interval(min(p[j].lo for p in boxes),max(p[j].hi for p in boxes)) for j in (0,1))
        closed,steps,reason=_closure(axis,model,design)
        value=shapely.intersection(LineString(endpoints[:2]),LineString(endpoints[2:]),grid_size=case['grid'])
        coords=shapely.get_coordinates(value).tolist()
        inside=None if closed is None else all(q.contains(F.from_float(float(v))) for p in coords for q,v in zip(closed,p))
        row={'fixture':fixture['name'],'geometry_type':value.geom_type,'empty':bool(value.is_empty),
            'coordinates':coords,'closed_axis_box':[q.serial() for q in closed] if closed else None,
            'closure_steps':steps,'closure_reason':reason,'inside_conditional_closed_axis_box':inside}
        overlays.append(row)
        if inside is False:
            write_json(folder/'failed_overlay.json',row);raise ValueError('MODEL_BOUND_VIOLATION: native overlay escapes candidate closure')
    result=case|{'model':model.serial(),'base_points':len(points),'native_point_calls':len(traces),
        'coordinate_range_and_drift_checks':displacements,'native_overlay_calls':len(overlays),
        'overlay_empty':sum(r['empty'] for r in overlays),
        'overlay_with_verified_axis_closure':sum(r['closed_axis_box'] is not None for r in overlays),
        'overlay_axis_closure_uncertified':sum(r['closed_axis_box'] is None for r in overlays),
        'naive_half_grid_counterexample':naive,'runtime_certificate_restored':False}
    write_json(folder/'point_traces.json',traces);write_json(folder/'overlays.json',overlays);write_json(folder/'result.json',result)
    write_json(folder/'receipt.json',{n:digest(folder/n) for n in ('point_traces.json','overlays.json','result.json')})
    return result


def _source_model(grid):
    # Independent scalar source-expression replay: modf branches, not the
    # rational quantizer's floor(value+1/2) construction.
    if grid==0:return 0.,0.
    scale=1./abs(grid)
    def positive_nearest(x):
        frac,integer=math.modf(x)
        return integer if frac<.5 else integer+1.
    def snap(x):
        n=positive_nearest(x)
        return n if abs(x-n)<1e-5 else x
    if scale<1:return scale,snap(1./scale)
    scale=snap(scale)
    return scale,1./scale


def _source_quantize(value,scale,grid,*,scaled=False):
    if scale==0:return value
    work=value/grid if grid>1 and not scaled else value*scale
    fraction,integer=math.modf(work)
    if abs(fraction)<.5:rounded=integer
    elif abs(fraction)>.5:rounded=math.ceil(work) if work>=0 else math.floor(work)
    else:rounded=integer+1. if work>=0 else integer
    return float(rounded*grid if grid>1 and not scaled else rounded/scale)


def _hex_points(points):return [[float(v).hex() for v in p] for p in points]


def audit(study,output):
    from udc.multigroup.offset_interval import U,ETA
    study,output=Path(study).resolve(),Path(output).resolve();m=read(study/'manifest.json')
    checked(study/'manifest.json',read(study/'seal.json')['manifest_sha256'])
    for name,checksum in m['sources'].items():checked(study/'source_snapshot'/name,checksum)
    for name,row in m['upstream_sources'].items():checked(study/'upstream_sources'/name,row['sha256'])
    checked(Path(m['parent'])/'manifest.json',m['parent_manifest_sha256'])
    output.mkdir(parents=True,exist_ok=False)
    point_calls=overlay_calls=range_checks=closure_checks=naive_cases=0
    parent_point_list=parent_points(m)
    for case in m['cases']:
        folder=study/'cases'/case['case_id']
        for name,checksum in read(folder/'receipt.json').items():checked(folder/name,checksum)
        result=read(folder/'result.json');traces=read(folder/'point_traces.json')
        model=PrecisionModel.from_grid(case['grid']);scale,grid=_source_model(case['grid'])
        if F.from_float(scale)!=model.scale or F.from_float(grid)!=model.grid:raise ValueError('Independent constructor replay differs')
        points=list(parent_point_list)
        step=model.grid if model.grid>1 else 1/model.scale if model.scale else F(1)
        for integer in m['design']['half_tie_integers']:
            center=float((integer+F(1,2))*step)
            points.extend([[x,-x] for x in (math.nextafter(center,-math.inf),center,math.nextafter(center,math.inf))])
        if len(traces)!=3*len(points) or len(traces)!=result['native_point_calls']:raise ValueError('Point-call denominator differs')
        for i,point in enumerate(points):
            error=F.from_float(float(case['coordinate_uncertainty_m']))
            inputs=[(F.from_float(float(v))-error,F.from_float(float(v))+error) for v in point]
            triple=traces[3*i:3*i+3]
            for trace in triple:
                if trace['point_index']!=i or any(not lo<=F.from_float(v)<=hi for v,(lo,hi) in zip(trace['input'],inputs)):
                    raise ValueError('Native trace source interval differs')
                native=shapely.set_precision(Point(trace['input']),case['grid'],mode='pointwise')
                values=[native.x,native.y]
                if [v.hex() for v in values]!=trace['native_hex']:raise ValueError('Actual point result not reproduced bitwise')
                direct=[_source_quantize(v,scale,grid) for v in trace['input']]
                if [v.hex() for v in values]!=[v.hex() for v in direct]:raise ValueError('Source-expression replay differs from native point')
                if float(shapely.get_precision(native))!=trace['reported_grid']:raise ValueError('Precision metadata changed')
                for j,(v,x) in enumerate(zip(values,trace['input'])):
                    lo,hi=map(F,trace['range_bounds'][j])
                    if not lo<=F.from_float(v)<=hi:raise ValueError('Native value escaped serialized interval')
                    magnitude=max(abs(q) for q in inputs[j])
                    if scale==0:bound=F(0)
                    else:
                        unit=F.from_float(grid) if grid>1 else 1/F.from_float(scale)
                        first=U*magnitude+ETA*unit+unit/2
                        bound=first+U*(magnitude+first)+ETA
                    if bound!=F(trace['drift_bounds'][j]) or abs(F.from_float(v)-F.from_float(x))>bound:
                        raise ValueError('Analytic drift bound replay differs')
                    range_checks+=1
                point_calls+=1
            # Every saved tight range equals actual evaluation at inward binary
            # endpoints; monotonicity supplies the remaining interval points.
            for j in range(2):
                interval=triple[0]['range_bounds'][j]
                if F(interval[0])!=F.from_float(triple[1]['native_output'][j]) or F(interval[1])!=F.from_float(triple[2]['native_output'][j]):
                    raise ValueError('Range endpoints are not native quantizations')
        for row in read(folder/'overlays.json'):
            parent_manifest=read(Path(m['parent'])/'manifest.json')
            fixture=next(c for c in parent_manifest['cases'] if c['kind']=='fixture' and c['name']==row['fixture'])
            endpoints=fixture['points']
            native=shapely.intersection(LineString(endpoints[:2]),LineString(endpoints[2:]),grid_size=case['grid'])
            coordinates=shapely.get_coordinates(native).tolist()
            if _hex_points(coordinates)!=_hex_points(row['coordinates']) or native.geom_type!=row['geometry_type']:
                raise ValueError('Fixed overlay output not reproduced bitwise')
            if row['closed_axis_box'] is not None:
                closed=[Interval(F(q[0]),F(q[1])) for q in row['closed_axis_box']]
                for j,q in enumerate(closed):
                    if any(not q.contains(F.from_float(float(p[j]))+sign*error) for p in endpoints for sign in (-1,1)):
                        raise ValueError('Closed box lost original endpoint set')
                    a,b=_representable_inside(q)
                    for scaled in (False,True):
                        if not q.contains(F.from_float(_source_quantize(float(a),scale,grid,scaled=scaled))) or not q.contains(F.from_float(_source_quantize(float(b),scale,grid,scaled=scaled))):
                            raise ValueError('Direct source map does not preserve saved closure')
                    if any(not q.contains(F.from_float(float(p[j]))) for p in coordinates):raise ValueError('Native overlay escaped closure')
                closure_checks+=1
            overlay_calls+=1
        naive=result['naive_half_grid_counterexample']
        if naive:
            j=naive['coordinate'];difference=abs(F.from_float(naive['native_output'][j])-F.from_float(naive['input'][j]))
            if difference!=F(naive['absolute_displacement_fraction']) or not difference>F.from_float(case['grid'])/2:
                raise ValueError('Naive-half-grid counterexample not exact')
            naive_cases+=1
    # Strong bit-pattern replay for the parent C API, including signed zeros.
    parent_native_calls=0
    with NativeSegmentIntersector() as native:
        if native.identity!=m['native_binary_identity']:raise ValueError('Native binary identity differs')
        for name in m['parent_receipts']:
            for row in read((Path(m['parent'])/name).parent/'native_calls.json'):
                value=native.intersect(row['input_endpoints'])
                if (value is None)!=(row['native_point'] is None) or value is not None and [v.hex() for v in value]!=[float(v).hex() for v in row['native_point']]:
                    raise ValueError('Parent actual native result differs at binary bit-pattern level')
                parent_native_calls+=1
    summary=read(study/'summary.json')
    result={'study_manifest_sha256':digest(study/'manifest.json'),'cases_replayed':len(m['cases']),
        'actual_native_point_calls_replayed':point_calls,'coordinate_range_and_drift_checks':range_checks,
        'native_overlay_calls_replayed':overlay_calls,'directly_verified_quantizer_closed_boxes':closure_checks,
        'naive_half_grid_counterexamples_replayed':naive_cases,'parent_native_strict_bit_replay_calls':parent_native_calls,
        'all_match':point_calls==summary['native_point_calls'] and range_checks==summary['coordinate_range_and_drift_checks']
            and overlay_calls==summary['native_overlay_calls'] and naive_cases==summary['cases_with_naive_half_grid_counterexample'],
        'runtime_certificate_restored':False,'new_independent_samples':0,'new_motion_executions':0,
        'scope':'native replay, independent scalar source expression and closure postcondition; not all BufferOp paths or original-reference service proof'}
    write_json(output/'audit.json',result)
    if not result['all_match']:raise ValueError('Fixed precision audit denominator differs')
    return result
