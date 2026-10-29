"""Frozen old-world containment and independent rational propagation audit."""
from fractions import Fraction as F
from pathlib import Path

import numpy as np

from .convex_transfer_audit import read,checked
from udc.interfaces import Observation
from udc.multigroup.foundation_contract import ObservationFrame
from udc.multigroup.observation import BoundedMeasurement
from udc.multigroup.static_person_enclosure import static_person_boxes
from udc.experiments.static_multigroup import digest,write_json


def load_case(study,index):
    m=read(study/'manifest.json');parent=Path(m['parent']);old=read(parent/'manifest.json')
    checked(parent/'manifest.json',m['parent_manifest_sha256'])
    original=Path(old['original']);checked(original/'manifest.json',old['original_manifest_sha256'])
    original_manifest=read(original/'manifest.json');selected=old['selected_pairs'][index]
    sequence=next(s for s in original_manifest['sequences'] if s['sequence_id']==selected['sequence_id'])
    folder=parent/'pairs'/f'{index:03d}'
    for relative,checksum in m['parent_receipts'].items():
        if relative.startswith(f'pairs/{index:03d}/'):checked(parent/relative,checksum)
    for name,checksum in read(folder/'receipt.json').items():checked(folder/name,checksum)
    with np.load(folder/'shared.npz',allow_pickle=False) as data:observations=data['observations'].copy()
    epsilon=original_manifest['spec']['declared_error_bound_m'];frames=[]
    for i,saved in enumerate(sequence['frames']):
        checked(original/saved['observation'],saved['observation_sha256'])
        with np.load(original/saved['observation'],allow_pickle=False) as data:
            if not np.array_equal(data['positions'],observations[i]):raise ValueError('Historical observation changed')
            obs=Observation(data['positions'].copy(),data['radii'].copy(),data['demand'].copy())
        # Stable row IDs are the frozen legacy protocol's row identity. These
        # frame times record order, not measured physical acquisition times.
        frames.append(ObservationFrame(f'legacy_{i:02d}',float(i),tuple(range(len(obs.positions))),
            BoundedMeasurement(obs,epsilon,'frozen_legacy_static_row_identity')))
    return m,folder,frames,original_manifest['base_spec']['room_m']


def verify_worlds(folder,frames,room,boxes):
    counts={'worlds_checked':0,'person_world_memberships':0,'witness_worlds_preserved':0}
    candidates=read(folder/'candidates.json');witnesses={r['name'] for r in candidates if r['witness']}
    expected=['baseline']+[r['name'] for r in candidates]
    if sorted(p.name for p in folder.iterdir() if p.is_dir())!=sorted(expected):
        raise ValueError('Historical world denominator changed')
    radii=frames[0].measurement.observation.radii
    for name in expected:
        world=folder/name
        for artifact,checksum in read(world/'receipt.json').items():checked(world/artifact,checksum)
        with np.load(world/'truth.npz',allow_pickle=False) as data:
            positions=data['positions'].copy()
            if (not np.array_equal(data['radii'],radii) or
                    not np.array_equal(data['demand'],frames[0].measurement.observation.demand)):
                raise ValueError('Old world attributes differ from exact observed metadata')
        if len(positions)!=len(boxes):raise ValueError('Person denominator changed')
        for row,point in enumerate(positions):
            point=tuple(F.from_float(float(v)) for v in point)
            if any(not q.contains(v) for q,v in zip(boxes[row],point)):
                return counts,{'world':name,'person_row':row,'point':[str(v) for v in point],
                    'box':[q.serial() for q in boxes[row]]}
            radius=F.from_float(float(radii[row]))
            if any(not radius<=v<=F.from_float(float(d))-radius for v,d in zip(point,room)):
                raise ValueError('Saved world violates exact body/room constraints')
            for frame in frames:
                center=frame.measurement.observation.positions[row]
                error=F.from_float(frame.measurement.position_error_bound_m)
                if sum((v-F.from_float(float(c)))**2 for v,c in zip(point,center))>error**2:
                    raise ValueError('Saved world violates exact observation disk')
            counts['person_world_memberships']+=1
        counts['worlds_checked']+=1;counts['witness_worlds_preserved']+=name in witnesses
    return counts,None


