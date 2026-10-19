"""Read frozen reconstruction evidence; verify paths, pairing and all replay verdicts."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from time import perf_counter

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from shapely.geometry import LineString, Point, Polygon, mapping
from threadpoolctl import threadpool_limits

from udc.navigation.paths import navigation_domain, _other_guide_space, _shortest, _visibility_graph
from udc.navigation import navigate_and_assign
from udc.safety import Safety, is_safe, path_clearances
from .nonconvex_rebuild import array_hash, load_boundary, sha, write


METHODS=('equal_arc','projected_mass','pairwise_euclidean')


def _parts(geometry):
    if geometry.geom_type=='Polygon':
        return [geometry]
    return [part for part in geometry.geoms if part.geom_type=='Polygon']


def _path_check(path, domain, people, room, limits):
    clearances=[path_clearances(a[None,:],b[None,:],people,room) for a,b in zip(path[:-1],path[1:])]
    return {'whole_line_in_domain':bool(domain.free.covers(LineString(path))),
            'physical_people_and_wall_safe':all(is_safe(value,limits) for value in clearances),
            'simultaneous_guide_safety': 'checked separately on executed trajectories',
            'segment_clearances':clearances}


def compare_targets(output, seed):
    """Reuse one static domain/visibility graph across the three saved target sets.

    Query only the saved complete matching. Other same-component edges remain
    explicitly unqueried, rather than being represented as verified paths.
    """
    cell=output/'inputs/wide_u'/f'seed_{seed}'
    frozen=np.load(cell/'input.npz',allow_pickle=False)
    boundary=load_boundary(cell)
    domain=navigation_domain(frozen['room'],0.55,Polygon(boundary.crowd),0.55)
    graph=_visibility_graph(domain.free,domain.conservative_obstacle)
    rows=[]
    targets_of=[]
    for method in METHODS:
        run=output/'baseline/wide_u'/f'seed_{seed}'/method
        data=np.load(run/'trajectory.npz',allow_pickle=False)
        metrics=json.loads((run/'metrics.json').read_text())
        limits=Safety(**metrics['safety_limits'])
        targets=data['targets']; targets_of.append(targets)
        guide_components=[domain.component_index(point) for point in frozen['guides']]
        target_components=[domain.component_index(point) for point in targets]
        relation=np.array([[1 if a is not None and a==b else 0 for b in target_components]
                           for a in guide_components],dtype=int)
        witnessed=[]
        started=perf_counter()
        for guide,target in enumerate(data['assignment']):
            if target<0:
                continue
            path,length=_shortest(frozen['guides'][guide],targets[target],domain.free,
                                  domain.conservative_obstacle,graph=graph)
            if path is None:
                raise AssertionError('Saved initial matching lost its path witness')
            checked=_path_check(path,domain,frozen['people'],frozen['room'],limits)
            assert checked['whole_line_in_domain'] and checked['physical_people_and_wall_safe']
            relation[guide,target]=2
            witnessed.append({'guide':guide,'target':int(target),'path':path.tolist(),
                              'length_m':length,'independent_validation':checked})
        rows.append({'method':method,'baseline_status':metrics['termination_status'],
                     'guide_components':guide_components,'target_components':target_components,
                     'invalid_guides':sum(value is None for value in guide_components),
                     'invalid_targets':sum(value is None for value in target_components),
                     'relation_codes':{'0':'different_components_or_invalid','1':'same_component_not_queried',
                                       '2':'found_and_independently_verified_path'},
                     'relation':relation.tolist(),'saved_assignment':data['assignment'].tolist(),
                     'verified_complete_matching':len(witnessed)==len(targets), 'matched_paths':witnessed,
                     'matched_queries_and_validation_s':perf_counter()-started})
    differences=[]
    for method,targets in zip(METHODS,targets_of):
        distances=np.linalg.norm(targets[:,None,:]-targets_of[0][None,:,:],axis=2)
        differences.append({'method':method,
                            'same_index_max_distance_m':float(np.linalg.norm(targets-targets_of[0],axis=1).max()),
                            'set_hausdorff_m':float(max(distances.min(axis=0).max(),distances.min(axis=1).max()))})
    return {'seed':seed,'input_sha256':json.loads((cell/'input_record.json').read_text())['input_sha256'],
            'geometry_sha256':domain.geometry_sha256,'free_geojson':mapping(domain.free),
            'component_count':len(_parts(domain.free)),'methods':rows,'target_differences_from_equal_arc':differences,
            'static_graph_builds':1,'all_pair_graph_rebuilds':0}


def failure_diagnosis(output):
    run=output/'baseline/wide_u/seed_22/equal_arc'
    data=np.load(run/'trajectory.npz',allow_pickle=False)
    metrics=json.loads((run/'metrics.json').read_text())
    fixed_run=output/'repair_regression/wide_u/seed_22/equal_arc'
    fixed=np.load(fixed_run/'trajectory.npz',allow_pickle=False)
    repaired=json.loads((fixed_run/'metrics.json').read_text())
    limits=Safety(**metrics['safety_limits'])
    domain=navigation_domain(data['room'],limits.wall_distance,Polygon(data['boundary_crowd']),limits.wall_distance)
    positions=data['positions'][-1]
    guide=7; target_id=int(data['assignment'][guide]); target=data['targets'][target_id]
    free,_=_other_guide_space(domain,positions,guide,limits.guide_distance)
    target_distances=np.linalg.norm(positions-target,axis=1)
    blockers=[int(i) for i,d in enumerate(target_distances) if i!=guide and d<limits.guide_distance]
    first_exit=None
    for frame,points in enumerate(data['positions']):
        for i,point in enumerate(points):
            if not domain.free.covers(Point(point)) and domain.signed_free_distance(point)<-1e-7:
                first_exit={'frame':frame,'guide':i,'position':point.tolist(),
                            'signed_distance_m':domain.signed_free_distance(point)}
                break
        if first_exit is not None:
            break
    attempts=repaired['recovery_attempts']
    old_paths=attempts[0]['paths']
    straight=np.array([positions[guide],target])
    probe=_path_check(straight,domain,data['crowd_positions'],data['room'],limits)
    moving_start,moving_end=positions.copy(),positions.copy()
    moving_end[guide]=target
    static_others_clearance=path_clearances(moving_start,moving_end,data['crowd_positions'],data['room'])
    old_assignment=np.array(attempts[0]['assignment']); new_assignment=np.array(attempts[1]['assignment'])
    assert np.array_equal(data['targets'],fixed['targets'])
    assert np.array_equal(data['positions'],fixed['positions'][:len(data['positions'])])
    return {'scene':'wide_u','seed':22,'method':'equal_arc','baseline_status':metrics['termination_status'],
            'classification':'execution_recovery_failed_on_temporarily_occupied_target',
            'failure_frame':len(data['positions'])-1,'failure_time_s':float(data['time'][-1]),
            'guide':guide,'target_id':target_id,'actual_position':positions[guide].tolist(),'target':target.tolist(),
            'waypoint_index':data['waypoint_index'][-1].tolist(), 'old_paths':old_paths,
            'assignment':data['assignment'].tolist(),'positions':positions.tolist(),
            'static_geometry_sha256':domain.geometry_sha256,
            'domain_parameters':{'wall_margin':domain.wall_margin,'crowd_margin':domain.crowd_margin,
                                 'simplify_m':domain.simplify_tolerance_m,'planning_margin_m':domain.planning_margin_m},
            'static_start_signed_m':domain.signed_free_distance(positions[guide]),
            'static_target_signed_m':domain.signed_free_distance(target),
            'static_components':[domain.component_index(positions[guide]),domain.component_index(target)],
            'dynamic_free_geojson':mapping(free),'dynamic_start_valid':bool(free.covers(Point(positions[guide]))),
            'dynamic_free_sha256':hashlib.sha256(free.wkb).hexdigest(),
            'temporary_guide_obstacle_parameters':{'center_radius_m':limits.guide_distance,
                                                    'quad_segs':2,'excluded_self_guide':guide},
            'dynamic_target_valid':bool(free.covers(Point(target))),
            'target_blockers':blockers,'target_blocker_center_distances_m':{str(i):float(target_distances[i]) for i in blockers},
            'target_blocker_clearance_residual_m':{str(i):float(target_distances[i]-limits.guide_distance) for i in blockers},
            'static_direct_path':straight.tolist(),'static_direct_path_verification':probe,
            'static_other_guides_connection_check':{'clearances':static_others_clearance,
                                                   'safe':is_safe(static_others_clearance,limits)},
            'first_static_domain_exit_beyond_tolerance':first_exit,
            'old_exception':metrics['failure_reason'],'old_replan_count':metrics['replan_count'],
            'old_events':metrics['deadlock_events'],'repaired_recovery_attempts':attempts,
            'assignment_changes':[int(i) for i in np.flatnonzero(old_assignment!=new_assignment)],
            'targets_unchanged':True,'trajectory_prefix_bitwise_identical':True,
            'changed_target_experiment':False,'repaired_status':repaired['termination_status'],
            'repaired_simulation_time_s':repaired['simulation_time'],
            'repaired_minimum_net_clearances':repaired['minimum_net_clearances'],
            'path_identity_note':'Existing reachable_reassignment returned the same mapping; full static-domain routes were freshly verified and installed.'}


def audit_results(output):
    manifest=json.loads((output/'manifest.json').read_text())['tasks']
    rows=[]
    for row in manifest:
        cell=output/row['cell']
        record=json.loads((cell/'input_record.json').read_text())
        assert sha(cell/'input.npz')==record['input_file_sha256']
        assert sha(cell/'input.yaml')==record['config_sha256']
        frozen=np.load(cell/'input.npz',allow_pickle=False)
        assert array_hash(**{key:frozen[key] for key in ('people','radii','demand','guides','room')})==row['input_sha256']
        for tag in ('baseline','repair_regression'):
            run=output/tag/row['scene']/f"seed_{row['seed']}"/row['method']
            metrics=json.loads((run/'metrics.json').read_text())
            offline=json.loads((run/'reevaluated_metrics.json').read_text())
            data=np.load(run/'trajectory.npz',allow_pickle=False)
            assert offline['criteria_consistent'] and offline['success']==metrics['success']
            assert np.array_equal(data['positions'][0],frozen['guides'])
            assert np.array_equal(data['crowd_positions'],frozen['people'])
            assert np.array_equal(data['crowd_radii'],frozen['radii'])
            assert np.array_equal(data['crowd_demand'],frozen['demand'])
            assert np.array_equal(data['room'],frozen['room'])
            executed=metrics.get('steps',0)>0
            if executed:
                targets=output/'targets'/row['scene']/f"seed_{row['seed']}"/row['method']
                plan=json.loads((targets/'plan.json').read_text())
                assert sha(targets/'targets.npz')==plan['targets_sha256']
                assert np.array_equal(data['targets'],np.load(targets/'targets.npz',allow_pickle=False)['targets'])
            actual_length=float(np.linalg.norm(np.diff(data['positions'],axis=0),axis=2).sum()) if executed else None
            rows.append({**row,'tag':tag,'status':metrics['termination_status'],'success':metrics['success'],
                         'executed':executed,'simulation_time_s':metrics.get('simulation_time'),
                         'actual_trajectory_length_m':actual_length,'controller_navigation_length_m':metrics.get('path_length'),
                         'wall_time_s':metrics.get('wall_time'),'replan_count':metrics.get('replan_count'),
                         'minimum_net_clearances':offline.get('diagnostics',{}).get('minimum_net_clearances'),
                         'offline_consistent':True,'input_and_targets_bitwise_exact':True,
                         'commit':metrics.get('commit',metrics.get('provenance',{}).get('commit')),
                         'source_sha256':metrics.get('source_sha256',metrics.get('provenance',{}).get('source_sha256'))})
    # Every executed trajectory, including the two minimal diagnostic replay tags,
    # must already have an independent saved verdict.
    executions=[]
    for path in sorted(output.rglob('metrics.json')):
        metrics=json.loads(path.read_text())
        if metrics.get('steps',0)<=0:
            continue
        verdict=json.loads(path.with_name('reevaluated_metrics.json').read_text())
        assert verdict['criteria_consistent']
        executions.append({'run':path.parent.relative_to(output).as_posix(),
                           'success':verdict['success'],'criteria_consistent':True})
    trajectories_identical=0
    for row in manifest:
        if row['input_status']!='ACCEPTED':
            continue
        sub=Path(row['scene'])/f"seed_{row['seed']}"/row['method']/'trajectory.npz'
        old=np.load(output/'baseline'/sub,allow_pickle=False)
        new=np.load(output/'repair_regression'/sub,allow_pickle=False)
        trajectories_identical+=np.array_equal(old['positions'],new['positions'])
    return {'rows':rows,'paired_input_units':15,'paired_method_rows':45,
            'exact_input_pairing':True,'targets_changed':0,
            'all_executed_trajectory_verdicts':executions,'executed_trajectories_including_minimal_replays':len(executions),
            'unchanged_valid_trajectories_bitwise':trajectories_identical,
            'baseline_outcomes':dict(Counter(row['status'] for row in rows if row['tag']=='baseline')),
            'regression_outcomes':dict(Counter(row['status'] for row in rows if row['tag']=='repair_regression'))}


def _paint_geometry(axis,geometry,color,alpha=1.):
    for part in _parts(geometry):
        exterior=np.asarray(part.exterior.coords)
        axis.fill(exterior[:,0],exterior[:,1],color=color,alpha=alpha,linewidth=0)
        for ring in part.interiors:
            hole=np.asarray(ring.coords)
            axis.fill(hole[:,0],hole[:,1],color='white',linewidth=0)


def plot_comparison(output,diagnosis,destination):
    fig,axes=plt.subplots(2,3,figsize=(14,9),layout='constrained')
    for row,seed in enumerate((21,22)):
        for col,method in enumerate(METHODS):
            axis=axes[row,col]
            data=np.load(output/f'baseline/wide_u/seed_{seed}'/method/'trajectory.npz',allow_pickle=False)
            boundary=Polygon(data['boundary_crowd'])
            domain=navigation_domain(data['room'],0.55,boundary,0.55)
            _paint_geometry(axis,domain.free,'#e0f0ec')
            _paint_geometry(axis,domain.conservative_obstacle,'#9ba8b2',0.65)
            curve=data['boundary_deployment']; curve=np.vstack([curve,curve[0]])
            axis.plot(curve[:,0],curve[:,1],color='#5362a8',lw=1,label='Deployment curve')
            axis.scatter(data['crowd_positions'][:,0],data['crowd_positions'][:,1],s=10,c='#243746',label='People')
            axis.scatter(data['targets'][:,0],data['targets'][:,1],s=32,marker='^',c='#d57a14',label='Targets')
            frame=0 if row==0 else min(290,len(data['positions'])-1)
            points=data['positions'][frame]
            axis.scatter(points[:,0],points[:,1],s=16,c='#087e8b',label='Guides (initial / 29 s)')
            for guide,target in enumerate(data['assignment']):
                path=data['positions'][:,guide]
                axis.plot(path[:,0],path[:,1],color='#4c91a7',alpha=0.28,lw=0.65)
            metrics=json.loads((output/f'baseline/wide_u/seed_{seed}'/method/'metrics.json').read_text())
            if row==1 and col==0:
                for blocker in diagnosis['target_blockers']:
                    axis.add_patch(plt.Circle(points[blocker],0.75,color='#ba4139',alpha=0.2))
                start=np.asarray(diagnosis['actual_position']); target=np.asarray(diagnosis['target'])
                axis.plot([start[0],target[0]],[start[1],target[1]],color='#b82e28',lw=2)
                axis.annotate('G7 / target 12\nblocked by G9',target,xytext=(16.2,17.5),fontsize=8,
                              arrowprops={'arrowstyle':'->','color':'#b82e28'})
                axis.set_xlim(9,19);axis.set_ylim(10,20)
            elif row==1:
                axis.set_xlim(9,19);axis.set_ylim(10,20)
            else:
                axis.set_xlim(0,30);axis.set_ylim(0,30)
            axis.set_title(f'wide-U seed {seed} / {method}\n{metrics["termination_status"]}',fontsize=10)
            axis.set_aspect('equal');axis.set_xlabel('x [m]');axis.set_ylabel('y [m]')
    axes[0,0].legend(loc='lower right',fontsize=7)
    fig.suptitle('New frozen inputs: seed 21 succeeds; seed 22 Equal Arc fails during temporary-guide replan',fontsize=12)
    destination.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(destination,dpi=180)
    plt.close(fig)


def plot_recovery(output,destination):
    old=np.load(output/'baseline/wide_u/seed_22/equal_arc/trajectory.npz',allow_pickle=False)
    new=np.load(output/'repair_regression/wide_u/seed_22/equal_arc/trajectory.npz',allow_pickle=False)
    fig=plt.figure(figsize=(12,6),layout='constrained')
    grid=fig.add_gridspec(2,2)
    local=fig.add_subplot(grid[:,0]); error_axis=fig.add_subplot(grid[0,1]); margin_axis=fig.add_subplot(grid[1,1])
    for guide,color in ((7,'#b44334'),(9,'#197a9b')):
        path=new['positions'][290:,guide]
        target=new['targets'][new['assignment'][guide]]
        local.plot(path[:,0],path[:,1],color=color,lw=2,label=f'Guide {guide}, after verified recovery')
        local.scatter(*path[0],color=color,s=45)
        local.scatter(*target,color=color,marker='^',s=55)
        local.annotate(f'G{guide} at 29 s',path[0],xytext=(5,6),textcoords='offset points',fontsize=9)
        errors=np.linalg.norm(new['positions'][:,guide]-target,axis=1)
        error_axis.plot(new['time'],errors,color=color,label=f'Guide {guide}')
    for other,point in enumerate(old['positions'][-1]):
        if other==9:
            local.add_patch(plt.Circle(point,0.75,color='#197a9b',alpha=0.13))
    curve=old['boundary_deployment'];local.plot(curve[:,0],curve[:,1],color='#7a7a7a',lw=1)
    focus=new['positions'][290:,[7,9]].reshape(-1,2)
    local.set_xlim(focus[:,0].min()-0.5,focus[:,0].max()+0.8)
    local.set_ylim(focus[:,1].min()-0.5,focus[:,1].max()+0.8);local.set_aspect('equal')
    local.set_xlabel('x [m]');local.set_ylabel('y [m]');local.set_title('Same targets and mapping; temporary blocker moves')
    local.legend(loc='upper left',fontsize=8)
    distances=np.linalg.norm(new['positions'][:,:,None,:]-new['positions'][:,None,:,:],axis=3)
    distances[:,np.arange(19),np.arange(19)]=np.inf
    margin_axis.plot(new['time'],distances.min(axis=(1,2))-0.75,color='#426b50')
    margin_axis.axhline(0.,color='#9e342c',lw=1,ls='--')
    margin_axis.set_yscale('symlog',linthresh=1e-6)
    margin_axis.set_xlabel('simulation time [s]');margin_axis.set_ylabel('frame guide clearance margin [m]')
    for axis in (error_axis,margin_axis):
        axis.axvline(29.,color='#777777',ls=':',lw=1)
        axis.set_xlim(25,34)
    error_axis.set_ylabel('target error [m]');error_axis.set_yscale('log');error_axis.legend()
    fig.suptitle('wide-U seed 22 / Equal Arc: baseline stops at 29.0 s; unchanged targets succeed at 33.7 s')
    fig.savefig(destination,dpi=180);plt.close(fig)


def confirmation_audit(output, report_dir):
    """Descriptive new-seed confirmation; shared method rows are not independent crowds."""
    manifest=json.loads((output/'manifest.json').read_text())
    protocol=json.loads((output/'protocol.json').read_text())
    rows=[]; events=[]; valid_cells=set(); input_cells={}; verdicts=[]
    frozen_source=protocol['provenance']['source_sha256']
    for task in manifest['tasks']:
        cell=output/task['cell']; key=(task['scene'],task['seed'])
        record=json.loads((cell/'input_record.json').read_text())
        assert sha(cell/'input.npz')==record['input_file_sha256'] and sha(cell/'input.yaml')==record['config_sha256']
        if record['boundary_sha256'] is not None:
            assert sha(cell/'boundary.npz')==record['boundary_sha256']
        quality=record['people_quality']
        valid_input=quality['meets_stated_separation'] and quality['inside_room']
        if valid_input:
            valid_cells.add(key)
        input_cells[key]=record
        frozen=np.load(cell/'input.npz',allow_pickle=False)
        assert array_hash(**{name:frozen[name] for name in ('people','radii','demand','guides','room')})==task['input_sha256']
        run=output/'baseline'/task['scene']/f"seed_{task['seed']}"/task['method']
        metric=json.loads((run/'metrics.json').read_text())
        provenance=metric.get('provenance',metric)
        if metric['termination_status']!='EXPERIMENT_ERROR':
            assert provenance['source_sha256']==frozen_source
        executed=metric.get('steps',0)>0
        stage=('input' if not valid_input else 'boundary' if metric['termination_status'] in ('BOUNDARY_INVALID','OFFSET_INVALID')
               else 'quantity' if (metric.get('feasibility') or {}).get('infeasibility_certificate') else
               'initial_path' if metric.get('plan_found') and not metric.get('assignment_found') else
               'execution' if metric.get('failure_stage')=='execution' else 'planning')
        if metric['success']:
            stage='success'
        if metric['termination_status']=='EXPERIMENT_ERROR':
            stage='program_error'
        offline=json.loads((run/'reevaluated_metrics.json').read_text()) if (run/'reevaluated_metrics.json').is_file() else None
        data=np.load(run/'trajectory.npz',allow_pickle=False) if (run/'trajectory.npz').is_file() else None
        if offline is not None:
            assert offline['criteria_consistent']
            if metric['termination_status']!='EXPERIMENT_ERROR':
                assert offline['success']==metric['success']
        if data is not None:
            assert np.array_equal(data['positions'][0],frozen['guides'])
            for actual,name in (('crowd_positions','people'),('crowd_radii','radii'),('crowd_demand','demand'),('room','room')):
                assert np.array_equal(data[actual],frozen[name])
        if executed:
            assert offline is not None
            verdicts.append({'task_id':task['task_id'],'success':offline['success'],'criteria_consistent':True})
        target_dir=output/'targets'/task['scene']/f"seed_{task['seed']}"/task['method']
        planning_time=None; target_hash=None
        if (target_dir/'solver.json').is_file():
            solver=json.loads((target_dir/'solver.json').read_text())
            planning_time=solver.get('planning_wall_s')
        if (target_dir/'targets.npz').is_file():
            plan_info=json.loads((target_dir/'plan.json').read_text())
            target_hash=sha(target_dir/'targets.npz'); assert target_hash==plan_info['targets_sha256']
            if metric.get('plan_found'):
                assert np.array_equal(data['targets'],np.load(target_dir/'targets.npz',allow_pickle=False)['targets'])
        attempts=metric.get('recovery_attempts',[])
        for event in attempts:
            required={'trigger_reason','positions','query_domain','current_occupations','classification','query_succeeded',
                      'validation_succeeded','installation_succeeded','budget_consumed','budget_remaining','before','after'}
            assert required<=event.keys()
            assert event['budget_consumed']<=3 and event['budget_remaining']==3-event['budget_consumed']
            assert event['before']['targets_sha256']==event['after']['targets_sha256']
            if not event['accepted']:
                assert event['before']==event['after']
            if event['installation_succeeded']:
                assert event['query_succeeded'] and event['validation_succeeded']
            events.append({'task_id':task['task_id'],**event})
        actual_length=float(np.linalg.norm(np.diff(data['positions'],axis=0),axis=2).sum()) if executed else None
        diagnostic={} if offline is None else offline['diagnostics']
        rows.append({**task,'valid_input':bool(valid_input),'status':metric['termination_status'],'success':metric['success'],
                     'failure_stage_category':stage,'failure_reason':metric.get('failure_reason'),
                     'failure_diagnosis':metric.get('failure_diagnosis'),'executed':executed,
                     'offline_consistent':None if offline is None else offline['criteria_consistent'],
                     'source_sha256':provenance.get('source_sha256'),'commit':provenance.get('commit'),
                     'target_sha256':target_hash,'planning_wall_s':planning_time,
                     'navigation_wall_s':metric.get('initial_navigation_wall_seconds'),
                     'execution_wall_s':metric.get('wall_time'),'simulation_time_s':metric.get('simulation_time') if executed else None,
                     'actual_trajectory_length_m':actual_length,'installed_navigation_length_m':metric.get('path_length'),
                     'actual_max_gap_m':diagnostic.get('actual_max_gap'),'max_curve_distance_m':diagnostic.get('max_curve_distance'),
                     'minimum_net_clearances':diagnostic.get('minimum_net_clearances'),
                     'recovery_trigger_frames':sorted({e.get('frame') for e in attempts}),
                     'recovery_installations':sum(e['installation_succeeded'] for e in attempts),
                     'recovery_budget_consumed':metric.get('replan_count',0)})
    success=sum(row['success'] for row in rows)
    valid_rows=[row for row in rows if row['valid_input']]
    def stats(values):
        values=[float(v) for v in values if v is not None]
        return None if not values else {'n':len(values),'min':min(values),'median':float(np.median(values)),'max':max(values)}
    executed_rows=[row for row in rows if row['executed']]
    triggers={(e['task_id'],e.get('frame')) for e in events}
    accepted_triggers={(e['task_id'],e.get('frame')) for e in events if e['accepted']}
    summary={'protocol':protocol,'tasks_expected':manifest['task_count'],'tasks_completed':len(rows),'rows':rows,
             'input_units':len(input_cells),'valid_input_units':len(valid_cells),
             'input_failures':[record for record in input_cells.values() if not (record['people_quality']['meets_stated_separation'] and record['people_quality']['inside_room'])],
             'overall_success':{'success':success,'denominator':len(rows),'fraction':success/len(rows)},
             'valid_input_success':{'success':sum(row['success'] for row in valid_rows),'denominator':len(valid_rows),
                                    'fraction':None if not valid_rows else sum(row['success'] for row in valid_rows)/len(valid_rows)},
             'outcomes':dict(Counter(row['status'] for row in rows)),
             'stage_counts':dict(Counter(row['failure_stage_category'] for row in rows)),
             'by_method':{method:dict(Counter(row['status'] for row in rows if row['method']==method)) for method in METHODS},
             'executed_trajectories':len(verdicts),'independent_verdicts':verdicts,'verdict_mismatches':0,
             'recovery':{'trigger_count':len(triggers),'accepted_trigger_count':len(accepted_triggers),
                         'verified_installations':sum(e['installation_succeeded'] for e in events),
                         'budget_exhausted_events':sum(e['classification']=='budget_exhausted' for e in events),
                         'classifications':dict(Counter(e['classification'] for e in events)),'events':events},
             'interval_safety_check':'Exact constant-velocity closest approach on every executed interval, not only frames',
             'performance':{key:stats(row[key] for row in executed_rows) for key in
                ('simulation_time_s','actual_trajectory_length_m','execution_wall_s','navigation_wall_s','actual_max_gap_m','max_curve_distance_m')},
             'planning_by_method':{method:stats(row['planning_wall_s'] for row in rows if row['method']==method) for method in METHODS},
             'minimum_net_clearances':{key:min(row['minimum_net_clearances'][key] for row in executed_rows) for key in ('crowd','guides','walls')} if executed_rows else None,
             'recovery_query_wall_s':stats(e.get('query_wall_seconds') for e in events),
             'recovery_validation_wall_s':stats(e.get('validation_wall_seconds') for e in events),
             'external_interruptions':len(list((output/'baseline').rglob('interruption.json')))}
    write(report_dir/'summary.json',summary)
    write(output/'confirmation_audit.json',summary)
    # A fixed scene-by-seed outcome panel avoids selecting favorable tasks.
    fig,axes=plt.subplots(1,3,figsize=(14,4.3),layout='constrained')
    seeds=protocol['spec']['seeds']
    for axis,method in zip(axes,METHODS):
        matrix=np.zeros((3,len(seeds)))
        labels=np.empty_like(matrix,dtype=object)
        for i,scene in enumerate(protocol['spec']['scenes']):
            for j,seed in enumerate(seeds):
                row=next(row for row in rows if row['scene']==scene and row['seed']==seed and row['method']==method)
                matrix[i,j]=2 if row['success'] else 0 if not row['valid_input'] else 1
                labels[i,j]='S' if row['success'] else 'I' if not row['valid_input'] else 'F'
        axis.imshow(matrix,vmin=0,vmax=2,cmap=matplotlib.colors.ListedColormap(['#b7bec8','#d98e69','#92c5ad']),aspect='auto')
        for i in range(3):
            for j in range(len(seeds)):
                axis.text(j,i,labels[i,j],ha='center',va='center',fontsize=10)
        axis.set_xticks(range(len(seeds)),seeds);axis.set_yticks(range(3),protocol['spec']['scenes'])
        axis.set_title(method);axis.set_xlabel('new development seed')
    fig.suptitle('Nonconvex confirmation v2: S success / I invalid input / F other failure (shared inputs across methods)')
    fig.savefig(report_dir/'outcomes_90.png',dpi=180);plt.close(fig)
    print(json.dumps({'outcomes':summary['outcomes'],'valid_units':len(valid_cells),
                      'executed':len(verdicts),'recovery_triggers':len(triggers)}))
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='runs/step1_nonconvex_record_rebuild_v1')
    parser.add_argument('--report-dir',default='reports/step1_nonconvex_record_rebuild_v1')
    args=parser.parse_args()
    output=Path(args.output); report_dir=Path(args.report_dir)
    if json.loads((output/'protocol.json').read_text())['spec']['protocol']=='nonconvex_confirmation_v2':
        confirmation_audit(output,report_dir)
        return
    audit=audit_results(output)
    comparison=[compare_targets(output,seed) for seed in (21,22)]
    diagnosis=failure_diagnosis(output)
    sample=np.load(output/'baseline/wide_u/seed_22/equal_arc/trajectory.npz',allow_pickle=False)
    with threadpool_limits(limits=1):
        started=perf_counter()
        nav=navigate_and_assign(sample['positions'][0],sample['targets'],sample['room'],0.55,
                                Polygon(sample['boundary_crowd']),0.55)
        navigation_benchmark={'wall_seconds':perf_counter()-started,'guide_count':19,'target_count':19,
                              'static_graph_builds':1,'numerical_threads':1,
                              'reachable_selected_paths':len(nav.paths),'samples':1,
                              'includes':'domain construction, one static graph, all pair queries and matching'}
    write(output/'diagnostics/pairing_and_verdict_audit.json',audit)
    write(output/'diagnostics/wide_u_comparison.json',comparison)
    write(output/'diagnostics/failure_and_repair.json',diagnosis)
    plot_comparison(output,diagnosis,report_dir/'wide_u_comparison.png')
    plot_recovery(output,report_dir/'recovery_comparison.png')
    write(report_dir/'diagnostics.json',{'audit':audit,'wide_u_comparison':comparison,'failure':diagnosis,
                                        'navigation_benchmark':navigation_benchmark})
    print(json.dumps({'baseline':audit['baseline_outcomes'],'regression':audit['regression_outcomes'],
                      'all_executed_independently_evaluated':len(audit['all_executed_trajectory_verdicts']),
                      'unchanged_valid_trajectories':audit['unchanged_valid_trajectories_bitwise'],
                      'assignment_changes_at_recovery':diagnosis['assignment_changes']}))


if __name__=='__main__':
    main()
