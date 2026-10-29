"""Native initial-circle probes and source-projection arithmetic audit."""
from fractions import Fraction as F
from pathlib import Path
from unittest.mock import patch

import numpy as np
import shapely

from .convex_transfer_audit import read,checked
from .static_person_enclosure_audit import load_case
from udc.multigroup import geometry
from udc.multigroup.circle_source_enclosure import circular_source_box
from udc.multigroup.offset_interval import Interval
from udc.experiments.static_multigroup import write_json,digest
from udc.experiments.frozen_resume import atomic_json


def capture_distance(radius,quad_segs):
    """Run the actual Python radius expression; intercept before native buffer.

    No alternate secant approximation or copied construction formula is used.
    The patch is process-local, ends before any native geometry evaluation,
    and never changes the controller or a saved historical source.
    """
    captured=[];marker=object()
    class CapturePoint:
        def __init__(self,center):pass
        def buffer(self,distance,**options):
            captured.append((float(distance),options));return marker
    with patch.object(geometry,'Point',CapturePoint):
        result=geometry.outer_disk([0.,0.],radius,quad_segs=quad_segs)
    if result is not marker or len(captured)!=1 or captured[0][1]!={'quad_segs':quad_segs}:
        raise ValueError('Actual initial-circle construction call shape changed')
    return captured[0][0]


def source_inputs(study,index):
    m,parent,frames,room=load_case(study,index);people=Path(m['person_study']);case=people/'cases'/f'{index:03d}'
    checked(case/'receipt.json',m['person_receipts'][f'cases/{index:03d}/receipt.json'])
    for name,checksum in read(case/'receipt.json').items():checked(case/name,checksum)
    history=read(case/'history.json')
    if history['inconsistency_witness'] is not None:raise ValueError('Inconsistent person box parent')
    original=Path(read(Path(m['parent'])/'manifest.json')['original']);config=read(original/'manifest.json')['base_spec']['model']
    boxes=[tuple(Interval(F(q[0]),F(q[1])) for q in box) for box in history['boxes']]
    radii=[float(r+config['construction_margin_m']) for r in frames[0].measurement.observation.radii]
    distances={r.hex():capture_distance(r,m['design']['quad_segs']) for r in set(radii)}
    output=[circular_source_box(box,distances[r.hex()]) for box,r in zip(boxes,radii)]
    return m,parent,frames,boxes,radii,distances,output


