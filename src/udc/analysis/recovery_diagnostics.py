"""Offline evidence on the fixed failure frame; uses the shared navigation queries."""
from __future__ import annotations
import argparse
from collections import Counter
import csv
import hashlib
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import maximum_bipartite_matching
from shapely.geometry import Point, Polygon, LineString

from udc.experiments.demand_pilot import read, arrays
from udc.experiments.nonconvex_rebuild import write, load_boundary
from udc.experiments.runner import _atomic_npz
from udc.experiments.provenance import provenance
from udc.experiments.recovery_diagnostics import OUTPUT, lock
from udc.experiments.nonconvex_rebuild import sha
from udc.analysis.demand_confirmation import csv_write
from udc.navigation import paths as p
from udc.safety import Safety, is_safe, path_clearances


def geometry_record(geometry,path):
    path.write_bytes(geometry.wkb)
    parts=[geometry] if geometry.geom_type=='Polygon' else list(getattr(geometry,'geoms',[]))
    return {'wkb_sha256':hashlib.sha256(geometry.wkb).hexdigest(),'type':geometry.geom_type,
        'components':len([g for g in parts if g.geom_type=='Polygon']),'valid':geometry.is_valid,
        'component_areas':[g.area for g in parts if g.geom_type=='Polygon']}


def validate_path(path,start,target,domain,people,room,limits):
    if path is None: return {'accepted':False,'reason':'no path supplied'}
    ends=(np.linalg.norm(path[0]-start)<=p.ENDPOINT_TOLERANCE_M and np.linalg.norm(path[-1]-target)<=p.ENDPOINT_TOLERANCE_M)
    records=[]
    for a,b in zip(path[:-1],path[1:]):
        c=path_clearances(a[None],b[None],people,room)
        records.append({'physical':is_safe(c,limits),'complete_static_segment':p.segment_in_free(a,b,domain),'clearances':c})
    return {'accepted':bool(ends and all(r['physical'] and r['complete_static_segment'] for r in records)),
        'endpoint_match':bool(ends),'segments':records}


def matching_evidence(reachable,cost):
    match=maximum_bipartite_matching(csr_matrix(reachable),perm_type='column')
    missing=np.flatnonzero(match<0)
    target_missing=sorted(set(range(reachable.shape[1]))-set(map(int,match[match>=0])))
    rows,cols=linear_sum_assignment(np.where(reachable,cost,1e12))
    # Alternating reachability from unmatched sources yields a Hall witness.
    left=set(map(int,missing)); right=set(); changed=True
    while changed:
        changed=False
        for i in list(left):
            for j in np.flatnonzero(reachable[i]):
                if int(j) not in right: right.add(int(j)); changed=True
        for i,j in enumerate(match):
            if int(j) in right and i not in left: left.add(i); changed=True
    return {'maximum_size':int(np.sum(match>=0)),'source_to_target':match.tolist(),
        'unmatched_sources':missing.tolist(),'unmatched_targets':target_missing,
        'hungarian_rows':rows.tolist(),'hungarian_targets':cols.tolist(),
        'hungarian_all_finite':bool(reachable[rows,cols].all()),
        'hall_witness':{'sources':sorted(left),'neighbors':sorted(right),'deficit':len(left)-len(right),
            'scope':'this finite verified-query graph only; no continuous-environment impossibility claim'}}


