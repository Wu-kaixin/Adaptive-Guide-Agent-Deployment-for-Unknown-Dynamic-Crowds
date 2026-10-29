"""Observation-only rational outer boxes for static person locations.

All finite sweeps preserve every real point satisfying the declared error
disks and body/room constraints. A nonempty box does not prove a feasible
world exists, identify groups, or enclose a constructed reference curve.
"""
from dataclasses import dataclass
from fractions import Fraction as F
import math

from .foundation_contract import ObservationFrame,STATIC_CONTRACT
from .offset_interval import Interval,_sqrt_bracket


def _exact(value):
    return F.from_float(float(value))


def _distance_to_interval(value,interval):
    return max(interval.lo-value,value-interval.hi,F(0))


@dataclass(frozen=True)
class StaticPersonEnclosure:
    person_ids: tuple
    boxes: tuple
    witness: dict | None
    sweeps: tuple
    trace: tuple

    def serial(self):
        return {'status':'INCONSISTENT_STATIC_OBSERVATIONS' if self.witness else 'OUTER_ENCLOSURE_ONLY',
            'person_ids':list(self.person_ids),'boxes':[[q.serial() for q in box] for box in self.boxes],
            'inconsistency_witness':self.witness,'sweeps':list(self.sweeps),'trace':list(self.trace),
            'feasibility_proved':False,'group_identity_proved':False,
            'original_reference_enclosed':False,'service_certificate':False}


def static_person_boxes(frames,room,*,max_sweeps=8,use_pairwise_lenses=False):
    """Intersect bounded observation disks, preserving stable ID and metadata.

    Room is the known closed rectangle [0,width] x [0,height]. Exact observed
    body radii bound centers away from its walls. Pairwise body separation and
    layout/reference admissibility are deliberately not inferred from boxes.
    Rational outward square-root brackets, rather than floating tolerances,
    implement coordinate propagation. A finite budget affects tightness only.
    """
    frames=tuple(frames)
    if (not frames or any(not isinstance(frame,ObservationFrame) for frame in frames)
            or type(max_sweeps) is not int or max_sweeps<1 or type(use_pairwise_lenses) is not bool):
        raise ValueError('Nonempty versioned static frames and positive finite sweep budget required')
    if any(frame.contract!=STATIC_CONTRACT for frame in frames):
        raise ValueError('Static person enclosure requires the static Step 1 contract')
    if len(set(frame.frame_id for frame in frames))!=len(frames) or any(
            later.time_s<earlier.time_s for earlier,later in zip(frames,frames[1:])):
        raise ValueError('Distinct frames in nondecreasing observation time required')
    if len(room)!=2 or any(not math.isfinite(float(v)) or float(v)<=0 for v in room):
        raise ValueError('Positive finite known rectangular room required')
    dimensions=tuple(_exact(v) for v in room)
    ids=tuple(sorted(frames[0].person_ids));records={i:[] for i in ids};attributes={}
    for frame in frames:
        if set(frame.person_ids)!=set(ids):raise ValueError('Static person IDs changed')
        obs=frame.measurement.observation
        for row,identity in enumerate(frame.person_ids):
            metadata=(_exact(obs.radii[row]),_exact(obs.demand[row]))
            if identity in attributes and attributes[identity]!=metadata:
                raise ValueError('Exact static radius or planning demand changed')
            attributes[identity]=metadata
            records[identity].append((frame.frame_id,tuple(_exact(v) for v in obs.positions[row]),
                _exact(frame.measurement.position_error_bound_m)))
    boxes=[];trace=[];sweeps=[]
    def inconsistent(identity,reason,**values):
        return StaticPersonEnclosure(ids,(),{'person_id':identity,'reason':reason,**values},tuple(sweeps),tuple(trace))
    for identity in ids:
        radius=attributes[identity][0];limits=[]
        for axis,dimension in enumerate(dimensions):
            lower=max([radius]+[p[axis]-error for _,p,error in records[identity]])
            upper=min([dimension-radius]+[p[axis]+error for _,p,error in records[identity]])
            if lower>upper:
                return inconsistent(identity,'initial_axis_intersection_empty',axis=axis,lower=str(lower),upper=str(upper))
            limits.append(Interval(lower,upper))
        trace.append({'kind':'initial','person_id':identity,'box':[q.serial() for q in limits]})
        if use_pairwise_lenses:
            from .disk_pair_enclosure import disk_pair_box
            history=records[identity]
            for i,(frame_id,center,error) in enumerate(history):
                for other_id,other_center,other_error in history[:i]:
                    pair,evidence=disk_pair_box(center,error,other_center,other_error)
                    if pair is None:
                        return inconsistent(identity,'two_observation_disks_disjoint',frame_ids=[frame_id,other_id],
                            pair_evidence=evidence)
                    endpoints=[(max(a.lo,b.lo),min(a.hi,b.hi)) for a,b in zip(limits,pair)]
                    if any(lo>hi for lo,hi in endpoints):
                        return inconsistent(identity,'disk_pair_box_misses_current_box',frame_ids=[frame_id,other_id],
                            before=[q.serial() for q in limits],pair_box=[q.serial() for q in pair],pair_evidence=evidence)
                    changed=[Interval(lo,hi) for lo,hi in endpoints]
                    if changed!=limits:
                        trace.append({'kind':'disk_pair_tightening','person_id':identity,'frame_ids':[frame_id,other_id],
                            'before':[q.serial() for q in limits],'after':[q.serial() for q in changed],
                            'pair_box':[q.serial() for q in pair],'pair_evidence':evidence})
                        limits=changed
        for sweep in range(max_sweeps):
            previous=tuple(limits)
            for frame_id,center,error in records[identity]:
                for axis in (0,1):
                    other=1-axis;distance=_distance_to_interval(center[other],limits[other])
                    radicand=error*error-distance*distance
                    if radicand<0:
                        return inconsistent(identity,'disk_misses_axis_box',frame_id=frame_id,axis=axis,
                            other_interval=limits[other].serial(),other_center=str(center[other]),
                            error=str(error),radicand=str(radicand))
                    _,root_upper=_sqrt_bracket(radicand)
                    lower=max(limits[axis].lo,center[axis]-root_upper)
                    upper=min(limits[axis].hi,center[axis]+root_upper)
                    if lower>upper:
                        return inconsistent(identity,'propagated_axis_intersection_empty',frame_id=frame_id,
                            axis=axis,lower=str(lower),upper=str(upper),radicand=str(radicand),
                            root_upper=str(root_upper),before=[q.serial() for q in limits])
                    changed=Interval(lower,upper)
                    if changed!=limits[axis]:
                        trace.append({'kind':'disk_coordinate_tightening','person_id':identity,'sweep':sweep,
                            'frame_id':frame_id,'axis':axis,'before':[q.serial() for q in limits],
                            'center':[str(v) for v in center],'error':str(error),'radicand':str(radicand),
                            'root_upper':str(root_upper),'after':changed.serial()})
                        limits[axis]=changed
            if tuple(limits)==previous:break
        sweeps.append(sweep+1);boxes.append(tuple(limits))
    return StaticPersonEnclosure(ids,tuple(boxes),None,tuple(sweeps),tuple(trace))