def worker(argument):
    study,index=argument;study=Path(study);m,parent,frames,boxes,radii,distances,enclosures=source_inputs(study,index)
    folder=study/'cases'/f'{index:03d}';folder.mkdir(parents=True,exist_ok=False)
    write_json(folder/'enclosures.json',{'person_ids':list(frames[0].person_ids),
        'center_boxes':[[q.serial() for q in b] for b in boxes],'radius_arguments':radii,
        'captured_native_distances':distances,'source_boxes':[[q.serial() for q in b] for b in enclosures]})
    bounds=np.array([[float(b[0].lo),float(b[1].lo),float(b[0].hi),float(b[1].hi)] for b in enclosures])
    cache={};inputs=[];rings=[];worlds=[];members=0;refuted=False;interrupted=False;witnesses=0
    candidates=read(parent/'candidates.json');names=['baseline']+[r['name'] for r in candidates]
    positive={r['name'] for r in candidates if r['witness']}
    for name in names:
        if (study/'STOP.json').exists():interrupted=True;break
        world=parent/name;checked(world/'truth.npz',read(world/'receipt.json')['truth.npz'])
        with np.load(world/'truth.npz',allow_pickle=False) as data:positions=data['positions'].copy()
        if len(positions)!=len(boxes):raise ValueError('World person denominator changed')
        mapping=[]
        for row,(center,radius,center_box,bound) in enumerate(zip(positions,radii,boxes,bounds)):
            if any(not q.contains(F.from_float(float(v))) for q,v in zip(center_box,center)):
                write_json(folder/'failed_world.json',{'world':name,'row':row,'center':center.tolist()})
                raise ValueError('MODEL_BOUND_VIOLATION: previously audited person box membership changed')
            key=(float(center[0]).hex(),float(center[1]).hex(),radius.hex())
            if key not in cache:
                value=geometry.outer_disk(center,radius,quad_segs=m['design']['quad_segs'])
                coordinates=shapely.get_coordinates(value)
                if value.geom_type!='Polygon' or value.is_empty or not value.is_valid:
                    write_json(folder/'failed_geometry.json',{'world':name,'row':row,'wkb_hex':value.wkb_hex,
                        'reason':'invalid_or_empty_initial_point_buffer'})
                    raise ValueError('MODEL_BOUND_VIOLATION: admitted initial point buffer invalid')
                cache[key]=len(inputs);inputs.append([float(center[0]),float(center[1]),radius]);rings.append(coordinates)
            number=cache[key];coordinates=rings[number]
            # Bounds and coordinates are binary64. Reductions select existing
            # coordinates; comparisons are exact ordering, with no tolerance.
            minimum=coordinates.min(axis=0);maximum=coordinates.max(axis=0)
            if not (np.all(minimum>=bound[:2]) and np.all(maximum<=bound[2:])):
                failure={'index':index,'world':name,'person_row':row,'input':inputs[number],
                    'source_box':[q.serial() for q in enclosures[row]],'native_coordinates':coordinates.tolist(),
                    'scope':'whole initial Point.buffer transfer hypothesis, not the conditional raw projection theorem'}
                write_json(folder/'transfer_counterexample.json',failure)
                atomic_json(study/'STOP.json',{'reason':'initial_point_buffer_source_transfer_refuted','index':index,
                    'artifact':str(folder/'transfer_counterexample.json'),'scientific_retry':False})
                refuted=True;break
            members+=1;mapping.append(number)
        if refuted:
            write_json(folder/'partial_world.json',{'name':name,'native_input_indices':mapping})
            break
        worlds.append({'name':name,'native_input_indices':mapping});witnesses+=name in positive
    sizes=np.array([len(r) for r in rings],dtype=np.int64);starts=np.r_[0,np.cumsum(sizes)]
    np.savez_compressed(folder/'native_circles.npz',inputs=np.asarray(inputs,float).reshape(-1,3),
        coordinates=np.concatenate(rings) if rings else np.empty((0,2)),starts=starts)
    write_json(folder/'world_mappings.json',worlds)
    result={'index':index,'worlds_checked':len(worlds),'person_world_memberships':members,'native_circle_calls':len(inputs),
        'stored_circle_coordinate_pairs':int(sizes.sum()),'witness_worlds_preserved':witnesses,
        'hypothesis_refuted':refuted,'interrupted_after_peer_refutation':interrupted,
        'captured_radius_expressions':len(distances),'whole_buffer_transfer_proved':False,
        'new_independent_samples':0,'new_motion_executions':0,'runtime_service_gate_changed':False}
    write_json(folder/'result.json',result)
    names=['enclosures.json','native_circles.npz','world_mappings.json','result.json']
    if refuted:names.extend(['transfer_counterexample.json','partial_world.json'])
    write_json(folder/'receipt.json',{name:digest(folder/name) for name in names})
    return result