def worker(argument):
    study,index=argument;study=Path(study);m,parent,frames,room=load_case(study,index)
    folder=study/'cases'/f'{index:03d}';folder.mkdir(parents=True,exist_ok=False)
    options={'max_sweeps':m['design']['max_sweeps'],'use_pairwise_lenses':m['design'].get('use_pairwise_lenses',False)}
    first=static_person_boxes(frames[:1],room,**options)
    full=static_person_boxes(frames,room,**options)
    write_json(folder/'first_frame.json',first.serial());write_json(folder/'history.json',full.serial())
    if first.witness or full.witness:
        raise ValueError('MODEL_BOUND_VIOLATION: previously feasible history reported inconsistent')
    counts,failure=verify_worlds(parent,frames,room,full.boxes)
    if failure:
        write_json(folder/'failed_world.json',failure)
        raise ValueError('MODEL_BOUND_VIOLATION: saved feasible person escaped observable box')
    first_width=sum(q.hi-q.lo for box in first.boxes for q in box)
    full_width=sum(q.hi-q.lo for box in full.boxes for q in box)
    if any(not a.lo<=b.lo<=b.hi<=a.hi for x,y in zip(first.boxes,full.boxes) for a,b in zip(x,y)):
        # Different finite sweep budgets can change propagation convergence.
        # This comparison is diagnostic only, and not the all-world proof.
        comparison='not_nested_at_finite_sweep_budget'
    else:comparison='nested'
    result={'index':index,'people':len(full.person_ids),'frames':len(frames),**counts,
        'first_frame_sum_coordinate_widths_m':str(first_width),'history_sum_coordinate_widths_m':str(full_width),
        'relative_width_reduction':float((first_width-full_width)/first_width) if first_width else 0.,
        'strictly_tighter_person_boxes':sum(any(b.lo>a.lo or b.hi<a.hi for a,b in zip(x,y)) for x,y in zip(first.boxes,full.boxes)),
        'finite_budget_nesting':comparison,'propagation_trace_steps':sum(r['kind']!='initial' for r in full.trace),
        'disk_pair_tightening_steps':sum(r['kind']=='disk_pair_tightening' for r in full.trace),
        'single_disk_tightening_steps':sum(r['kind']=='disk_coordinate_tightening' for r in full.trace),
        'runtime_service_gate_changed':False,'new_independent_samples':0,'new_motion_executions':0}
    write_json(folder/'result.json',result)
    write_json(folder/'receipt.json',{n:digest(folder/n) for n in ['first_frame.json','history.json','result.json']})
    return result


def replay_pair_box(row,frame_map,identity):
    """Verify pair extrema through an exact discriminant and squared root bound."""
    data=[]
    for name in row['frame_ids']:
        frame=frame_map[name];i=frame.person_ids.index(identity)
        data.append((tuple(F.from_float(float(v)) for v in frame.measurement.observation.positions[i]),
            F.from_float(frame.measurement.position_error_bound_m)))
    (a,r),(b,s)=data;dx,dy=b[0]-a[0],b[1]-a[1];d2=dx*dx+dy*dy;evidence=row['pair_evidence']
    if F(evidence['distance_squared'])!=d2 or F(evidence['radii_sum_squared'])!=(r+s)**2 or d2>(r+s)**2:
        raise ValueError('Pair distance or consistency differs')
    if d2<=(r-s)**2:
        if evidence['kind']!='contained_or_coincident_disks':raise ValueError('Nested disk branch differs')
        center,radius=(a,r) if r<=s else (b,s)
        expected=tuple((c-radius,c+radius) for c in center)
    else:
        if evidence['kind']!='intersecting_circles':raise ValueError('Circle intersection branch differs')
        numerator=r*r-s*s+d2;discriminant=4*r*r*d2-numerator*numerator
        root_squared=discriminant/(4*d2*d2);root=F(evidence['root_upper']);unit=F(1,2**160)
        if (discriminant<0 or F(evidence['along'])!=numerator/(2*d2)
                or F(evidence['radicand'])!=root_squared or root<0 or root*root<root_squared
                or (root and (root-unit)**2>=root_squared) or (root/unit).denominator!=1):
            raise ValueError('Pair intersection discriminant or rational sqrt bound differs')
        expected=[]
        for j,perpendicular in [(0,-dy),(1,dx)]:
            axis_values=[]
            for center,radius,other,other_radius in [(a,r,b,s),(b,s,a,r)]:
                for sign in (-1,1):
                    candidate=list(center);candidate[j]+=sign*radius
                    if (candidate[0]-other[0])**2+(candidate[1]-other[1])**2<=other_radius**2:
                        axis_values.append(candidate[j])
            midpoint=a[j]+numerator*(b[j]-a[j])/(2*d2)
            expected.append((min([midpoint-abs(perpendicular)*root]+axis_values),
                max([midpoint+abs(perpendicular)*root]+axis_values)))
        expected=tuple(expected)
    if expected!=tuple(tuple(map(F,q)) for q in row['pair_box']):raise ValueError('Saved disk-pair box differs')
    return expected


