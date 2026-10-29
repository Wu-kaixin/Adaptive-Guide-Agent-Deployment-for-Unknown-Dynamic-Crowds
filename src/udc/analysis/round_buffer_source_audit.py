"""Read-only full old-stage coordinate probes of a conditional source bound."""
from fractions import Fraction as F
from pathlib import Path
import math

import numpy as np

from .convex_transfer_audit import read,checked
from udc.multigroup.offset_interval import Interval
from udc.multigroup.round_buffer_source_enclosure import round_buffer_source_box
from udc.experiments.static_multigroup import write_json,digest
from udc.experiments.frozen_resume import atomic_json


def coordinate_box(points):
    if points.ndim!=2 or points.shape[1]!=2 or not len(points) or not np.isfinite(points).all():
        raise ValueError('Nonempty finite saved stage coordinates required')
    return tuple(Interval(F.from_float(float(lo)),F.from_float(float(hi))) for lo,hi in zip(points.min(axis=0),points.max(axis=0)))


def contains_coordinates(box,points):
    if not len(points):return True
    return all(q.lo<=F.from_float(float(lo))<=F.from_float(float(hi))<=q.hi
        for q,lo,hi in zip(box,points.min(axis=0),points.max(axis=0)))


def worker(argument):
    study,index=argument;study=Path(study);m=read(study/'manifest.json');task=m['tasks'][index]
    parent=Path(m['parent']);source=parent/'inputs'/task['task_id'];checked(source/'receipt.json',m['parent_receipts'][f'inputs/{task["task_id"]}/receipt.json'])
    receipt=read(source/'receipt.json')
    for name,h in receipt.items():checked(source/name,h)
    folder=study/'inputs'/task['task_id'];folder.mkdir(parents=True,exist_ok=False)
    records=[];groups=0;refuted=False;interrupted=False
    for filename in sorted(n for n in receipt if n.endswith('.npz')):
        if (study/'STOP.json').exists():interrupted=True;break
        with np.load(source/filename,allow_pickle=False) as data:
            stages={name:data[name].copy() for name in {v[k] for v in m['stages'] for k in ['source','target']}}
        for transfer in m['stages']:
            before=stages[transfer['source']];after=stages[transfer['target']]
            input_box=coordinate_box(before);enclosure=round_buffer_source_box(input_box,transfer['distance'])
            universal=contains_coordinates(enclosure.output_box,after);raw=contains_coordinates(enclosure.raw_box,after)
            record={'group_file':filename,'stage':transfer['name'],'source_stage':transfer['source'],'target_stage':transfer['target'],
                'distance':transfer['distance'],'source_coordinate_count':len(before),'target_coordinate_count':len(after),
                'enclosure':enclosure.serial(),'universal_conditional_box_contains_saved_output':universal,
                'raw_floating_box_contains_saved_output_diagnostic_only':raw}
            records.append(record)
            if not universal:
                write_json(folder/'transfer_counterexample.json',record)
                np.savez_compressed(folder/'counterexample_coordinates.npz',before=before,after=after)
                atomic_json(study/'STOP.json',{'reason':'conditional_round_buffer_source_transfer_refuted','input':task['task_id'],'scientific_retry':False})
                refuted=True;break
        groups+=1
        if refuted:break
    write_json(folder/'records.json',records)
    result={'task_id':task['task_id'],'groups_checked':groups,'stage_transfers_checked':len(records),
        'saved_output_coordinates_checked':sum(r['target_coordinate_count'] for r in records),
        'raw_floating_diagnostic_counterexamples':sum(not r['raw_floating_box_contains_saved_output_diagnostic_only'] for r in records),
        'hypothesis_refuted':refuted,'interrupted_after_peer_refutation':interrupted}
    write_json(folder/'result.json',result)
    names=['records.json','result.json']+(['transfer_counterexample.json','counterexample_coordinates.npz'] if refuted else [])
    write_json(folder/'receipt.json',{name:digest(folder/name) for name in names})
    return result


def verify_enclosure(saved,distance):
    """Check independent algebra, direct source expressions, and source hull."""
    u=F(1,2**53);eta=F(1,2**1075);d=F.from_float(abs(distance));cap=F(saved['normal_displacement_limit'])
    tobox=lambda values:tuple(tuple(map(F,q)) for q in values)
    base=tobox(saved['input_box'])
    if d:
        root=(cap-(1+u)*eta/F(1,2**537)-eta)*(1-u)/((1+u)**2*d);target=F(3,2)/(1-u);unit=F(1,2**160)
        if root<0 or root*root<target or (root-unit)**2>=target or (root/unit).denominator!=1:raise ValueError('Finite normal cap root differs')
    elif cap!=0:raise ValueError('Zero-distance cap differs')
    normal=tuple((F.from_float(float(lo-cap)),F.from_float(float(hi+cap))) for lo,hi in base)
    arcs=tuple((F.from_float(float(lo-d)),F.from_float(float(hi+d))) for lo,hi in base)
    if normal!=tobox(saved['offset_box']) or arcs!=tobox(saved['arc_box']):raise ValueError('Source normal/arc extrema differ')
    closings=[]
    for k in (1,80):
        boxes=[]
        for offset,center in zip(normal,base):
            def evaluate(o,c):
                separate=(k*float(o)+float(c))/(k+1)
                fused=float(k*o+c)/(k+1)
                return [F.from_float(separate),F.from_float(fused)]
            boxes.append((min(evaluate(offset[0],center[0])),max(evaluate(offset[1],center[1]))))
        if tuple(boxes)!=tobox(saved['closing_boxes'][str(k)]):raise ValueError('Closing numerator association range differs')
        closings.append(tuple(boxes))
    raw=tuple((min(b[j][0] for b in [base,normal,arcs,*closings]),max(b[j][1] for b in [base,normal,arcs,*closings])) for j in (0,1))
    if raw!=tobox(saved['raw_source_box']):raise ValueError('Raw source coordinate-producer hull differs')
    C=2*(1+u)**2;whole=tuple((C*C*lo-(C+1)*eta if lo<0 else F(0),C*C*hi+(C+1)*eta if hi>0 else F(0)) for lo,hi in raw)
    if whole!=tobox(saved['unknown_precision_output_box']):raise ValueError('Unknown precision composition differs')
    if saved['all_inputs_defined'] or saved['service_certificate']:raise ValueError('Conditional source scope lost')