def audit(study,output):
    """Native bit replay and direct scalar endpoint proof checks, not core rerun."""
    study,output=Path(study),Path(output);output.mkdir(parents=True,exist_ok=False);m=read(study/'manifest.json')
    totals={'cases_replayed':0,'worlds_checked':0,'person_world_memberships':0,'actual_native_circle_calls_replayed':0,
        'coordinate_pairs_bit_replayed':0,'direct_source_coordinate_ranges_checked':0,'witness_worlds_preserved':0,
        'transfer_counterexamples_replayed':0}
    for result in read(study/'results.json'):
        index=result['index'];folder=study/'cases'/f'{index:03d}'
        for name,checksum in read(folder/'receipt.json').items():checked(folder/name,checksum)
        # Read original observations/boxes without calling the core source-box
        # builder. capture_distance executes only the active Python expression.
        _,parent,frames,room=load_case(study,index);saved=read(folder/'enclosures.json')
        person=Path(m['person_study'])/'cases'/f'{index:03d}'
        for name,checksum in read(person/'receipt.json').items():checked(person/name,checksum)
        if saved['center_boxes']!=read(person/'history.json')['boxes']:raise ValueError('Center boxes changed')
        original=Path(read(Path(m['parent'])/'manifest.json')['original']);margin=read(original/'manifest.json')['base_spec']['model']['construction_margin_m']
        expected_radii=[float(r+margin) for r in frames[0].measurement.observation.radii]
        if expected_radii!=saved['radius_arguments']:raise ValueError('Observable radius input changed')
        for key,value in saved['captured_native_distances'].items():
            if capture_distance(float.fromhex(key),m['design']['quad_segs']).hex()!=float(value).hex():
                raise ValueError('Actual radius expression changed')
        for box,source,radius in zip(saved['center_boxes'],saved['source_boxes'],expected_radii):
            distance=F.from_float(saved['captured_native_distances'][radius.hex()])
            for endpoints,target in zip(box,source):
                lo,hi=map(F,endpoints)
                import math
                a,b=float(lo),float(hi)
                if F.from_float(a)<lo:a=math.nextafter(a,math.inf)
                if F.from_float(b)>hi:b=math.nextafter(b,-math.inf)
                expected=[F.from_float(float(F.from_float(a)-distance)),F.from_float(float(F.from_float(b)+distance))]
                if expected!=list(map(F,target)):raise ValueError('Direct source endpoint formula differs')
                totals['direct_source_coordinate_ranges_checked']+=1
        with np.load(folder/'native_circles.npz',allow_pickle=False) as data:
            inputs=data['inputs'].copy();coordinates=data['coordinates'].copy();starts=data['starts'].copy()
        if len(inputs)!=result['native_circle_calls'] or len(starts)!=len(inputs)+1:raise ValueError('Native input count changed')
        for i,(x,y,radius) in enumerate(inputs):
            actual=shapely.get_coordinates(geometry.outer_disk([x,y],radius,quad_segs=m['design']['quad_segs']))
            expected=coordinates[starts[i]:starts[i+1]]
            if actual.shape!=expected.shape or not np.array_equal(actual.view(np.uint64),expected.view(np.uint64)):
                np.savez_compressed(output/'native_replay_mismatch.npz',input=[x,y,radius],actual=actual,expected=expected)
                raise ValueError('Native initial-circle coordinate bit replay differs')
            totals['actual_native_circle_calls_replayed']+=1;totals['coordinate_pairs_bit_replayed']+=len(actual)
        candidates=read(parent/'candidates.json');positive={r['name'] for r in candidates if r['witness']}
        mappings=read(folder/'world_mappings.json')
        if not result['hypothesis_refuted'] and not result['interrupted_after_peer_refutation']:
            if [w['name'] for w in mappings]!=['baseline']+[r['name'] for r in candidates]:raise ValueError('World mapping denominator changed')
        if result['hypothesis_refuted']:
            mappings=mappings+[read(folder/'partial_world.json')]
            failure=read(folder/'transfer_counterexample.json')
            failed_input=np.asarray(failure['input'],float)
            matches=np.flatnonzero(np.all(inputs.view(np.uint64)==failed_input.view(np.uint64),axis=1))
            if len(matches)!=1:raise ValueError('Counterexample native input mapping differs')
            i=int(matches[0]);ring=coordinates[starts[i]:starts[i+1]]
            if not np.array_equal(ring.view(np.uint64),np.asarray(failure['native_coordinates'],float).view(np.uint64)):
                raise ValueError('Counterexample coordinate artifact differs')
            if all(F(q[0])<=F.from_float(float(p[j]))<=F(q[1]) for p in ring for j,q in enumerate(failure['source_box'])):
                raise ValueError('Saved transfer refutation not reproduced')
            totals['transfer_counterexamples_replayed']+=1
        for world in mappings:
            path=parent/world['name'];checked(path/'truth.npz',read(path/'receipt.json')['truth.npz'])
            with np.load(path/'truth.npz',allow_pickle=False) as data:positions=data['positions'].copy()
            partial=result['hypothesis_refuted'] and world is mappings[-1]
            if len(world['native_input_indices'])!=len(positions) and not partial:raise ValueError('Person mapping denominator changed')
            for row,(point,number) in enumerate(zip(positions,world['native_input_indices'])):
                if not np.array_equal(inputs[number,:2].view(np.uint64),point.view(np.uint64)) or inputs[number,2]!=expected_radii[row]:
                    raise ValueError('Native input world mapping differs')
                ring=coordinates[starts[number]:starts[number+1]];minimum=ring.min(axis=0);maximum=ring.max(axis=0)
                for j,target in enumerate(saved['source_boxes'][row]):
                    lo,hi=map(F,target)
                    if not lo<=F.from_float(float(minimum[j]))<=F.from_float(float(maximum[j]))<=hi:
                        raise ValueError('Exact source-box extrema membership differs')
                totals['person_world_memberships']+=1
            if not partial:
                totals['worlds_checked']+=1;totals['witness_worlds_preserved']+=world['name'] in positive
        totals['cases_replayed']+=1
    write_json(output/'audit.json',totals|{'study_manifest_sha256':digest(study/'manifest.json'),'all_match':True,
        'whole_buffer_transfer_proved':False,'runtime_service_gate_changed':False,'new_independent_samples':0,'new_motion_executions':0,
        'scope':'bit replay of native Point.buffer calls plus direct conditional raw-source formula and rational extrema membership; not all BufferOp paths or service proof'})
