"""Old frozen union/resampling checks and independent rational replay."""
from fractions import Fraction as F
import math
from pathlib import Path

import numpy as np

from .convex_transfer_audit import read,checked
from .round_buffer_source_audit import coordinate_box,contains_coordinates
from .circle_source_audit import capture_distance
from udc.multigroup.offset_interval import Interval
from udc.multigroup.circle_source_enclosure import circular_source_box
from udc.multigroup.universal_precision_enclosure import unknown_precision_box
from udc.multigroup.union_coordinate_enclosure import unary_union_coordinate_box,interpolation_coordinate_box
from udc.experiments.static_multigroup import write_json,digest
from udc.experiments.frozen_resume import atomic_json


def input_data(m,index):
    task=m['tasks'][index];original=Path(m['original'])
    checked(original/task['input'],task['input_sha256'])
    with np.load(original/task['input'],allow_pickle=False) as data:
        positions,radii=data['positions'].copy(),data['radii'].copy()
    parent=Path(m['parent'])/'inputs'/task['task_id']
    checked(parent/'receipt.json',m['parent_receipts'][task['task_id']])
    receipt=read(parent/'receipt.json')
    for name,h in receipt.items():checked(parent/name,h)
    return task,positions,radii,parent,receipt


def worker(argument):
    study,index=argument;study=Path(study);m=read(study/'manifest.json')
    task,positions,radii,parent,receipt=input_data(m,index)
    folder=study/'inputs'/task['task_id'];folder.mkdir(parents=True,exist_ok=False)
    epsilon=F.from_float(task['error_bound_m']);raw=[];people=[];distances={}
    for point,radius in zip(positions,radii):
        center=tuple(Interval(F.from_float(float(v))-epsilon,F.from_float(float(v))+epsilon) for v in point)
        argument=float(radius+task['error_bound_m']+m['model']['construction_margin_m'])
        if argument.hex() not in distances:distances[argument.hex()]=capture_distance(argument,16)
        source=circular_source_box(center,distances[argument.hex()]);raw.append(source)
        people.append(dict(center_box=[q.serial() for q in center],radius_argument_hex=argument.hex(),
            source_box=[q.serial() for q in source]))
    initial=tuple(Interval(min(b[j].lo for b in raw),max(b[j].hi for b in raw)) for j in (0,1))
    # Each source buffer has its own possible two quantizers before cascading.
    initial_output=unknown_precision_box(initial,stages=2)
    union=unary_union_coordinate_box(initial_output,len(positions),quantizers_per_binary=m['design'].get('binary_quantizer_limit',2))
    write_json(folder/'input_enclosure.json',dict(people=people,captured_distances={k:v.hex() for k,v in distances.items()},
        raw_source_hull=[q.serial() for q in initial],point_buffer_output_box=[q.serial() for q in initial_output],union=union.serial()))
    rows=[];refuted=False;groups=0
    for name in sorted(n for n in receipt if n.endswith('.npz')):
        if (study/'STOP.json').exists():break
        with np.load(parent/name,allow_pickle=False) as data:
            disks,offset,reference=(data[k].copy() for k in ('disks','offset','reference'))
        source=coordinate_box(offset);resampled,errors=interpolation_coordinate_box(source)
        row=dict(group_file=name,union_contains_saved_disks=contains_coordinates(union.output_box,disks),
            disks_coordinate_count=len(disks),offset_box=[q.serial() for q in source],
            resampling_box=[q.serial() for q in resampled],resampling_errors=[str(e) for e in errors],
            resampling_contains_saved_reference=contains_coordinates(resampled,reference),reference_coordinate_count=len(reference))
        rows.append(row);groups+=1
        if not row['union_contains_saved_disks'] or not row['resampling_contains_saved_reference']:
            refuted=True;write_json(folder/'counterexample.json',row)
            np.savez_compressed(folder/'counterexample_coordinates.npz',disks=disks,offset=offset,reference=reference)
            atomic_json(study/'STOP.json',dict(reason='conditional_union_or_resampling_bound_refuted',input=task['task_id'],scientific_retry=False));break
    write_json(folder/'records.json',rows)
    result=dict(task_id=task['task_id'],groups_checked=groups,refuted=refuted,
        disks_coordinates_checked=sum(r['disks_coordinate_count'] for r in rows),
        reference_coordinates_checked=sum(r['reference_coordinate_count'] for r in rows),
        union_width_inflation_vs_raw_source=float(sum(q.hi-q.lo for q in union.output_box)/sum(q.hi-q.lo for q in initial)))
    write_json(folder/'result.json',result)
    names=['input_enclosure.json','records.json','result.json']+(['counterexample.json','counterexample_coordinates.npz'] if refuted else [])
    write_json(folder/'receipt.json',{n:digest(folder/n) for n in names})
    return result


