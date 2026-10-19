"""ABCG planning, reachable assignment, waypoint tracking and safe feedback."""
from time import perf_counter

import numpy as np
from shapely.geometry import Point, Polygon

from udc.boundary import estimate_boundary
from udc.deployment import (demand_profile, feasibility_bounds, infeasibility_certificate,
                            plan_coverage, plan_risk_aware)
from udc.navigation import navigate_and_assign, navigation_domain, route_assigned, segment_in_free
from udc.navigation.paths import NavigationFailure, ENDPOINT_TOLERANCE_M
import hashlib
from udc.safety import safe_velocity, path_clearances, is_safe


class ABCGController:
    # Disabled only by the path-following ablation. Boundary contact keeps the
    # previous command so a guide already on the planning shell is unchanged.
    follow_valid_step=True

    def __init__(self, config, safety, room, protocol='adaptive_resource', fixed_n=None):
        self.config=config; self.safety=safety; self.room=np.asarray(room,float)
        self.boundary=self.plan=self.assignment=None
        self.attempts=[]; self.paths={}; self.waypoint={}; self.replans=0
        self.protocol=protocol; self.fixed_n=fixed_n
        self.priority_order=[]; self.priority_index=0; self.priority_mode=False
        self.follow_valid_step=type(self).follow_valid_step
        self._observation=None
        self.navigation_domain=None
        self.margin_connections=[]
        self.recovery_attempts=[]
        # Stage diagnostics. They never change a termination status.
        self.feasibility=None; self.plan_found=False; self.assignment_found=False
        self.boundary_source=None
        self.prescribed_plan_installed=False

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
        if boundary_source not in {'prescribed_diagnostic', 'estimated_disk_union'}:
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
                rho=demand_profile(self.boundary,observation.positions,observation.demand,
                                   c['demand_bandwidth'],'mass_cvt')
                record={'n':n,'phase':phase,'max_gap':plan.max_gap,'min_separation':separation,
                    'cost':plan.cost.tolist(),'iterations':plan.iterations,'exit_reason':plan.exit_reason,
                    'accepted':accepted,'planner_seconds':perf_counter()-started,
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

    def remaining_path_lengths(self, positions):
        """Remaining polyline distance; unlike target distance it decreases on detours."""
        remaining=[]
        for i in np.flatnonzero(self.assignment>=0):
            path=self.paths[int(i)]; k=self.waypoint[int(i)]
            value=float(np.linalg.norm(path[k]-positions[i]))
            if k < len(path)-1:
                value += float(np.linalg.norm(np.diff(path[k:],axis=0),axis=1).sum())
            remaining.append(value)
        return np.asarray(remaining)

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
                    or not np.array_equal(path[0],positions[guide])
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
        return None

    def _command_reaches(self, position, aim, domain, dt):
        """The one execution sample toward ``aim`` stays in conservative free space."""
        delta=np.asarray(aim,float)-np.asarray(position,float)
        distance=float(np.linalg.norm(delta))
        if distance <= 1e-12:
            return True
        length=min(distance, float(dt)*self.safety.max_speed)
        return segment_in_free(position, position+delta/distance*length, domain)

    def _legacy_tracking(self, position, path, index, domain):
        k=index
        while k < len(path)-1 and np.linalg.norm(path[k]-position) <= self.config['waypoint_tolerance']:
            if not segment_in_free(position, path[k+1], domain):
                break
            k+=1
        return k, self.config['gain']*(path[k]-position)

    def _tracking_velocity(self, position, path, index, domain, dt):
        # A guide already on the planning boundary keeps the previous rule.
        # The immediate-step check is for a free-space approach that would
        # otherwise accept a corner shortcut the next sample cannot follow.
        if (not self.follow_valid_step
                or domain.signed_free_distance(position) <= self.safety.numeric_tolerance):
            return self._legacy_tracking(position, path, index, domain)
        k=index
        tolerance=self.config['waypoint_tolerance']
        while k < len(path)-1 and np.linalg.norm(path[k]-position) <= tolerance:
            if not segment_in_free(position, path[k+1], domain):
                break
            if not self._command_reaches(position, path[k+1], domain, dt):
                break
            k+=1
        aim=path[k]
        delta=aim-position
        distance=float(np.linalg.norm(delta))
        # The accepted gate is inside the existing radius, but proportional
        # gain would stop short of the vertex and leave the installed edge
        # invisible. One capped step reaches that vertex; it does not move
        # the guide by assignment or enlarge the radius.
        gate=(k < len(path)-1 and 1e-12 < distance <= tolerance
              and segment_in_free(aim, path[k+1], domain)
              and segment_in_free(position, aim, domain)
              and not (segment_in_free(position, path[k+1], domain)
                       and self._command_reaches(position, path[k+1], domain, dt)))
        if gate:
            return k, delta/float(dt)
        return k, self.config['gain']*delta

    def step(self, observation, positions, dt):
        nominal=np.zeros_like(positions)
        priority=self._priority_guide(positions)
        domain=self._domain()
        for i in np.flatnonzero(self.assignment>=0):
            if priority is not None and i != priority:
                continue
            path=self.paths[int(i)]
            index, velocity=self._tracking_velocity(positions[i], path, self.waypoint[int(i)], domain, dt)
            self.waypoint[int(i)]=index
            nominal[i]=velocity
        speed=np.linalg.norm(nominal,axis=1)
        nominal*=np.minimum(1.,self.safety.max_speed/np.maximum(speed,1e-30))[:,None]
        return nominal,safe_velocity(positions,nominal,observation.positions,self.room,dt,self.safety,
                                     navigation_obstacle=domain.conservative_obstacle)