def audit(study,output):
    study,output=Path(study),Path(output);output.mkdir(parents=True,exist_ok=False);m=read(study/'manifest.json');parent=Path(m['parent'])
    totals={'inputs_checked':0,'groups_checked':0,'stage_transfers_checked':0,'saved_output_coordinates_checked':0,'source_formulas_checked':0}
    for result in read(study/'results.json'):
        folder=study/'inputs'/result['task_id'];original=parent/'inputs'/result['task_id']
        checked(original/'receipt.json',m['parent_receipts'][f'inputs/{result["task_id"]}/receipt.json'])
        for name,h in read(original/'receipt.json').items():checked(original/name,h)
        for name,h in read(folder/'receipt.json').items():checked(folder/name,h)
        records=read(folder/'records.json');seen=set()
        expected={(n,s['name']) for n in read(original/'receipt.json') if n.endswith('.npz') for s in m['stages']}
        for row in records:
            key=row['group_file'],row['stage']
            if key not in expected or key in seen:raise ValueError('Duplicate/unknown group-stage mapping')
            seen.add(key);transfer=next(t for t in m['stages'] if t['name']==row['stage'])
            if row['distance']!=transfer['distance'] or row['source_stage']!=transfer['source'] or row['target_stage']!=transfer['target']:
                raise ValueError('Stage transfer metadata changed')
            with np.load(original/row['group_file'],allow_pickle=False) as data:
                before=data[transfer['source']];after=data[transfer['target']]
                expected_box=[[str(F.from_float(float(lo))),str(F.from_float(float(hi)))] for lo,hi in zip(before.min(axis=0),before.max(axis=0))]
                if row['enclosure']['input_box']!=expected_box:raise ValueError('Source range not bound to actual saved coordinates')
                verify_enclosure(row['enclosure'],row['distance']);totals['source_formulas_checked']+=1
                for name in ['unknown_precision_output_box','raw_source_box']:
                    box=tuple(Interval(F(q[0]),F(q[1])) for q in row['enclosure'][name]);inside=contains_coordinates(box,after)
                    key='universal_conditional_box_contains_saved_output' if name=='unknown_precision_output_box' else 'raw_floating_box_contains_saved_output_diagnostic_only'
                    if inside!=row[key]:raise ValueError('Direct exact range membership differs')
                if len(before)!=row['source_coordinate_count'] or len(after)!=row['target_coordinate_count']:raise ValueError('Stage vertex denominator differs')
                totals['saved_output_coordinates_checked']+=len(after)
            totals['stage_transfers_checked']+=1
        if not result['hypothesis_refuted'] and not result['interrupted_after_peer_refutation'] and seen!=expected:
            raise ValueError('Missing planned source-group-stage records')
        totals['inputs_checked']+=1;totals['groups_checked']+=result['groups_checked']
    probes=read(study/'weighted_identity_probes.json')
    if len(probes)!=len(m['design']['weighted_identity_probe_values']):raise ValueError('Closing diagnostic denominator changed')
    for x,row in zip(m['design']['weighted_identity_probe_values'],probes):
        value=(80*float(x)+float(x))/81
        if row['input_hex']!=float(x).hex() or row['separate_output_hex']!=value.hex() or row['outside_input_singleton']!=(value!=x):
            raise ValueError('Closing arithmetic singleton diagnostic differs')
        if F(row['exact_difference_fraction'])!=F.from_float(value)-F.from_float(float(x)):
            raise ValueError('Closing diagnostic exact displacement differs')
    totals['closing_arithmetic_diagnostics_checked']=len(probes)
    write_json(output/'audit.json',totals|{'study_manifest_sha256':digest(study/'manifest.json'),'all_match':True,
        'new_native_geometry_calls':0,'new_independent_samples':0,'new_motion_executions':0,
        'runtime_service_gate_changed':False,'all_inputs_defined':False,'whole_geometry_pipeline_proved':False,
        'scope':'direct algebra/source-expression and immutable stage-membership checks; no kernel regeneration or internal branch coverage'})