def matrix(output,variant='A'):
    root=Path(output).resolve(); lockvalue=lock(root,full=True)
    dest=root/('matrix_'+variant)
    if dest.exists(): raise FileExistsError('Preserve each matrix version')
    dest.mkdir(parents=True)
    row=next(r for r in read(root/'manifest_360.json') if r['task_id']==lockvalue['spec']['focal'])
    original=arrays(Path(row['original_result'])/'trajectory.npz'); starts=original['positions'][-1]; targets=original['targets']
    people=original['crowd_positions']; room=original['room']; limits=Safety(.75,.75,.55,1.,1e-7)
    b=load_boundary(Path(row['physical'])); domain=p.navigation_domain(room,.55,Polygon(b.crowd),.55)
    geometries={name:geometry_record(value,dest/(name+'.wkb')) for name,value in
        [('static_free',domain.free),('physical_free',domain.physical_free),('conservative_obstacle',domain.conservative_obstacle)]}
    state=read(root/'A_baseline'/row['task_id']/'terminal_control_state.json')
    graph=p._visibility_graph(domain.free,domain.conservative_obstacle)
    endpoint=[]; prepared=[]
    for i,x in enumerate(starts):
        ready=p._prepare_start(x,domain,starts,i,people,limits,room); prepared.append(ready)
        point=Point(x); near=np.asarray(domain.conservative_obstacle.boundary.interpolate(domain.conservative_obstacle.boundary.project(point)).coords[0])
        normal=p._outward_free_direction(near,domain); normal_node=None if normal is None else near+p.BOUNDARY_NODE_NUDGE_M*normal
        endpoint.append({'kind':'source','id':i,'position':x.tolist(),'physical_free':domain.physical_free.covers(point),
            'signed_distance_m':domain.signed_free_distance(x),'covered':domain.free.covers(point),'component':domain.component_index(x),
            'prepared':ready is not None,'planner_node':None if ready is None else ready[0].tolist(),
            'true_prefix':None if ready is None or ready[1] is None else ready[1].tolist(),
            'projection':near.tolist(),'projection_delta':(near-x).tolist(),'projection_delta_norm':float(np.linalg.norm(near-x)),
            'normal_node':None if normal_node is None else normal_node.tolist(),
            'normal_node_covered':False if normal_node is None else domain.free.covers(Point(normal_node)),
            'normal_prefix_accepted':False if normal_node is None else p.segment_in_free(x,normal_node,domain),
            'visible_static_nodes':None if ready is None else sum(domain.free.covers(LineString([ready[0],node]))
                and not LineString([ready[0],node]).crosses(domain.conservative_obstacle) for node in graph[0])})
    ready_targets=[]
    for j,x in enumerate(targets):
        ready=p._prepare_start(x,domain); ready_targets.append(ready)
        endpoint.append({'kind':'target','id':j,'position':x.tolist(),'physical_free':domain.physical_free.covers(Point(x)),
            'signed_distance_m':domain.signed_free_distance(x),'covered':domain.free.covers(Point(x)),
            'component':domain.component_index(x),'prepared':ready is not None,
            'occupants':[{'guide':int(i),'distance_m':float(np.linalg.norm(g-x))} for i,g in enumerate(starts) if np.linalg.norm(g-x)<.75]})
    records=[]; payload={}; cost=np.full((len(starts),len(targets)),np.inf); queried=np.zeros(cost.shape,bool)
    for i,x in enumerate(starts):
        for j,t in enumerate(targets):
            record={'guide':i,'target':j,'status':'not_queried','queried':False,'length':None}
            try:
                if not domain.physical_free.covers(Point(x)): record['status']='invalid_start'
                elif prepared[i] is None: record['status']='start_not_entered'
                elif ready_targets[j] is None: record['status']='invalid_target'
                elif (domain.component_index(prepared[i][0]) is not None and domain.component_index(ready_targets[j][0]) is not None
                      and domain.component_index(prepared[i][0])!=domain.component_index(ready_targets[j][0])):
                    record['status']='different_conservative_components'
                else:
                    queried[i,j]=True; record['queried']=True
                    path,length=p._shortest(prepared[i][0],ready_targets[j][0],domain.free,domain.conservative_obstacle,graph=graph)
                    if path is None: record['status']='search_not_found'
                    else:
                        if prepared[i][1] is not None:
                            path=np.vstack([prepared[i][1],path]); length+=np.linalg.norm(path[1]-path[0])
                        if ready_targets[j][1] is not None:
                            path=np.vstack([path,targets[j]]); length+=np.linalg.norm(path[-1]-path[-2])
                        validated=validate_path(path,x,t,domain,people,room,limits)
                        record.update(status='validated_path' if validated['accepted'] else 'candidate_validation_rejected',
                                      length=float(length),validation=validated)
                        payload[f'{i}_{j}']=path.tolist()
                        if validated['accepted']: cost[i,j]=length
            except Exception as error: record.update(status='program_exception',error=repr(error))
            records.append(record)
    reachable=np.isfinite(cost); matching=matching_evidence(reachable,cost)
    assignment=np.asarray(state['assignment']); suffixes=[]; temporary=[]
    for i,x in enumerate(starts):
        path=np.asarray(state['paths'][str(i)]); k=state['waypoints'][str(i)]
        suffix=np.vstack([x,path[k:]])
        v=validate_path(suffix,x,targets[assignment[i]],domain,people,room,limits)
        suffixes.append({'guide':i,'target':int(assignment[i]),'waypoint':k,'path':suffix.tolist(),'validation':v})
        free,extra=p._other_guide_space(domain,starts,i,.75)
        geometry=geometry_record(free,dest/f'temporary_guide_{i}_free.wkb')
        j=int(assignment[i]); prepared_i=prepared[i]; target=ready_targets[j]
        record={'guide':i,'target':j,'geometry':geometry,'original_route_loop_entered':i<=8,
            'original_shortest_queried':i<8,'static_status':records[i*len(targets)+j]['status']}
        if prepared_i is None: record['diagnostic_status']='start_not_entered'
        elif not p._within_free(free,prepared_i[0]): record['diagnostic_status']='temporary_start_occupied'
        elif not p._within_free(free,target[0]): record['diagnostic_status']='temporary_target_occupied'
        else:
            c1,c2=p._component(free,prepared_i[0]),p._component(free,target[0])
            record['components']=[c1,c2]
            if c1 is not None and c2 is not None and c1!=c2: record['diagnostic_status']='different_temporary_components'
            else:
                path,length=p._shortest(prepared_i[0],target[0],free,domain.conservative_obstacle,extra)
                record['diagnostic_status']='search_not_found' if path is None else 'found_path'
                if path is not None:
                    if prepared_i[1] is not None: path=np.vstack([prepared_i[1],path])
                    record['path']=path.tolist(); record['validation']=validate_path(path,x,targets[j],domain,people,room,limits)
        record['target_occupants']=p._occupation(starts,targets[j],i,.75)
        temporary.append(record)
    log=read(root/'A_baseline'/row['task_id']/'observation_log.json')
    static_hash=hashlib.sha256(domain.free.wkb).hexdigest()
    result={'variant':variant,'source':provenance({},None,'offline_matrix',23),'domain_sha256':domain.geometry_sha256,
        'parameters':{'wall_margin':.55,'crowd_margin':.55,'simplify':.10,'extra_margin':.11,'endpoint_tolerance':1e-7,'node_nudge':1e-8},
        'geometry':geometries,'source_physical_safety':path_clearances(starts,starts,people,room),
        'graph_nodes':len(graph[0]),'graph_edges':sum(map(len,graph[1]))//2,'graph_built_once_for_529_pairs':True,
        'statuses':dict(Counter(r['status'] for r in records)),'matching':matching,
        'original_assignment_statuses':[records[i*23+int(assignment[i])]['status'] for i in range(23)],
        'all_installed_suffixes_valid':all(v['validation']['accepted'] for v in suffixes),
        'actual_reassignment_static_graph_verified':any(e['kind']=='graph_build' and e['scope']=='reachable_reassignment'
            and e['free_wkb_sha256']==static_hash for e in log),
        'call_local_graph_no_cross_query_graph_cache':True}
    write(dest/'summary.json',result); write(dest/'endpoints_46.json',endpoint)
    write(dest/'matrix_529.json',records); write(dest/'paths.json',payload)
    write(dest/'temporary_assigned_23.json',temporary); write(dest/'installed_suffixes_23.json',suffixes)
    _atomic_npz(dest/'matrix.npz',cost=cost,reachable=reachable,queried=queried,starts=starts,targets=targets,assignment=assignment,
                graph_nodes=np.asarray(graph[0]))
    with (dest/'matrix_529.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=['guide','target','status','queried','length']); writer.writeheader()
        writer.writerows({k:r[k] for k in writer.fieldnames} for r in records)
    print({k:result[k] for k in ('variant','statuses','matching','all_installed_suffixes_valid','actual_reassignment_static_graph_verified')})


def main():
    cli=argparse.ArgumentParser(description=__doc__); cli.add_argument('--output',default=OUTPUT); cli.add_argument('--variant',default='A')
    cli.add_argument('--stage',choices=['matrix','summarize'],default='matrix')
    cli.add_argument('--report',default='reports/step1_resource_recovery_diagnostics_v1')
    args=cli.parse_args()
    if args.stage=='matrix': matrix(args.output,args.variant)
    else: summarize(args.output,args.report)


def summarize(output,report):
    root=Path(output).resolve(); report=Path(report); report.mkdir(parents=True,exist_ok=True)
    value=lock(root,full=True); frozen=read(root/'candidate_freeze.json')
    if provenance({},None,'fixed_n',None)['source_sha256']!=frozen['source']['source_sha256']:
        raise RuntimeError('Use frozen candidate analysis; retain source provenance')
    checks=read(root/'all_independent_checks_360.json'); indexed={r['task_id']:r for r in checks}
    flat=[]
    for row in read(root/'manifest_360.json'):
        record={k:row[k] for k in ('task_id','scene','seed','count','method','original_status','target_valid')}
        record.update(new_status='READONLY_COMMON_REJECTION',original_physical_success=None,new_physical_success=None,
            executed=False,original_time_s=None,new_time_s=None,cutoff_margin_s=None,total_travel_m=None,
            per_guide_travel_m=None,recoveries=None,original_recoveries=None,trajectory_identical=None,
            common_prefix_exact=None,first_changed_common_position_frame=None,first_unshared_frame=None,
            targets_exact=None,input_exact=None,assignment_trace_ok=None,minimum_crowd_m=None,minimum_guides_m=None,
            minimum_walls_m=None,criteria_consistent=None,diagnostics_consistent=None,runner_wall_s=None,
            old_275_success_time_difference_s=None,old_275_success_travel_difference_m=None)
        if row['target_valid']:
            directory=root/'full_candidate'/row['task_id']; summary=read(directory/'summary.json')
            metrics=read(directory/'metrics.json'); actual=arrays(directory/'trajectory.npz')
            old=arrays(Path(row['original_result'])/'trajectory.npz'); oldm=read(Path(row['original_result'])/'metrics.json')
            motion=summary['motion']; length=min(len(actual['positions']),len(old['positions']))
            identical=all(np.array_equal(actual[k],old[k]) for k in
                ('positions','nominal_velocity','safe_velocity','waypoint_index','targets','assignment'))
            record.update(new_status=summary['status'],original_physical_success=row['original_physical_success'],
                new_physical_success=indexed[row['task_id']]['success'],executed=True,
                original_time_s=float(old['time'][-1]),new_time_s=motion['simulation_time_s'],
                cutoff_margin_s=40-motion['simulation_time_s'] if indexed[row['task_id']]['success'] else None,
                total_travel_m=motion['actual_trajectory_length_m'],per_guide_travel_m=motion['actual_trajectory_length_m']/row['count'],
                recoveries=summary['recoveries'],original_recoveries=oldm['replan_count'],trajectory_identical=identical,
                common_prefix_exact=all(r['exact'] for r in summary['prefix'].values()),
                first_changed_common_position_frame=summary['first_position_difference_frame'],
                first_unshared_frame=length if len(actual['positions'])!=len(old['positions']) else None,
                targets_exact=summary['targets_exact'],input_exact=summary['input_exact'],
                assignment_trace_ok=summary['assignment_trace']['accepted'],
                minimum_crowd_m=motion['minimum_net_clearances_m']['crowd'],
                minimum_guides_m=motion['minimum_net_clearances_m']['guides'],minimum_walls_m=motion['minimum_net_clearances_m']['walls'],
                criteria_consistent=indexed[row['task_id']]['criteria_consistent'],diagnostics_consistent=indexed[row['task_id']]['diagnostics_consistent'],
                runner_wall_s=metrics['wall_time'])
            if row['original_physical_success'] and record['new_physical_success']:
                old_travel=float(np.linalg.norm(np.diff(old['positions'],axis=0),axis=2).sum())
                record.update(old_275_success_time_difference_s=motion['simulation_time_s']-float(old['time'][-1]),
                    old_275_success_travel_difference_m=motion['actual_trajectory_length_m']-old_travel)
        flat.append(record)
    executed=[r for r in flat if r['executed']]; original_success=[r for r in executed if r['original_physical_success']]
    focal=next(r for r in flat if r['task_id']==value['spec']['focal'])
    summary={'source':frozen['source'],'original_accounting_unchanged':{'SUCCESS':273,'common_rejections':84,'EXPERIMENT_ERROR':2,'actual_scientific_failure':1},
        'all_rows':len(flat),'fixed_target_executions':len(executed),'readonly_common_rejections':len(flat)-len(executed),
        'new_outcomes':dict(Counter(r['new_status'] for r in flat)),
        'original_physical_success_n':len(original_success),'original_success_degraded':[r['task_id'] for r in original_success if not r['new_physical_success']],
        'identical_original_success_trajectories':sum(r['trajectory_identical'] for r in original_success),
        'changed_original_success_trajectories':[r for r in original_success if not r['trajectory_identical']],
        'focal':focal,'prior_program_errors':[r for r in executed if r['original_status']=='EXPERIMENT_ERROR'],
        'all_new_targets_exact':all(r['targets_exact'] for r in executed),'all_new_inputs_exact':all(r['input_exact'] for r in executed),
        'all_assignment_traces_valid':all(r['assignment_trace_ok'] for r in executed),
        'independent_inconsistent':sum(any(c.get(k) is False for k in ('criteria_consistent','diagnostics_consistent','success_consistent')) for c in checks),
        'unsafe_intervals':sum(c.get('unsafe_interval_count',0) for c in checks),
        'safety_minima':{key:min(r['minimum_'+key+'_m'] for r in executed) for key in ('crowd','guides','walls')},
        'recovery_distribution':dict(Counter(r['recoveries'] for r in executed)),
        'maximum_recoveries':max(r['recoveries'] for r in executed),'slowest_success':max((r for r in executed if r['new_physical_success']),key=lambda r:r['new_time_s']),
        'costs':{k:{'mean':float(np.mean([r[k] for r in executed])),'minimum':min(r[k] for r in executed),'maximum':max(r[k] for r in executed)}
                 for k in ('new_time_s','total_travel_m','per_guide_travel_m','runner_wall_s')},
        'old_275_success_cost_differences':{k:{'mean':float(np.mean([r[k] for r in original_success])),
            'maximum_absolute':max(abs(r[k]) for r in original_success)} for k in
            ('old_275_success_time_difference_s','old_275_success_travel_difference_m')},
        'full_summary':read(root/'full_summary.json'),'small_summary':read(root/'small_summary.json')}
    csv_write(report/'fixed_target_ledger_360.csv',flat); write(report/'analysis.json',summary)
    for name in ('protocol.json','manifest_360.json','candidate_freeze.json','source_transition.json','repair_decision.json',
        'B_hypothesis.json','B_interventions.json','causal_comparison.json','B_shared_equivalence.json',
        'all_independent_checks_360.json','small_gate.json','small_summary.json','full_summary.json'):
        write(report/name,read(root/name))
    for variant in ('A','B_counterfactual'):
        source=root/('matrix_'+variant)
        for name in ('summary.json','endpoints_46.json','matrix_529.json','temporary_assigned_23.json','installed_suffixes_23.json'):
            write(report/(variant+'_'+name),read(source/name))
        (report/(variant+'_matrix_529.csv')).write_text((source/'matrix_529.csv').read_text(encoding='utf-8-sig'),encoding='utf-8-sig')
    diagrams(root,report)
    lock(root,full=True)
    write(report/'raw_checksums.json',{'new_run_files':{p.relative_to(root).as_posix():sha(p) for p in sorted(root.rglob('*')) if p.is_file()},
        'historical_references_verified':len(value['protected']),'source_transition_separately_verified':True,'original_manifest_sha256':value['manifest_sha256']})
    print({k:summary[k] for k in ('fixed_target_executions','new_outcomes','original_success_degraded','identical_original_success_trajectories','unsafe_intervals')})


def diagrams(root,report):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    fig,axes=plt.subplots(1,2,figsize=(11,5),constrained_layout=True)
    for ax,variant,label in zip(axes,('A','B_counterfactual'),('A: 23 edges not queried','B: 529 paths verified')):
        data=arrays(root/('matrix_'+variant)/'matrix.npz')
        # Grey means never queried, not unreachable. Every queried A edge passed.
        ax.imshow(data['reachable'],vmin=0,vmax=1,cmap=ListedColormap(['#d0d0d0','#236fa1']),origin='lower')
        ax.set(title=label,xlabel='frozen target ID',ylabel='real Guide ID',xticks=[0,8,12,16,22],yticks=[0,8,9,16,22])
    fig.suptitle('Fixed frame at 20.6 s: finite validated query graph')
    fig.savefig(report/'path_matrices.png',dpi=170); plt.close(fig)
    A=read(root/'matrix_A/summary.json'); data=arrays(root/'matrix_A/matrix.npz')
    from shapely import from_wkb
    obstacle=from_wkb((root/'matrix_A/conservative_obstacle.wkb').read_bytes())
    fig,ax=plt.subplots(figsize=(9,6),constrained_layout=True)
    ring=np.asarray(obstacle.exterior.coords); ax.fill(ring[:,0],ring[:,1],alpha=.2,color='grey',label='conservative obstacle')
    ax.scatter(data['starts'][:,0],data['starts'][:,1],label='actual guides',s=28)
    ax.scatter(data['targets'][:,0],data['targets'][:,1],marker='x',label='frozen targets',s=25)
    for i,x in enumerate(data['starts']): ax.annotate(str(i),x,fontsize=7,xytext=(2,3),textcoords='offset points')
    g=data['starts'][9]; t=data['targets'][12]
    ax.plot([g[0],t[0]],[g[1],t[1]],'r--',label='Guide9 to target12 occupation')
    ax.set(aspect='equal',xlabel='x (m)',ylabel='y (m)',title='Original failing frame; fixed targets and true positions')
    ax.legend(fontsize=8); fig.savefig(report/'failure_frame.png',dpi=170); plt.close(fig)


if __name__=='__main__': main()
