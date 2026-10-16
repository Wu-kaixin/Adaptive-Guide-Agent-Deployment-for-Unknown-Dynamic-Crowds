"""ABCG planning, reachable assignment, waypoint tracking and safe feedback."""
from time import perf_counter

import numpy as np
from shapely.geometry import Polygon

from udc.boundary import estimate_boundary
from udc.deployment import (demand_profile, feasibility_bounds, infeasibility_certificate,
                            plan_coverage, plan_risk_aware)
from udc.navigation import navigate_and_assign, route_assigned
from udc.safety import safe_velocity


class ABCGController:
    def __init__(self, config, safety, room, protocol='adaptive_resource', fixed_n=None):
        self.config=config; self.safety=safety; self.room=np.asarray(room,float)
        self.boundary=self.plan=self.assignment=None
        self.attempts=[]; self.paths={}; self.waypoint={}; self.replans=0
        self.protocol=protocol; self.fixed_n=fixed_n
        self.priority_order=[]; self.priority_index=0; self.priority_mode=False
        self._observation=None
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
        nav=navigate_and_assign(guides,plan.positions,self.room,self.safety.wall_distance,
                                crowd,self.safety.wall_distance)
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
        nav=navigate_and_assign(guides,chosen.positions,self.room,self.safety.wall_distance,
                                crowd,self.safety.wall_distance)
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

    def recover(self, observation, positions):
        """Finite deterministic recovery: reroute, reassign, then priority yield."""
        if self.replans >= self.config['max_replans']:
            return None
        strategy=('path_recompute','reachable_reassignment','priority_yield')[min(self.replans,2)]
        crowd=Polygon(self.boundary.crowd)
        if strategy == 'path_recompute':
            paths,_=route_assigned(positions,self.plan.positions,self.assignment,self.room,
                                   self.safety.wall_distance,crowd,self.safety.wall_distance)
            self.paths=paths; self.waypoint={i:1 for i in paths}
        elif strategy == 'reachable_reassignment':
            nav=navigate_and_assign(positions,self.plan.positions,self.room,
                                    self.safety.wall_distance,crowd,self.safety.wall_distance)
            self._install_navigation(nav)
        else:
            active=np.flatnonzero(self.assignment>=0)
            remaining=self.remaining_path_lengths(positions)
            self.priority_order=[int(active[k]) for k in np.argsort(-remaining,kind='stable')]
            self.priority_index=0; self.priority_mode=True
        self.replans += 1
        return strategy

    def step(self, observation, positions, dt):
        nominal=np.zeros_like(positions)
        priority=None
        if self.priority_mode and self.priority_order:
            while self.priority_index < len(self.priority_order):
                candidate=self.priority_order[self.priority_index]
                target=self.plan.positions[self.assignment[candidate]]
                if np.linalg.norm(target-positions[candidate]) <= self.config['position_tolerance']:
                    self.priority_index += 1
                else:
                    priority=candidate; break
            if self.priority_index >= len(self.priority_order):
                self.priority_mode=False
        for i in np.flatnonzero(self.assignment>=0):
            if priority is not None and i != priority:
                continue
            path=self.paths[int(i)]; k=self.waypoint[int(i)]
            while k < len(path)-1 and np.linalg.norm(path[k]-positions[i]) <= self.config['waypoint_tolerance']:
                k+=1
            self.waypoint[int(i)]=k
            nominal[i]=self.config['gain']*(path[k]-positions[i])
        speed=np.linalg.norm(nominal,axis=1)
        nominal*=np.minimum(1.,self.safety.max_speed/np.maximum(speed,1e-30))[:,None]
        return nominal,safe_velocity(positions,nominal,observation.positions,self.room,dt,self.safety)
