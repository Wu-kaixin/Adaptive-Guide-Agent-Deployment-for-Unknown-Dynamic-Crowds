"""Exact local reconstruction of a saved closing/hull counterexample.

Finds a pair of offset segments whose exact rational intersection, rounded
coordinatewise, reproduces the saved output. This is a concrete arithmetic
explanation, not instrumentation proving the unique internal GEOS path or a
universal error bound for all offsets, intersections, simplification and noding.
"""
from fractions import Fraction as F
import math
from pathlib import Path

import numpy as np

from .convex_transfer_audit import read, checked, exact_supports, support_violations
from udc.experiments.static_multigroup import digest, write_json


def rational_intersection(a,b,c,d):
    cross=lambda x,y:x[0]*y[1]-x[1]*y[0]
    u=tuple(y-x for x,y in zip(a,b))
    v=tuple(y-x for x,y in zip(c,d))
    w=tuple(y-x for x,y in zip(a,c))
    denominator=cross(u,v)
    if denominator==0:
        return None
    t,s=cross(w,v)/denominator,cross(w,u)/denominator
    return tuple(a[j]+u[j]*t for j in range(2)),t,s


def diagnose(study,output):
    study,output=Path(study),Path(output)
    manifest=read(study/'manifest.json')
    checked(study/'manifest.json',read(study/'seal.json')['manifest_sha256'])
    row=next(r for r in read(study/'results.json') if r['task_id']=='input_0000')
    test=row['records'][0]
    if test['test']!='closed_in_input_hull' or not test['violations']:
        raise ValueError('Expected saved closing/hull counterexample')
    witness=max(test['violations'],key=lambda v:F(v['excess_fraction'])**2/sum(n*n for n in v['direction']))
    geometry=study/'inputs'/row['task_id']/test['stage_geometry']
    checked(geometry,test['stage_geometry_sha256'])
    with np.load(geometry,allow_pickle=False) as data:
        stages={k:data[k].copy() for k in data.files}
    # Establish the omission directly on identical v2 stage coordinates.
    previous=study.parent/'convex_support_transfer_development_v2'/'inputs'/row['task_id']/test['stage_geometry']
    with np.load(previous,allow_pickle=False) as data:
        if not all(np.array_equal(data[k],stages[k]) for k in data.files):
            raise ValueError('The full-facet study did not reuse identical stage coordinates')
    if support_violations(stages['disks'],stages['closed']):
        raise ValueError('This example must be missed by the old 16 directions')
    q=stages['closed'][witness['target_vertex_index']]
    original=read(Path(manifest['original'])/'manifest.json')
    radius=original['base_spec']['model']['closing_radius_m']
    f=lambda value:F.from_float(float(value))
    positive=stages['closing_positive']
    edges=[]
    for a,b in zip(positive,np.roll(positive,-1,axis=0)):
        dx,dy=float(b[0])-float(a[0]),float(b[1])-float(a[1])
        length=math.sqrt(dx*dx+dy*dy)
        if length==0:
            edges.append(None)
            continue
        ux,uy=-radius*dx/length,-radius*dy/length
        edges.append(tuple(tuple(f(v) for v in (float(p[0])-uy,float(p[1])+ux)) for p in (a,b)))
    directions=[tuple(n) for n in test['directions']]
    supports=exact_supports(stages['disks'],directions)
    direction=tuple(witness['direction'])
    source_support=F(witness['source_support_fraction'])
    dot=lambda p,n:sum(x*y for x,y in zip(p,n))
    matches=[]
    for j,(edge,next_edge) in enumerate(zip(edges,edges[1:]+edges[:1])):
        if edge is None or next_edge is None:
            continue
        result=rational_intersection(*edge,*next_edge)
        if result is None:
            continue
        point,t,s=result
        if not 0<=t<=1 or not 0<=s<=1 or not np.array_equal(np.array([float(v) for v in point]),q):
            continue
        exact_inside=all(dot(point,n)<=h for n,(h,_) in zip(directions,supports))
        if not exact_inside or dot(point,direction)!=source_support:
            continue
        errors=[f(v)-p for v,p in zip(q,point)]
        matches.append({'positive_edge_indices':[j,(j+1)%len(edges)],
            'negative_offset_segment_endpoints':[[[str(c) for c in p] for p in line] for line in (edge,next_edge)],
            'exact_segment_parameters':[str(t),str(s)],'exact_intersection':[str(v) for v in point],
            'exact_intersection_inside_all_raw_hull_facets':exact_inside,
            'exact_intersection_on_witness_facet':True,'rounded_intersection_equals_saved_point':True,
            'coordinate_rounding_errors':[str(v) for v in errors],
            'support_error_explained_by_rounding':str(dot(errors,direction))})
    if not matches or any(F(m['support_error_explained_by_rounding'])!=F(witness['excess_fraction']) for m in matches):
        raise ValueError('No exact-intersection rounding reconstruction of the saved violation')
    result={'task_id':row['task_id'],'source':row['source'],'witness':witness,
        'saved_point':q.tolist(),'same_stage_coordinates_as_v2':True,'old_16_direction_checks_miss_this_violation':True,
        'matching_local_reconstructions':matches,
        'unit_normal_excess_squared_fraction':str(F(witness['excess_fraction'])**2/sum(n*n for n in direction)),
        'unit_normal_excess_approx_m':float(F(witness['excess_fraction']))/math.hypot(*direction),
        'source_geometry':str(geometry),'source_geometry_sha256':digest(geometry),
        'source_manifest_sha256':digest(study/'manifest.json'),
        'source_references':['https://raw.githubusercontent.com/libgeos/geos/3.14.1/src/operation/buffer/OffsetSegmentGenerator.cpp',
            'https://raw.githubusercontent.com/libgeos/geos/3.14.1/src/operation/buffer/OffsetCurveBuilder.cpp'],
        'scope':'exact counterexample and matching local offset/intersection rounding reconstruction; not unique internal path instrumentation or all-world kernel error bound',
        'new_independent_samples':0,'new_motion_executions':0,'runtime_certificate_restored':False}
    if output.exists():
        raise FileExistsError(output)
    write_json(output,result)
    return result