def replay_trace(saved,frames,room):
    """Check each necessary contraction directly; never call the core iterator."""
    if saved['inconsistency_witness'] is not None:raise ValueError('Unexpected historical inconsistency')
    ids=tuple(saved['person_ids']);current={};frame_map={f.frame_id:f for f in frames};updates=0
    def values(serial):return tuple(tuple(F(v) for v in q) for q in serial)
    for row in saved['trace']:
        identity=row['person_id']
        if identity not in ids:raise ValueError('Trace identity changed')
        if row['kind']=='initial':
            if identity in current:raise ValueError('Repeated initial trace')
            candidates=[];radius=None
            for frame in frames:
                i=frame.person_ids.index(identity);obs=frame.measurement.observation
                radius=F.from_float(float(obs.radii[i]));e=F.from_float(frame.measurement.position_error_bound_m)
                candidates.append((tuple(F.from_float(float(x)) for x in obs.positions[i]),e))
            expected=tuple((max([radius]+[p[j]-e for p,e in candidates]),
                min([F.from_float(float(room[j]))-radius]+[p[j]+e for p,e in candidates])) for j in (0,1))
            if values(row['box'])!=expected:raise ValueError('Initial intersection formula differs')
            current[identity]=expected;continue
        before=values(row['before'])
        if before!=current[identity]:raise ValueError('Propagation trace is discontinuous')
        if row['kind']=='disk_pair_tightening':
            pair=replay_pair_box(row,frame_map,identity)
            expected=tuple((max(a[0],b[0]),min(a[1],b[1])) for a,b in zip(before,pair))
            if values(row['after'])!=expected or any(a>b for a,b in expected):raise ValueError('Pair contraction differs')
            current[identity]=expected;updates+=1;continue
        if row['kind']!='disk_coordinate_tightening':raise ValueError('Unknown trace operation')
        frame=frame_map[row['frame_id']];i=frame.person_ids.index(identity)
        center=tuple(F.from_float(float(v)) for v in frame.measurement.observation.positions[i])
        error=F.from_float(frame.measurement.position_error_bound_m)
        if center!=tuple(map(F,row['center'])) or error!=F(row['error']):raise ValueError('Trace observation changed')
        axis=row['axis'];a,b=before[1-axis]
        minimum_distance=max(a-center[1-axis],center[1-axis]-b,F(0))
        t=error*error-minimum_distance*minimum_distance;upper=F(row['root_upper'])
        # Independently verify the rational sqrt bracket by squaring. This
        # does not reuse the core isqrt construction or an approximate sqrt.
        unit=F(1,2**160)
        if (t<0 or t!=F(row['radicand']) or upper<0 or upper*upper<t
                or (upper and (upper-unit)**2>=t) or (upper/unit).denominator!=1):
            raise ValueError('Rational square-root enclosure differs')
        after=(max(before[axis][0],center[axis]-upper),min(before[axis][1],center[axis]+upper))
        if tuple(map(F,row['after']))!=after or after[0]>after[1]:raise ValueError('Coordinate contraction differs')
        new=list(before);new[axis]=after;current[identity]=tuple(new);updates+=1
    if len(current)!=len(ids) or [current[i] for i in ids]!=[values(b) for b in saved['boxes']]:
        raise ValueError('Saved final boxes differ from trace')
    return updates


def audit(study,output):
    study,output=Path(study),Path(output);output.mkdir(parents=True,exist_ok=False)
    m=read(study/'manifest.json');totals={'pairs_replayed':0,'worlds_checked':0,'person_world_memberships':0,
        'witness_worlds_preserved':0,'necessary_contractions_checked':0}
    for index in range(m['scheduled_pairs']):
        _,parent,frames,room=load_case(study,index);folder=study/'cases'/f'{index:03d}'
        for name,checksum in read(folder/'receipt.json').items():checked(folder/name,checksum)
        full=read(folder/'history.json');first=read(folder/'first_frame.json')
        totals['necessary_contractions_checked']+=replay_trace(full,frames,room)+replay_trace(first,frames[:1],room)
        from udc.multigroup.offset_interval import Interval
        boxes=tuple(tuple(Interval(F(q[0]),F(q[1])) for q in box) for box in full['boxes'])
        counts,failure=verify_worlds(parent,frames,room,boxes)
        if failure:
            write_json(output/'failed_world.json',failure);raise ValueError('Independent world membership differs')
        for key,value in counts.items():totals[key]+=value
        totals['pairs_replayed']+=1
    write_json(output/'audit.json',totals|{'study_manifest_sha256':digest(study/'manifest.json'),'all_match':True,
        'new_independent_samples':0,'new_motion_executions':0,'service_certificate':False,
        'scope':'direct rational trace implications and exact old-world membership; not exhaustive feasible-world search or reference-curve proof'})