def audit(study,output):
    study,output=Path(study),Path(output);output.mkdir(parents=True,exist_ok=False);m=read(study/'manifest.json')
    checked(study/'manifest.json',read(study/'seal.json')['manifest_sha256'])
    completed=read(study/'completion.json')['receipts']
    counts=dict(inputs_checked=0,people_source_formulas=0,groups_checked=0,disks_coordinates_checked=0,reference_coordinates_checked=0)
    u=F(1,2**53);eta=F(1,2**1075);C=2*(1+u)**2
    box=lambda values:tuple(tuple(map(F,q)) for q in values)
    def inside(bound,points):
        return all(lo<=F.from_float(float(a))<=F.from_float(float(b))<=hi
            for (lo,hi),a,b in zip(bound,points.min(axis=0),points.max(axis=0)))
    def rounds(values):
        result=[]
        for lo,hi in values:
            a,b=float(lo),float(hi)
            if F.from_float(a)<lo:a=math.nextafter(a,math.inf)
            if F.from_float(b)>hi:b=math.nextafter(b,-math.inf)
            assert a<=b
            result.append((F.from_float(a),F.from_float(b)))
        return result
    for index,task in enumerate(m['tasks']):
        folder=study/'inputs'/task['task_id']
        checked(folder/'receipt.json',completed[task['task_id']])
        for n,h in read(folder/'receipt.json').items():checked(folder/n,h)
        _,positions,radii,parent,receipt=input_data(m,index);saved=read(folder/'input_enclosure.json');raw=[]
        assert len(saved['people'])==len(positions)
        epsilon=F.from_float(task['error_bound_m'])
        for point,radius,person in zip(positions,radii,saved['people']):
            center=tuple((F.from_float(float(v))-epsilon,F.from_float(float(v))+epsilon) for v in point)
            assert center==box(person['center_box'])
            argument=float(radius+task['error_bound_m']+m['model']['construction_margin_m'])
            assert argument.hex()==person['radius_argument_hex']
            # Direct ordinary-numpy scalar formula, not the intercepted helper.
            distance=argument/np.cos(np.pi/64)+1e-12
            assert float(distance).hex()==saved['captured_distances'][argument.hex()]
            d=F.from_float(float(distance))
            source=tuple((F.from_float(float(lo-d)),F.from_float(float(hi+d))) for lo,hi in rounds(center))
            assert source==box(person['source_box']);raw.append(source);counts['people_source_formulas']+=1
        initial=tuple((min(v[j][0] for v in raw),max(v[j][1] for v in raw)) for j in (0,1))
        assert initial==box(saved['raw_source_hull'])
        def closed_form(values,n):
            gain=C**n;extra=eta*(gain-1)/(C-1)
            return tuple((gain*lo-extra if lo<0 else F(0),gain*hi+extra if hi>0 else F(0)) for lo,hi in values)
        point_output=closed_form(initial,2)
        assert point_output==box(saved['point_buffer_output_box'])
        depth=0
        while 2**depth<len(positions):depth+=1
        stage_limit=m['design'].get('binary_quantizer_limit',2)*depth
        union=closed_form(point_output,stage_limit);info=saved['union']
        assert point_output==box(info['input_box'])
        assert union==box(info['output_box']) and info['polygon_component_limit']==len(positions)
        assert info['maximum_binary_depth']==depth and info['unknown_quantizer_stage_limit']==stage_limit
        assert not info['component_limit_proved'] and not info['all_inputs_defined'] and not info['service_certificate']
        rows=read(folder/'records.json')
        assert len(rows)==task['group_count']
        assert {row['group_file'] for row in rows}=={n for n in receipt if n.endswith('.npz')}
        for row in rows:
            with np.load(parent/row['group_file'],allow_pickle=False) as data:
                disks,offset,reference=(data[k].copy() for k in ('disks','offset','reference'))
            source=tuple((F.from_float(float(a)),F.from_float(float(b))) for a,b in zip(offset.min(axis=0),offset.max(axis=0)))
            assert source==box(row['offset_box'])
            errors=tuple(u*max(abs(lo),abs(hi))+(3*u+3*u*u+u**3)*(hi-lo)+(3+3*u+u*u)*eta for lo,hi in source)
            result=tuple((lo-e,hi+e) for (lo,hi),e in zip(source,errors))
            assert result==box(row['resampling_box']) and errors==tuple(map(F,row['resampling_errors']))
            assert inside(union,disks)==row['union_contains_saved_disks']==True
            assert inside(result,reference)==row['resampling_contains_saved_reference']==True
            counts['groups_checked']+=1;counts['disks_coordinates_checked']+=len(disks);counts['reference_coordinates_checked']+=len(reference)
        counts['inputs_checked']+=1
    write_json(output/'audit.json',dict(all_match=True,**counts,new_native_geometry_calls=0,new_independent_samples=0,
        new_motion_executions=0,component_limit_proved=False,original_reference_enclosed=False,service_certificate=False))
