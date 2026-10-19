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


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='runs/step1_nonconvex_record_rebuild_v1')
    parser.add_argument('--report-dir',default='reports/step1_nonconvex_record_rebuild_v1')
    args=parser.parse_args()
    output=Path(args.output); report_dir=Path(args.report_dir)
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
