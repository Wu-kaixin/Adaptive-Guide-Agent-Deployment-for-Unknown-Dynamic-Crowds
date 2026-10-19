"""ABCG planning, reachable assignment, waypoint tracking and safe feedback."""
from time import perf_counter
import hashlib
import os

import numpy as np
from shapely.geometry import Point, Polygon

from udc.boundary import estimate_boundary
from udc.deployment import (demand_profile, feasibility_bounds, infeasibility_certificate,
                            plan_coverage, plan_risk_aware)
from udc.navigation import (navigate_and_assign, navigation_domain, route_assigned,
                            segment_in_free, segment_in_free_sampled)
from udc.navigation.paths import (NavigationFailure, ENDPOINT_TOLERANCE_M,
                                  target_domain_check, require_target_domain)
from udc.controller.progress import priority_path_stall
from udc.safety import safe_velocity, path_clearances, is_safe
from udc.safety.coordinate import joint_progress_velocity
from udc.safety.filter import binding_constraints


def _env_flag(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    if value not in {'0', '1'}:
        raise ValueError(f'{name} must be unset, 0, or 1')
    return value == '1'


class ABCGController:
    # follow_valid_step False restores proportional tracking and the five-point
    # predicate. It is the ablation switch, not a safety requirement.
    # complete_segment and vertex_arrival are the two repairs under test.
    # Near a planning boundary the vertex command is still eligible; the
    # connection check and the unchanged safety filter decide whether it runs.
    follow_valid_step=True
    complete_segment=True
    vertex_arrival=True

    def __init__(self, config, safety, room, protocol='adaptive_resource', fixed_n=None):
        self.config=config; self.safety=safety; self.room=np.asarray(room,float)
        self.boundary=self.plan=self.assignment=None
        self.attempts=[]; self.paths={}; self.waypoint={}; self.replans=0
        self.protocol=protocol; self.fixed_n=fixed_n
        self.priority_order=[]; self.priority_index=0; self.priority_mode=False
        self.follow_valid_step=type(self).follow_valid_step
        self.complete_segment=_env_flag('UDC_COMPLETE_SEGMENT', type(self).complete_segment)
        self.vertex_arrival=_env_flag('UDC_VERTEX_ARRIVAL', type(self).vertex_arrival)
        self.segment_audits=[]
        self.segment_disagreement_count=0
        self._audit_keys=set()
        self.vertex_arrivals=[]
        self._vertex_pending=set()
        self._control_step=0
        self._observation=None
        self.navigation_domain=None
        self.margin_connections=[]
        self.recovery_attempts=[]
        # Stage diagnostics. They never change a termination status.
        self.feasibility=None; self.plan_found=False; self.assignment_found=False
        self.boundary_source=None
        self.prescribed_plan_installed=False
        # Priority mode only. The law does not add a recovery or a second solver.
        self.coordination={
            'law':'along_current_legal_segment','activated':False,'order':[],
            'steps':0,'illegal_aim_steps':0,'fallback_steps':0,
            'enforce_attempts':0,'enforce_installed':0,'enforce_rejected':0,
            'max_other_filtered_speed_m_s':0.0,'filter_seconds':0.0,'enforce_seconds':0.0,
            'exit_step':None,'exit_reason':None,'exit_outside_tolerance':[],
            'progress_decisions':[],'progress_reference_changes':[],
        }
        self._priority_reference=None
        self._priority_samples=[]
        self._priority_token=None

    def _plan(self, observation, n, phase, planned_limit):
        c=self.config
        if c['method'] in {'ra_abdc', 'projected_mass'}:
            # projected_mass is the a=1 projected attempt with the schedule disabled.
            return plan_risk_aware(self.boundary,observation.positions,observation.demand,n,
                c['demand_bandwidth'],planned_limit,self.safety.guide_distance,phase,
                c['quadrature_samples'],c['planner_max_iterations'],
                c.get('concentration',1.0), adapt_concentration=c['method']=='ra_abdc',
                method=c['method'])
        return plan_coverage(self.boundary,observation.positions,observation.demand,n,
            c['demand_bandwidth'],c['method'],phase,c['quadrature_samples'],c['planner_max_iterations'])

    def _install_navigation(self, nav):
        self.assignment=nav.assignment
        self.paths={}; self.waypoint={}
        active=np.flatnonzero(nav.assignment>=0)
        for guide,path in zip(active,nav.paths):
            self.paths[int(guide)]=path
            self.waypoint[int(guide)]=1
        self.navigation=nav

    def prepare(self, observation, guides):
        c=self.config
        self._observation=observation
        self.navigation_domain=None
        self.boundary_source='estimated_convex_offset'
        self.prescribed_plan_installed=False
        self.boundary=estimate_boundary(observation.positions,c['offset'],c['sample_spacing'],self.room,
            self.safety.wall_distance,self.safety.crowd_distance,observation.radii)
        self._search(observation, guides)

    def prepare_prescribed(self, observation, guides, boundary, plan=None,
                           boundary_source='prescribed_diagnostic'):
        """Install a curve the caller already built.

        ``prepare`` still estimates a convex offset and does not call this
        method. ``prescribed_diagnostic`` is a given curve. ``estimated_disk_union``
        is an observation-only estimate; it is not the generator's spawn polygon.
        """
        if boundary_source not in {'prescribed_diagnostic', 'estimated_disk_union', 'estimated_convex_offset'}:
            raise ValueError(f'PLAN_INVALID: unknown boundary source {boundary_source}')
        if boundary is None:
            raise ValueError('PLAN_INVALID: a caller-supplied curve is required')
        self._observation=observation
        self.navigation_domain=None
        self.boundary=boundary
        self.boundary_source=boundary_source
        self.prescribed_plan_installed=plan is not None
        if plan is None:
            self._search(observation, guides)
            return
        self._install_supplied_plan(guides, plan)

    def _install_supplied_plan(self, guides, plan):
        c=self.config
        planned_limit=c['max_gap_limit']-2*c['arc_tolerance']
        if planned_limit <= 0:
            raise ValueError('PLAN_INVALID: max_gap_limit - 2*arc_tolerance must be positive')
        bounds=feasibility_bounds(self.boundary.length,planned_limit,self.safety.guide_distance)
        distances=np.linalg.norm(plan.positions[:,None]-plan.positions[None,:],axis=2)
        np.fill_diagonal(distances,np.inf)
        separation=float(distances.min())
        accepted=plan.max_gap <= planned_limit+1e-9 and separation >= self.safety.guide_distance
        certificate=infeasibility_certificate(bounds,len(plan.positions))
        self.feasibility={**bounds,'available_guides':len(guides),'requested_count':len(plan.positions),
            'necessary_condition_met':certificate is None,'infeasibility_certificate':certificate}
        self.attempts.append({'n':len(plan.positions),'phase':None,'max_gap':plan.max_gap,
            'min_separation':separation,'accepted':accepted,'supplied_plan':True,
            'boundary_source':self.boundary_source})
        if not accepted:
            raise ValueError('PLAN_SEARCH_EXHAUSTED: supplied diagnostic targets do not meet the arc and Euclidean checks')
        if len(plan.positions) > len(guides):
            raise ValueError(f'CAPACITY_SHORTFALL: supplied plan requires {len(plan.positions)}, have {len(guides)}')
        self.plan=plan
        self.plan_found=True
        admission = target_domain_check(self._domain(), plan.positions, plan.arc_positions)
        self.attempts[-1]['target_domain'] = admission
        self.attempts[-1]['accepted'] = accepted and admission['accepted']
        require_target_domain(self._domain(), plan.positions, plan.arc_positions)
        crowd=Polygon(self.boundary.crowd)
        started=perf_counter()
        nav=navigate_and_assign(guides,plan.positions,self.room,self.safety.wall_distance,
                                crowd,self.safety.wall_distance)
        self.initial_navigation_wall_seconds=perf_counter()-started
        self._install_navigation(nav)
        self.assignment_found=True

    def _search(self, observation, guides):
        c=self.config
        planned_limit=c['max_gap_limit']-2*c['arc_tolerance']
        if planned_limit <= 0: raise ValueError('PLAN_INVALID: max_gap_limit - 2*arc_tolerance must be positive')
        bounds=feasibility_bounds(self.boundary.length,planned_limit,self.safety.guide_distance)
        lower=bounds['necessary_min_count']
        start=max(3,int(np.ceil(self.boundary.length/c['budget_gap'])))
        self.feasibility={**bounds,'available_guides':len(guides),'requested_count':None,
            'necessary_condition_met':None,'infeasibility_certificate':None}
        if lower > len(guides):
            self.feasibility.update(requested_count=lower,necessary_condition_met=False,
                                    infeasibility_certificate='GAP_BOUND')
            raise ValueError(f'CAPACITY_SHORTFALL: necessary planning bound requires {lower}, have {len(guides)}')
        chosen=None
        # Search every N: the budget recommendation must not suppress a smaller feasible N.
        if self.protocol == 'fixed_n':
            if type(self.fixed_n) is not int or self.fixed_n < 3:
                raise ValueError('PLAN_INVALID: fixed_n protocol requires integer fixed_n >= 3')
            if self.fixed_n > len(guides):
                raise ValueError(f'CAPACITY_SHORTFALL: fixed_n requires {self.fixed_n}, have {len(guides)}')
            order=[self.fixed_n]
        elif self.protocol == 'adaptive_resource':
            order=list(range(lower,len(guides)+1)); order.sort(key=lambda n:(n<start,abs(n-start)))
        else:
            raise ValueError(f'PLAN_INVALID: unsupported protocol {self.protocol}')
        certificates={n:infeasibility_certificate(bounds,n) for n in order}
        self.feasibility.update(
            requested_count=order[0] if self.protocol == 'fixed_n' else [order[0],max(order)],
            necessary_condition_met=any(cert is None for cert in certificates.values()),
            infeasibility_certificate=(None if any(cert is None for cert in certificates.values())
                                       else certificates[order[0]]))
        for n in order:
            for phase in c['phases']:
                started=perf_counter()
                plan=self._plan(observation,n,phase,planned_limit)
                distances=np.linalg.norm(plan.positions[:,None]-plan.positions[None,:],axis=2)
                np.fill_diagonal(distances,np.inf); separation=float(distances.min())
                accepted=plan.max_gap <= planned_limit+1e-9 and separation >= self.safety.guide_distance
                admission = target_domain_check(self._domain(), plan.positions, plan.arc_positions)
                accepted = accepted and admission['accepted']
                rho=demand_profile(self.boundary,observation.positions,observation.demand,
                                   c['demand_bandwidth'],'mass_cvt')
                record={'n':n,'phase':phase,'max_gap':plan.max_gap,'min_separation':separation,
                    'cost':plan.cost.tolist(),'iterations':plan.iterations,'exit_reason':plan.exit_reason,
                    'accepted':accepted,'planner_seconds':perf_counter()-started,
                    'target_domain':admission,
                    # Same original density for every method. Concentration never replaces it.
                    'evaluation_density':'mass_cvt gaussian demand mass',
                    'evaluation_density_contrast':float(rho.max()/max(float(rho.min()),1e-30))}
                if plan.method in {'ra_abdc','projected_mass'}:
                    record.update(concentration=plan.concentration,equal_arc_fallback=plan.equal_arc_fallback,
                                  adapt_concentration=plan.adapt_concentration,
                                  adjustments=list(plan.adjustments))
                self.attempts.append(record)
                if accepted: chosen=plan; break
            if chosen is not None: break
        if chosen is None:
            certificate=self.feasibility['infeasibility_certificate']
            detail=(f'; necessary condition violated ({certificate}: N={order[0]}, '
                    f'ceil(L/H_p)={lower}, floor(L/d_min)={bounds["necessary_max_count"]})'
                    if certificate else '; necessary count conditions hold, so infeasibility is not proven')
            raise ValueError('PLAN_SEARCH_EXHAUSTED: bounded CVT/spacing search found no admissible target set'+detail)
        self.plan=chosen; self.plan_found=True
        crowd=Polygon(self.boundary.crowd)
        started=perf_counter()
        nav=navigate_and_assign(guides,chosen.positions,self.room,self.safety.wall_distance,
                                crowd,self.safety.wall_distance)
        self.initial_navigation_wall_seconds=perf_counter()-started
        self._install_navigation(nav)
        self.assignment_found=True

    def errors(self, positions):
        active=self.assignment>=0
        vectors=self.plan.positions[self.assignment[active]]-positions[active]
        return np.linalg.norm(vectors,axis=1)

    def guide_remaining_path(self, guide, position):
        """Remaining polyline distance of one guide. Waypoint changes are not motion."""
        guide=int(guide)
        path=self.paths[guide]; k=int(self.waypoint[guide])
        value=float(np.linalg.norm(path[k]-position))
        if k < len(path)-1:
            value += float(np.linalg.norm(np.diff(path[k:],axis=0),axis=1).sum())
        return value

    def remaining_path_lengths(self, positions):
        """Remaining polyline distance; unlike target distance it decreases on detours."""
        return np.asarray([
            self.guide_remaining_path(i, positions[i]) for i in np.flatnonzero(self.assignment>=0)
        ])

    def _domain(self):
        if self.navigation_domain is None:
            self.navigation_domain=navigation_domain(
                self.room,self.safety.wall_distance,Polygon(self.boundary.crowd),self.safety.wall_distance)
        return self.navigation_domain

    def _navigation_hashes(self):
        def digest(value):
            return hashlib.sha256(np.ascontiguousarray(value).tobytes()).hexdigest()
        paths=hashlib.sha256()
        for guide,path in sorted(self.paths.items()):
            paths.update(np.asarray([guide,*np.asarray(path).shape],dtype=np.int64).tobytes())
            paths.update(np.asarray(path,dtype=np.float64).tobytes())
        return {'targets_sha256':digest(np.asarray(self.plan.positions,dtype=np.float64)),
                'assignment_sha256':digest(np.asarray(self.assignment,dtype=np.int64)),
                'paths_sha256':paths.hexdigest()}

    def _recovery_event(self, positions, trigger, strategy):
        occupations=[]
        for guide in np.flatnonzero(self.assignment>=0):
            target=int(self.assignment[guide])
            distance=np.linalg.norm(positions-self.plan.positions[target],axis=1)
            occupants=[{'guide':int(i),'center_distance_m':float(d),'threshold_m':self.safety.guide_distance}
                       for i,d in enumerate(distance) if i!=guide and d<self.safety.guide_distance]
            if occupants:
                occupations.append({'guide':int(guide),'target':target,'occupants':occupants})
        return {'strategy':strategy,'trigger_reason':trigger,'positions':np.asarray(positions).tolist(),
                'targets':self.plan.positions.tolist(),'assignment':self.assignment.tolist(),
                'geometry_sha256':self._domain().geometry_sha256,
                'query_domain':('static_with_current_guides' if strategy=='path_recompute' else
                                'static' if strategy=='reachable_reassignment' else 'installed_paths'),
                'query_succeeded':False,'validation_succeeded':False,'installation_succeeded':False,
                'budget_before':self.replans,'budget_limit':self.config['max_replans'],
                'before':self._navigation_hashes(),'current_occupations':occupations,'release_guaranteed':False,
                'guide_center_threshold_m':self.safety.guide_distance,
                'crowd_center_threshold_m':self.safety.crowd_distance,
                'wall_threshold_m':self.safety.wall_distance,'numeric_tolerance_m':self.safety.numeric_tolerance}

    def _finish_recovery_event(self,event,started):
        event.update(wall_seconds=perf_counter()-started,budget_consumed=self.replans,
                     budget_remaining=max(0,self.config['max_replans']-self.replans),after=self._navigation_hashes())
        self.recovery_attempts.append(event)

    def recover(self, observation, positions, trigger_reason='explicit_recovery_request'):
        """Finite deterministic recovery: reroute, reassign, then priority yield."""
        if self.replans >= self.config['max_replans']:
            started=perf_counter()
            event=self._recovery_event(positions,trigger_reason,'none')
            event.update(accepted=False,classification='budget_exhausted',query_domain='not_queried')
            self._finish_recovery_event(event,started)
            return None
        current_clearances=path_clearances(positions,positions,observation.positions,self.room)
        if not is_safe(current_clearances,self.safety):
            started=perf_counter()
            event=self._recovery_event(positions,trigger_reason,'none')
            event.update(accepted=False,classification='unsafe_actual_state',query_domain='not_queried',
                         actual_clearances=current_clearances)
            self._finish_recovery_event(event,started)
            raise NavigationFailure('unsafe_actual_state','unsafe actual state; recovery not executed',
                                    query_domain='not_queried',actual_clearances=current_clearances)
        # A temporarily occupied assigned target may reject a fixed-identity
        # replan while a complete static-domain reassignment remains available.
        # Count the rejected attempt against the same budget. Install no partial
        # paths and never continue on the old paths merely because we caught it.
        first_error=None
        while self.replans < self.config['max_replans']:
            started=perf_counter()
            strategy=('path_recompute','reachable_reassignment','priority_yield')[min(self.replans,2)]
            event=self._recovery_event(positions,trigger_reason,strategy)
            self._recovery_phase=event
            self._recovery_path_validation=[]
            try:
                accepted=self._recover_strategy(observation, positions, strategy)
            except ValueError as error:
                if not str(error).startswith('PATH_UNREACHABLE'):
                    event.update(accepted=False,classification='program_error',error=str(error))
                    self._finish_recovery_event(event,started)
                    raise
                diagnosis=getattr(error,'diagnosis',{'kind':'search_not_found','infeasibility_proven':False})
                event.update(accepted=False,error=str(error),classification=diagnosis['kind'],diagnosis=diagnosis,
                             paths={str(i):path.tolist() for i,path in self.paths.items()},waypoints=dict(self.waypoint))
                self.replans += 1
                self._finish_recovery_event(event,started)
                if first_error is None:
                    first_error=error
                domain=self._domain()
                safe_state=(all(domain.physical_free.covers(Point(point)) for point in positions)
                            and is_safe(path_clearances(positions,positions,observation.positions,self.room),self.safety))
                if strategy == 'path_recompute' and safe_state and self.replans < self.config['max_replans']:
                    continue
                raise first_error from error
            except Exception as error:
                event.update(accepted=False,classification='program_error',error=str(error))
                self._finish_recovery_event(event,started)
                raise
            event.update(accepted=True,classification='verified_path_set_installed' if strategy!='priority_yield' else 'priority_yield',
                         assignment=self.assignment.tolist(),paths={str(i):path.tolist() for i,path in self.paths.items()},
                         path_validation=self._recovery_path_validation)
            self.replans += 1
            self._finish_recovery_event(event,started)
            return accepted
        if first_error is not None:
            raise first_error
        return None

    def _validate_recovery_paths(self, paths, assignment, positions, observation):
        validation_started=perf_counter()
        active=np.flatnonzero(np.asarray(assignment)>=0)
        ids=np.asarray(assignment)[active]
        if (set(paths)!=set(map(int,active)) or len(ids)!=len(self.plan.positions)
                or not np.array_equal(np.sort(ids),np.arange(len(self.plan.positions)))):
            raise NavigationFailure('incomplete_path_set','candidate path set or matching is incomplete')
        validation=[]
        for guide in active:
            path=np.asarray(paths[int(guide)])
            if (path.ndim!=2 or path.shape[1]!=2 or len(path)<2 or not np.isfinite(path).all()
                    or np.linalg.norm(path[0]-positions[guide])>ENDPOINT_TOLERANCE_M
                    or np.linalg.norm(path[-1]-self.plan.positions[int(assignment[guide])])>ENDPOINT_TOLERANCE_M):
                raise NavigationFailure('invalid_candidate_path','candidate path endpoints do not match actual state and assigned target',guide=int(guide))
            for a,b in zip(path[:-1],path[1:]):
                clearance=path_clearances(a[None,:],b[None,:],observation.positions,self.room)
                if not is_safe(clearance,self.safety):
                    raise NavigationFailure('unsafe_candidate_path','replacement path failed independent physical validation',
                                            guide=int(guide),clearances=clearance)
                validation.append({'guide':int(guide),'clearances':clearance})
        self._recovery_phase['validation_wall_seconds']=perf_counter()-validation_started
        return validation

    def _recover_strategy(self, observation, positions, strategy):
        """Build a complete replacement before changing the installed navigation."""
        crowd=Polygon(self.boundary.crowd)
        if strategy == 'path_recompute':
            domain=self._domain()
            outside=[int(guide) for guide in np.flatnonzero(self.assignment>=0)
                     if not domain.free.covers(Point(positions[guide]))]
            query_started=perf_counter()
            try:
                paths,_=route_assigned(positions,self.plan.positions,self.assignment,self.room,
                                       self.safety.wall_distance,crowd,self.safety.wall_distance,
                                       people=observation.positions,limits=self.safety)
            finally:
                self._recovery_phase['query_wall_seconds']=perf_counter()-query_started
            self._recovery_phase['query_succeeded']=True
            self._recovery_path_validation=self._validate_recovery_paths(paths,self.assignment,positions,observation)
            self._recovery_phase['validation_succeeded']=True
            self.paths=paths; self.waypoint={i:1 for i in paths}
            self._recovery_phase['installation_succeeded']=True
            if outside:
                self.margin_connections.append({
                    'guides':outside,
                    'replans_before':self.replans,
                    'geometry_sha256':domain.geometry_sha256,
                })
        elif strategy == 'reachable_reassignment':
            query_started=perf_counter()
            try:
                nav=navigate_and_assign(positions,self.plan.positions,self.room,
                                        self.safety.wall_distance,crowd,self.safety.wall_distance,
                                        people=observation.positions,limits=self.safety)
            finally:
                self._recovery_phase['query_wall_seconds']=perf_counter()-query_started
            self._recovery_phase['query_succeeded']=True
            # Independently verify each replacement leg against frozen people
            # and walls before installing any path. Simultaneous guide–guide
            # motion still goes through the unchanged velocity/segment filter.
            active=np.flatnonzero(nav.assignment>=0)
            if len(nav.paths)!=len(active):
                raise NavigationFailure('incomplete_path_set','candidate path set is incomplete')
            self._recovery_path_validation=self._validate_recovery_paths(
                {int(i):path for i,path in zip(active,nav.paths)},nav.assignment,positions,observation)
            self._recovery_phase['validation_succeeded']=True
            self._install_navigation(nav)
            self._recovery_phase['installation_succeeded']=True
        else:
            active=np.flatnonzero(self.assignment>=0)
            remaining=self.remaining_path_lengths(positions)
            self.priority_order=[int(active[k]) for k in np.argsort(-remaining,kind='stable')]
            self.priority_index=0; self.priority_mode=True
            # Same frozen order and the same single budget count. While this
            # mode is active the priority guide requests along-path progress;
            # a blocking guide is not given a nominal, and the joint filter
            # may move it. Leaving the mode does not restore the budget.
            self.coordination.update(activated=True,order=list(self.priority_order),
                                     exit_step=None,exit_reason=None)
            self._recovery_phase['coordination_law']=self.coordination['law']
            self._recovery_phase['priority_order']=list(self.priority_order)
        return strategy

    def _priority_guide(self, positions):
        if not (self.priority_mode and self.priority_order):
            return None
        while self.priority_index < len(self.priority_order):
            candidate=self.priority_order[self.priority_index]
            target=self.plan.positions[self.assignment[candidate]]
            if np.linalg.norm(target-positions[candidate]) <= self.config['position_tolerance']:
                self.priority_index += 1
            else:
                return candidate
        self.priority_mode=False
        # The queue walking forward is not a completion flag. Recheck every
        # assigned guide at the positions that produced this exit. Guides still
        # outside the arrival tolerance stay assigned; ordinary tracking resumes.
        outside=[]
        tolerance=float(self.config['position_tolerance'])
        for guide in np.flatnonzero(self.assignment>=0):
            target=self.plan.positions[self.assignment[guide]]
            distance=float(np.linalg.norm(target-positions[guide]))
            if distance > tolerance:
                outside.append({'guide':int(guide),'distance_m':distance})
        if self.coordination['exit_step'] is None:
            reason=('priority_order_within_tolerance' if not outside
                    else 'queue_finished_with_guides_outside_tolerance')
            self.coordination.update(exit_step=int(self._control_step), exit_reason=reason,
                                     exit_outside_tolerance=outside)
        return None

    def _path_sha256(self, guide):
        path=np.ascontiguousarray(self.paths[int(guide)], dtype=np.float64)
        return hashlib.sha256(path.tobytes()).hexdigest()

    def observe_priority_progress(self, next_positions, max_error, window, position_tolerance):
        """Fold the step that just ran into the priority guide's path window.

        A new guide, a new waypoint, or a path reload starts a new window and
        is recorded. The jump itself is not progress. The same reference does
        not clear the window. This does not change the recovery budget.
        Returns whether that priority window is stalled. Ordinary mode returns
        False; the caller keeps the mean-path test.
        """
        if not self.priority_mode or self._priority_reference is None:
            self._priority_samples=[]
            self._priority_token=None
            return False
        ref=self._priority_reference
        guide=int(ref['guide'])
        after=self.guide_remaining_path(guide, next_positions[guide])
        token=(guide, int(ref['waypoint']), ref['path_sha256'])
        continuous=(token==self._priority_token and len(self._priority_samples)>=1
                    and abs(float(ref['remaining_before_m'])-self._priority_samples[-1])<=1e-6)
        if not continuous:
            self.coordination['progress_reference_changes'].append({
                'step':int(self._control_step),'guide':guide,'waypoint':int(ref['waypoint']),
                'kind':'initial' if self._priority_token is None else 'reference_changed',
                'remaining_before_m':float(ref['remaining_before_m']),
                'previous_remaining_m':(None if not self._priority_samples
                                        else float(self._priority_samples[-1])),
            })
            self._priority_samples=[float(ref['remaining_before_m']), float(after)]
            self._priority_token=token
        else:
            self._priority_samples.append(float(after))
        return priority_path_stall(
            self._priority_samples, int(window), float(position_tolerance), float(max_error))

    def _note_disagreement(self, record):
        """Store a segment-check disagreement. Recording does not change the decision."""
        self.segment_disagreement_count += 1
        key=(tuple(np.round(record['start'], 5)), tuple(np.round(record['end'], 5)), record['reason'])
        if key in self._audit_keys or len(self.segment_audits) >= 24:
            return
        self._audit_keys.add(key)
        self.segment_audits.append(record)

    def _segment_checker(self):
        if self.complete_segment:
            audit=self._note_disagreement
            def check(start, end, domain, _audit=audit):
                return segment_in_free(start, end, domain, audit=_audit)
            return check
        return segment_in_free_sampled

    def _command_reaches(self, position, aim, domain, dt, checker):
        """The one execution sample toward ``aim`` stays in conservative free space."""
        delta=np.asarray(aim,float)-np.asarray(position,float)
        distance=float(np.linalg.norm(delta))
        if distance <= 1e-12:
            return True
        length=min(distance, float(dt)*self.safety.max_speed)
        return checker(position, position+delta/distance*length, domain)

    def _legacy_tracking(self, position, path, index, domain, checker):
        k=index
        while k < len(path)-1 and np.linalg.norm(path[k]-position) <= self.config['waypoint_tolerance']:
            if not checker(position, path[k+1], domain):
                break
            k+=1
        return k, self.config['gain']*(path[k]-position)

    def _proportional(self, delta):
        return self.config['gain']*delta

    def _finite_vertex_velocity(self, delta, distance, dt):
        """Nominal speed min(v_max, distance/dt) toward the vertex. Not a teleport."""
        direction=delta/distance
        speed=min(float(self.safety.max_speed), distance/float(dt))
        return speed*direction

    def _tracking_velocity(self, position, path, index, domain, dt):
        self._track_note=None
        # The disabled switch is the old proportional tracker, including on the
        # boundary. It is not evidence that boundary contact forbids arrival.
        if not self.follow_valid_step:
            return self._legacy_tracking(position, path, index, domain, segment_in_free_sampled)
        checker=self._segment_checker()
        if not self.vertex_arrival:
            if domain.signed_free_distance(position) <= self.safety.numeric_tolerance:
                return self._legacy_tracking(position, path, index, domain, checker)
            return self._gated_tracking(position, path, index, domain, dt, checker, cap_speed=False)
        return self._gated_tracking(position, path, index, domain, dt, checker, cap_speed=True)

    def _gated_tracking(self, position, path, index, domain, dt, checker, cap_speed):
        k=index
        tolerance=self.config['waypoint_tolerance']
        while k < len(path)-1 and np.linalg.norm(path[k]-position) <= tolerance:
            # Exactly on the vertex: hand off along the installed edge.
            # A later frame repeats the test from the true position.
            if (cap_speed and np.linalg.norm(path[k]-position) <= 1e-12
                    and checker(path[k], path[k+1], domain)):
                k+=1
                continue
            if not checker(position, path[k+1], domain):
                break
            if not self._command_reaches(position, path[k+1], domain, dt, checker):
                break
            k+=1
        aim=np.asarray(path[k], dtype=float)
        delta=aim-np.asarray(position, dtype=float)
        distance=float(np.linalg.norm(delta))
        if distance <= 1e-12:
            return k, np.zeros(2)
        next_index=k+1 if k < len(path)-1 else None
        connection=checker(position, aim, domain)
        next_from_vertex=(next_index is not None and checker(aim, path[next_index], domain))
        next_from_here=(next_index is not None and checker(position, path[next_index], domain)
                        and self._command_reaches(position, path[next_index], domain, dt, checker))
        immediate=self._command_reaches(position, aim, domain, dt, checker)
        near=distance <= tolerance
        # Finite-time arrival only when the whole segment to this vertex is legal
        # and the next installed edge is legal from the vertex but not from here.
        # Boundary proximity does not remove the candidate. An illegal connection
        # keeps the proportional command and the existing recovery budget.
        use_vertex=(cap_speed and near and connection and next_from_vertex and not next_from_here)
        if use_vertex:
            velocity=self._finite_vertex_velocity(delta, distance, dt)
            self._track_note={
                'vertex_index': int(k),
                'vertex': aim.tolist(),
                'next_waypoint': None if next_index is None else np.asarray(path[next_index], dtype=float).tolist(),
                'distance_m': distance,
                'connection_legal': True,
                'immediate_step_legal': bool(immediate),
                'next_edge_from_vertex_legal': True,
                'next_edge_from_here_legal': False,
                'nominal_velocity': velocity.tolist(),
                'nominal_speed_m_s': float(np.linalg.norm(velocity)),
                'signed_free_distance_m': float(domain.signed_free_distance(position)),
            }
            return k, velocity
        if cap_speed and near and next_from_vertex and not next_from_here and not connection:
            self._track_note={
                'vertex_index': int(k), 'vertex': aim.tolist(),
                'next_waypoint': None if next_index is None else np.asarray(path[next_index], dtype=float).tolist(),
                'distance_m': distance, 'connection_legal': False,
                'immediate_step_legal': bool(immediate),
                'next_edge_from_vertex_legal': True, 'next_edge_from_here_legal': False,
                'nominal_velocity': self._proportional(delta).tolist(),
                'nominal_speed_m_s': float(np.linalg.norm(self._proportional(delta))),
                'signed_free_distance_m': float(domain.signed_free_distance(position)),
                'rejected_direct_command': True,
            }
        if (not cap_speed) and near and connection and next_from_vertex and not next_from_here and k < len(path)-1:
            # Previous free-space gate: one uncapped step, then the outer speed limit.
            # Kept only for the ablation that disables vertex arrival.
            return k, delta/float(dt)
        return k, self._proportional(delta)

    def _priority_progress_command(self, position, path, index, domain, dt):
        """Request min(v_max, distance/dt) along the current legal aim.

        Priority mode already selected this guide and left every other nominal
        at zero. The direction is the installed aim, not the final target. An
        illegal segment keeps the tracker's slower command. Blocking motion,
        if required, comes from the joint projection in ``safe_velocity``.
        """
        aim = np.asarray(path[int(index)], dtype=float)
        delta = aim - np.asarray(position, dtype=float)
        distance = float(np.linalg.norm(delta))
        if distance <= 1e-12:
            return None, 'at_aim'
        if not self._segment_checker()(position, aim, domain):
            return None, 'illegal_aim'
        speed = min(float(self.safety.max_speed), distance / float(dt))
        return (delta / distance) * speed, 'along_segment'

    def step(self, observation, positions, dt):
        nominal=np.zeros_like(positions)
        priority=self._priority_guide(positions)
        domain=self._domain()
        notes=[]
        progress_request=None
        progress_tangent=None
        self._control_step += 1
        for i in np.flatnonzero(self.assignment>=0):
            if priority is not None and i != priority:
                continue
            path=self.paths[int(i)]
            before=int(self.waypoint[int(i)])
            index, velocity=self._tracking_velocity(positions[i], path, before, domain, dt)
            self.waypoint[int(i)]=index
            if priority is not None and int(i)==int(priority):
                boosted, reason=self._priority_progress_command(positions[i], path, index, domain, dt)
                if reason == 'illegal_aim':
                    self.coordination['illegal_aim_steps']+=1
                elif boosted is not None:
                    velocity=boosted
                    progress_request=float(np.linalg.norm(boosted))
                    progress_tangent=boosted/max(progress_request,1e-30)
            nominal[i]=velocity
            if self._track_note is not None:
                note=self._track_note
                note.update(guide=int(i), step=int(self._control_step),
                            position=np.asarray(positions[i], dtype=float).tolist(),
                            waypoint_before=before, waypoint_after=int(index),
                            handoff=int(index)!=before)
                notes.append(note)
                if not note.get('rejected_direct_command'):
                    self._vertex_pending.add(int(i))
            elif int(i) in self._vertex_pending and int(index)!=before:
                self.vertex_arrivals.append({
                    'guide': int(i), 'step': int(self._control_step), 'handoff': True,
                    'waypoint_before': before, 'waypoint_after': int(index),
                    'position': np.asarray(positions[i], dtype=float).tolist(),
                })
                self._vertex_pending.discard(int(i))
        speed=np.linalg.norm(nominal,axis=1)
        nominal*=np.minimum(1.,self.safety.max_speed/np.maximum(speed,1e-30))[:,None]
        result=safe_velocity(positions,nominal,observation.positions,self.room,dt,self.safety,
                             navigation_obstacle=domain.conservative_obstacle)
        decision=None
        achieved=None
        if priority is not None and progress_request is not None and progress_request > 1e-4:
            achieved=float(progress_tangent @ result.velocity[int(priority)])
            # The Euclidean projection can cut an arrival step in half and leave
            # the guide short of a corner vertex. One fixed-budget solve restores
            # that progress only when the executed segment is independently safe.
            if achieved < 0.9 * progress_request:
                self.coordination['enforce_attempts'] += 1
                enforced, elapsed=joint_progress_velocity(
                    positions, nominal, observation.positions, self.room, dt, self.safety,
                    domain.conservative_obstacle, int(priority), progress_tangent, progress_request)
                self.coordination['enforce_seconds'] += float(elapsed)
                if enforced is None:
                    self.coordination['enforce_rejected'] += 1
                    decision='rejected'
                else:
                    self.coordination['enforce_installed'] += 1
                    result=enforced
                    decision='installed'
                    achieved=float(progress_tangent @ result.velocity[int(priority)])
            else:
                decision='projection_sufficient'
        elif priority is not None:
            decision='not_requested'
        if priority is not None:
            self.coordination['progress_decisions'].append({
                'step':int(self._control_step),'guide':int(priority),'decision':decision,
                'request_m_s':None if progress_request is None else float(progress_request),
                'achieved_m_s':None if achieved is None else float(achieved),
            })
        if priority is not None:
            others=np.flatnonzero(np.arange(len(positions))!=priority)
            other_speed=float(np.linalg.norm(result.velocity[others],axis=1).max()) if len(others) else 0.0
            self.coordination['steps']+=1
            self.coordination['filter_seconds']+=float(result.solve_time)
            self.coordination['max_other_filtered_speed_m_s']=max(
                float(self.coordination['max_other_filtered_speed_m_s']), other_speed)
            if result.fallback_used:
                self.coordination['fallback_steps']+=1
        if priority is None:
            self._priority_reference=None
        else:
            guide=int(priority)
            self._priority_reference={
                'guide':guide,
                'waypoint':int(self.waypoint[guide]),
                'path_sha256':self._path_sha256(guide),
                'remaining_before_m':self.guide_remaining_path(guide, positions[guide]),
            }
        if notes:
            active=binding_constraints(
                positions, result.velocity, observation.positions, self.room, dt, self.safety,
                domain.conservative_obstacle)
            for note in notes:
                guide=note['guide']
                filtered=np.asarray(result.velocity[guide], dtype=float)
                vertex=np.asarray(note.get('vertex', note['position']), dtype=float)
                predicted=np.asarray(positions[guide], dtype=float)+float(dt)*filtered
                note['nominal_velocity']=np.asarray(nominal[guide], dtype=float).tolist()
                note['nominal_speed_m_s']=float(np.linalg.norm(nominal[guide]))
                note['filtered_velocity']=filtered.tolist()
                note['filtered_speed_m_s']=float(np.linalg.norm(filtered))
                note['predicted_distance_to_vertex_m']=float(np.linalg.norm(predicted-vertex))
                note['filter_reached_vertex']=bool(note['predicted_distance_to_vertex_m'] <= 1e-9)
                note['active_constraints']=[
                    item for item in active if item['guide']==guide or item.get('other_guide')==guide
                ]
                if len(self.vertex_arrivals) < 64:
                    self.vertex_arrivals.append(note)
        return nominal, result
