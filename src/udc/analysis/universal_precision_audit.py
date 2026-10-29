"""Adversarial scalar checks and coarse initial-buffer source composition."""
from fractions import Fraction as F
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import Point

from .convex_transfer_audit import read,checked
from .fixed_precision_diagnostic import _source_model,_source_quantize
from udc.multigroup.offset_interval import Interval,UncertifiedArithmetic
from udc.multigroup.precision_enclosure import PrecisionModel
from udc.multigroup.universal_precision_enclosure import unknown_precision_box,default_point_buffer_box
from udc.experiments.static_multigroup import write_json,digest
from udc.experiments.frozen_resume import atomic_json


def execute(study):
    study=Path(study);m=read(study/'manifest.json');rows=[];native_calls=0;largest=F(0)
    for case in m['cases']:
        x=float.fromhex(case['x_hex']);point=[x,-x];models=[];predicted=[];current=tuple(map(F.from_float,point))
        try:
            for grid in case['grids']:
                model=PrecisionModel.from_grid(grid);current=tuple(model.quantize(v) for v in current)
                models.append(model);predicted.append(current)
        except UncertifiedArithmetic as exc:
            rows.append(case|{'status':'UNCERTIFIED_FINITE_OPERATION_DOMAIN','reason':str(exc),'native_calls':0})
            atomic_json(study/'progress.json',{'rows':rows,'scheduled_paths':len(m['cases'])});continue
        original=tuple(Interval(v,v) for v in map(F.from_float,point));traces=[]
        for stage,(grid,model,expected) in enumerate(zip(case['grids'],models,predicted),start=1):
            # Always create a fresh FLOATING point, avoiding the existing-
            # precision clone shortcut when two requested grids are equal.
            output=shapely.set_precision(Point(point),grid,mode='pointwise');value=[output.x,output.y];native_calls+=1
            outer=unknown_precision_box(original,stages=stage)
            if any(F.from_float(v)!=q or not bound.contains(F.from_float(v)) for v,q,bound in zip(value,expected,outer)):
                write_json(study/'failed_trace.json',{'case':case,'stage':stage,'input':point,'output':value,
                    'universal_box':[q.serial() for q in outer]})
                raise ValueError('MODEL_BOUND_VIOLATION: universal precision range or native scalar model differs')
            traces.append({'stage':stage,'input_hex':[v.hex() for v in point],'output_hex':[v.hex() for v in value],
                'outer_box':[q.serial() for q in outer],'model':model.serial()})
            point=value
        if x:largest=max(largest,abs(F.from_float(point[0])/F.from_float(x)))
        rows.append(case|{'status':'FINITE_PATH_NATIVE_MATCH','native_calls':len(traces),'traces':traces})
        atomic_json(study/'progress.json',{'rows':rows,'scheduled_paths':len(m['cases'])})
    write_json(study/'scalar_results.json',rows)
    parent=Path(m['parent']);compositions=[];ratios=[]
    for name,checksum in m['parent_receipts'].items():
        receipt=parent/name;checked(receipt,checksum)
        for artifact,h in read(receipt).items():checked(receipt.parent/artifact,h)
        saved=read(receipt.parent/'enclosures.json')
        for i,(center,radius,raw) in enumerate(zip(saved['center_boxes'],saved['radius_arguments'],saved['source_boxes'])):
            box=tuple(Interval(F(q[0]),F(q[1])) for q in center)
            distance=saved['captured_native_distances'][float(radius).hex()]
            result=default_point_buffer_box(box,distance)
            original=tuple(Interval(F(q[0]),F(q[1])) for q in raw)
            if any(not outer.lo<=inner.lo<=inner.hi<=outer.hi for inner,outer in zip(original,result)):
                raise ValueError('Coarse source composition lost the original floating box')
            width=sum(q.hi-q.lo for q in original);outer_width=sum(q.hi-q.lo for q in result)
            ratio=outer_width/width;ratios.append(ratio)
            compositions.append({'case':receipt.parent.name,'person_row':i,'center_box':center,'distance':distance,
                'floating_source_box':raw,'universal_point_buffer_box':[q.serial() for q in result],
                'width_inflation_fraction':str(ratio)})
    write_json(study/'compositions.json',compositions)
    summary={'scheduled_scalar_paths':len(m['cases']),'admitted_native_paths':sum(r['status']=='FINITE_PATH_NATIVE_MATCH' for r in rows),
        'uncertified_finite_domain_paths':sum(r['status']=='UNCERTIFIED_FINITE_OPERATION_DOMAIN' for r in rows),
        'actual_native_point_calls':native_calls,'maximum_observed_path_amplification_fraction':str(largest),
        'initial_circle_source_boxes_composed':len(compositions),'width_inflation_range':[float(min(ratios)),float(max(ratios))],
        'new_native_circle_calls':0,'new_independent_samples':0,'new_motion_executions':0,
        'runtime_service_gate_changed':False,'whole_geometry_pipeline_proved':False,'goal_complete':False}
    write_json(study/'summary.json',summary);write_json(study/'receipt.json',{n:digest(study/n) for n in ['scalar_results.json','compositions.json','summary.json']})


def audit(study,output):
    study,output=Path(study),Path(output);output.mkdir(parents=True,exist_ok=False);m=read(study/'manifest.json')
    for name,h in read(study/'receipt.json').items():checked(study/name,h)
    calls=0;refusals=0;stages_checked=0;C=2*(1+F(1,2**53))**2;eta=F(1,2**1075)
    rows=read(study/'scalar_results.json')
    if len(rows)!=len(m['cases']):raise ValueError('Scalar path denominator changed')
    for case,row in zip(m['cases'],rows):
        if row['x_hex']!=case['x_hex'] or row['grids']!=case['grids']:raise ValueError('Scalar fixture changed')
        x=float.fromhex(case['x_hex']);original=[x,-x]
        if row['status']=='UNCERTIFIED_FINITE_OPERATION_DOMAIN':
            # Read-only admission replay. No claim these are observed native
            # failures; the declared finite arithmetic contract was not met.
            try:
                p=tuple(map(F.from_float,original))
                for grid in case['grids']:
                    model=PrecisionModel.from_grid(grid);p=tuple(model.quantize(v) for v in p)
            except UncertifiedArithmetic:refusals+=1;continue
            raise ValueError('Saved finite-domain refusal no longer reproduced')
        if row['native_calls']!=len(case['grids']) or len(row['traces'])!=len(case['grids']):
            raise ValueError('Native stage denominator changed')
        current=original
        for grid,trace in zip(case['grids'],row['traces']):
            if [v.hex() for v in current]!=trace['input_hex']:raise ValueError('Scalar trace input changed')
            scale,internal_grid=_source_model(grid)
            direct=[_source_quantize(v,scale,internal_grid) for v in current]
            native=shapely.set_precision(Point(current),grid,mode='pointwise');actual=[native.x,native.y]
            if [v.hex() for v in actual]!=trace['output_hex'] or [v.hex() for v in direct]!=trace['output_hex']:
                raise ValueError('Actual native or independent source-expression bit replay differs')
            for incoming,outgoing in zip(current,actual):
                value=F.from_float(outgoing);base=F.from_float(incoming)
                if abs(value)>C*abs(base)+eta or (base>=0 and value<0) or (base<=0 and value>0):
                    raise ValueError('Direct gain or sign bound violated')
                if scale:
                    work=incoming/internal_grid if internal_grid>1 else incoming*scale
                    # A nonzero output implies nonzero integer rounding.
                    if outgoing and abs(work)<.5:raise ValueError('Nonzero-round branch premise violated')
                stages_checked+=1
            current=actual;calls+=1
    composed=read(study/'compositions.json')
    for row in composed:
        raw=[tuple(map(F,q)) for q in row['floating_source_box']]
        gain=C*C;add=(C+1)*eta
        expected=[(gain*lo-add if lo<0 else F(0),gain*hi+add if hi>0 else F(0)) for lo,hi in raw]
        if expected!=[tuple(map(F,q)) for q in row['universal_point_buffer_box']]:raise ValueError('Closed-form two-stage composition differs')
    write_json(output/'audit.json',{'study_manifest_sha256':digest(study/'manifest.json'),'scalar_paths_checked':len(rows),
        'finite_domain_refusals_reproduced':refusals,'native_point_calls_replayed':calls,
        'coordinate_gain_sign_branch_checks':stages_checked,'closed_form_source_compositions_checked':len(composed),
        'all_match':True,'whole_geometry_pipeline_proved':False,'runtime_service_gate_changed':False,
        'new_native_circle_calls':0,'new_independent_samples':0,'new_motion_executions':0,
        'scope':'conditional scalar theorem and source-level default Point.buffer composition; not library binary verification or service mass'})
