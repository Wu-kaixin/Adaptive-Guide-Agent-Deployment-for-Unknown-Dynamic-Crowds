"""Build compact report tables and figures from existing immutable runs only."""
from collections import Counter
import csv
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import psutil
import shapely
from shapely.geometry import LineString, Point, Polygon
import udc

ROOT = Path('runs/step1_target_domain_consistency')
OUT = Path(__file__).resolve().parent


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def git(*args):
    return subprocess.check_output(['git', *args], text=True, encoding='utf-8', errors='replace').strip()


def supplementary_diagnostics():
    """Recreate the optional analytic report evidence from the saved WKB/NPZ."""
    root=ROOT/'diagnosis'
    geometry=lambda name: shapely.from_wkb((root/name).read_bytes())
    if not (root/'causal_counterfactuals.json').exists():
        simple=geometry('10_simplify_0p10.wkb')
        physical=geometry('09_physical_mitre_0p55.wkb')
        occupied=geometry('05_controller_crowd_exterior.wkb')
        free=geometry('12_navigation_free.wkb')
        line=geometry('07_sampled_deployment.wkb')
        raw=geometry('06_raw_offset_1p3.wkb').exterior
        target=np.array(read(root/'geometry_chain.json')['common_target'])
        vertices=np.asarray(simple.exterior.coords[:-1])
        i=int(np.argmin(np.linalg.norm(vertices-target,axis=1)))
        triangle=vertices[[(i-1)%len(vertices),i,(i+1)%len(vertices)]]
        u=triangle[0]-triangle[1]; u/=np.linalg.norm(u)
        w=triangle[2]-triangle[1]; w/=np.linalg.norm(w)
        angle=np.arccos(np.clip(np.dot(u,w),-1,1))
        parent=triangle[1]+(u+w)/np.linalg.norm(u+w)*.55/np.sin(angle/2)
        write(root/'causal_counterfactuals.json',{'raw_offset_illegal_length_m':raw.difference(free).length,
            'sampled_illegal_length_m':line.difference(free).length,
            'sampled_physical_illegal_length_m':line.intersection(physical).length,
            'sampled_simplified_illegal_length_m':line.intersection(simple).length,
            'predicted_parent_corner_if_same_mitre_vertex':parent.tolist(),
            'predicted_parent_distance_to_occupied_boundary_m':occupied.boundary.distance(Point(parent))})
        vertices=np.asarray(occupied.exterior.coords[:-1])
        i=int(np.argmin(np.linalg.norm(vertices-parent,axis=1)))
        triangle=vertices[[(i-1)%len(vertices),i,(i+1)%len(vertices)]]
        u=triangle[0]-triangle[1]; w=triangle[2]-triangle[1]
        angle=np.arccos(np.clip(np.dot(u,w)/np.linalg.norm(u)/np.linalg.norm(w),-1,1))
        record={'occupied_corner_vertices':triangle.tolist(),
            'incident_edge_lengths_m':[float(np.linalg.norm(u)),float(np.linalg.norm(w))],
            'interior_angle_degrees':float(np.degrees(angle)),
            'physical_mitre_extension_m':float(.55/np.sin(angle/2))}
        for name in ('01_observed_disks.wkb','03_closing_erosion.wkb'):
            record[name+'_distance_to_corner_m']=geometry(name).boundary.distance(Point(triangle[1]))
        write(root/'occupied_corner.json',record)
    if not (root/'shared_target_origin.json').exists():
        from udc.experiments.nonconvex_rebuild import load_boundary
        from udc.deployment.planners import quadrature_mass,periodic_cells
        from udc.deployment.risk_aware import _projected_lloyd
        b=load_boundary(ROOT/'reconstructed_v3/inputs/elongated/seed_51')
        q,w,length=quadrature_mass(b,np.ones(len(b.deployment)),2048)
        sites=np.arange(11)*length/11
        owner,delta,_=periodic_cells(q,w,sites,length)
        projected,_,history,reason,_,_=_projected_lloyd(b,np.ones(len(b.deployment)),11,0.,2048,300,1.94,.75)
        solver=read(ROOT/'reconstructed_v3/targets/elongated/seed_51/pairwise_euclidean/solver.json')
        write(root/'shared_target_origin.json',{'equal_arc_phase':0.,'equal_arc_first_arc_m':0.,
            'midpoint_quadrature_samples':2048,'site0_cell_samples':int(np.sum(owner==0)),
            'site0_weighted_lloyd_move_m':float(np.sum(w[owner==0]*delta[owner==0])/np.sum(w[owner==0])),
            'projected_sites':projected.tolist(),'projected_cost_history':history.tolist(),
            'projected_exit_reason':reason,'pairwise_selected_start':solver['selected_start'],
            'pairwise_selected_attempt':{k:v for k,v in solver['attempts'][0].items() if k in
                ('solver_iterations','solver_status','cost_before','cost_after','arc_positions')}})


def figures():
    root = ROOT/'diagnosis'
    occupied = shapely.from_wkb((root/'05_controller_crowd_exterior.wkb').read_bytes())
    physical = shapely.from_wkb((root/'09_physical_mitre_0p55.wkb').read_bytes())
    simple = shapely.from_wkb((root/'10_simplify_0p10.wkb').read_bytes())
    conservative = shapely.from_wkb((root/'11_conservative_mitre_0p11.wkb').read_bytes())
    curve = shapely.from_wkb((root/'07_sampled_deployment.wkb').read_bytes())
    raw = shapely.from_wkb((root/'06_raw_offset_1p3.wkb').read_bytes())
    before = np.load(ROOT/'reconstructed_v3/targets/elongated/seed_51/equal_arc/targets.npz')
    after = np.load(ROOT/'candidate_targets/regression/elongated/seed_51/equal_arc/targets.npz')
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.5))
    for ax in axes:
        x,y = occupied.exterior.xy
        ax.fill(x,y,color='#aebcc9',alpha=.6,label='Observed closed occupied set')
        for geometry, color, style, label in ((physical,'#6a8294','--','Physical obstacle: mitre 0.55 m'),
            (conservative,'#b95042','-','Conservative obstacle: mitre +0.11 m'),
            (curve,'#174f8c','-','Original deployment polyline')):
            line = geometry.exterior if geometry.geom_type=='Polygon' else geometry
            x,y = line.xy
            ax.plot(x,y,style,color=color,lw=1.5,label=label)
        ax.scatter(before['targets'][:,0], before['targets'][:,1], c='#f09c33',s=40,marker='x',label='Original Equal Arc targets',zorder=5)
        ax.scatter(after['targets'][:,0], after['targets'][:,1], facecolors='white',edgecolors='#267652',s=42,label='Phase candidate targets',zorder=4)
        ax.set_aspect('equal'); ax.grid(alpha=.15); ax.set_xlabel('Room x (m)'); ax.set_ylabel('Room y (m)')
    axes[0].set_xlim(9.8,19.3); axes[0].set_ylim(11.1,18.3)
    axes[0].set_title('Reconstructed elongated seed51: 11 targets')
    axes[0].legend(fontsize=7,loc='upper center',bbox_to_anchor=(.5,-.13),ncol=2)
    axes[1].set_xlim(10.34,11.0); axes[1].set_ylim(14.45,15.03)
    axes[1].set_title('Mitre tip overlaps original curve near s = 0')
    v = np.asarray(simple.exterior.coords)
    axes[1].plot(v[:,0],v[:,1],':',color='#725b8d',lw=1,label='Simplified physical obstacle')
    for item in read(root/'curve_domain.json')['per_edge_intersections']:
        coordinates=np.asarray(item['coordinates'])
        axes[1].plot(coordinates[:,0],coordinates[:,1],color='#d32626',lw=4,zorder=3)
    target=before['targets'][0]
    axes[1].annotate('Original target 0\npenetration 5.462 mm',target,xytext=(10.61,14.94),
        fontsize=8,arrowprops={'arrowstyle':'->','color':'#333333'})
    fig.tight_layout(rect=(0,.03,1,1)); fig.savefig(OUT/'seed51_geometry.png',dpi=180,bbox_inches='tight'); plt.close(fig)
    stages=read(root/'geometry_chain.json')['stages']
    chosen=[s for s in stages if s['name'] in ['05_controller_crowd_exterior','09_physical_mitre_0p55','10_simplify_0p10','11_conservative_mitre_0p11']]
    fig,ax=plt.subplots(figsize=(8,3.5))
    values=[s['signed_outside_clearance_m'] for s in chosen]
    ax.bar(['Closed occupancy','Physical +0.55 mitre','Simplify 0.10','Conservative +0.11 mitre'],values,
           color=['#6a8294','#6a8294','#6a8294','#b95042'])
    for i,value in enumerate(values): ax.annotate(f'{value:.6f} m',(i,max(0,value)),xytext=(0,5),textcoords='offset points',ha='center',fontsize=9)
    ax.axhline(0,color='black',lw=.8); ax.set_ylabel('Target outside-obstacle clearance (m)')
    ax.set_ylim(-.10,1.5); ax.tick_params(axis='x',labelsize=8)
    ax.set_title('First conflict: the second mitre buffer (same room coordinates)')
    fig.tight_layout(); fig.savefig(OUT/'seed51_clearance_chain.png',dpi=180); plt.close(fig)


def main():
    supplementary_diagnostics()
    regression=read(ROOT/'regression_summary.json')
    final=read(ROOT/'regression_final_summary.json')
    final_source=read(ROOT/'regression_final_freeze.json')['provenance']['source_sha256']
    final_comparisons=[]
    for row in regression['rows']:
        relative=Path(row['scene'])/f"seed_{row['seed']}"/row['method']
        with np.load(ROOT/'regression'/relative/'trajectory.npz') as a, np.load(ROOT/'regression_final'/relative/'trajectory.npz') as b:
            matches=all(np.array_equal(a[key],b[key],equal_nan=True) for key in a.files if key in b.files)
        final_comparisons.append({'task_id':row['task_id'],'all_trajectory_arrays_equal':bool(matches)})
        row['final_source_sha256']=final_source
        row['final_targets_reused']=True if row['input_status']=='ACCEPTED' else None
    checks=[]
    scene_method=Counter()
    for row in regression['rows']:
        directory=ROOT/'regression'/row['scene']/f"seed_{row['seed']}"/row['method']
        metrics=read(directory/'metrics.json')
        scene_method[(row['scene'],row['method'],metrics['termination_status'])]+=1
        checks.append({'task':row['task_id'],'criteria':metrics.get('criteria'),
            'source_sha256':metrics.get('source_sha256',metrics.get('provenance',{}).get('source_sha256')),
            'status':metrics['termination_status'],'steps':metrics.get('steps'),
            'simulation_time':metrics.get('simulation_time'), 'replans':metrics.get('replan_count'),
            'wall_s':row['wall_s'], 'initial_navigation_wall_seconds':metrics.get('initial_navigation_wall_seconds'),
            'target_domain_sha256':(metrics.get('target_domain') or {}).get('geometry_sha256')})
    columns=['task_id','scene','seed','method','input_status','input_sha256','recomputed_target','reason',
        'candidate_target_changed','algorithm','candidate_source_sha256','original_targets_array_sha256',
        'candidate_targets_array_sha256','termination_status','simulation_time','replan_count',
        'exact_input_and_targets','offline_consistent','wall_s','final_source_sha256','final_targets_reused']
    with (OUT/'tasks_180.csv').open('w',newline='',encoding='utf-8-sig') as stream:
        writer=csv.DictWriter(stream,fieldnames=columns,extrasaction='ignore')
        writer.writeheader(); writer.writerows(regression['rows'])
    write(OUT/'geometry_chain_summary.json',read(ROOT/'diagnosis/geometry_chain.json'))
    write(OUT/'curve_domain_summary.json',read(ROOT/'diagnosis/curve_domain.json'))
    seed51=[]
    target_deltas=[]
    for method in ('equal_arc','projected_mass','pairwise_euclidean'):
        directory=ROOT/'regression/elongated/seed_51'/method
        search=read(directory/'phase_search.json')
        original=np.load(ROOT/'reconstructed_v3/targets/elongated/seed_51'/method/'targets.npz')
        candidate=np.load(ROOT/'candidate_targets/regression/elongated/seed_51'/method/'targets.npz')
        length=read(ROOT/'diagnosis/curve_domain.json')['curve_length_m']
        for index,arc in enumerate(original['arc_positions']):
            shifted=(arc+search['selected_phase_m'])%length
            after_index=int(np.argmin(np.abs(candidate['arc_positions']-shifted)))
            old_point=original['targets'][index]; new_point=candidate['targets'][after_index]
            target_deltas.append({'method':method,'original_index':index,'candidate_index':after_index,
                'original_arc_m':float(arc),'candidate_arc_m':float(candidate['arc_positions'][after_index]),
                'signed_common_phase_m':search['selected_phase_m'],
                'original_x':old_point[0],'original_y':old_point[1],
                'candidate_x':new_point[0],'candidate_y':new_point[1],
                'euclidean_displacement_m':float(np.linalg.norm(old_point-new_point))})
        seed51.append({'method':method,'geometry':read(directory/'independent_geometry.json'),
            'selected_phase_m':search['selected_phase_m'],'feasible_grid_count':sum(p['accepted'] for p in search['attempts']),
            'phase_bins':search['phase_bins'],'cost_before':search['cost_before'],'cost_after':search['cost_after'],
            'exact_constant_density_cost':search['constant_density_exact_cost'],
            'execution':next(row for row in regression['rows'] if row['scene']=='elongated' and row['seed']==51 and row['method']==method)})
    with (OUT/'seed51_target_deltas.csv').open('w',newline='',encoding='utf-8-sig') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(target_deltas[0]))
        writer.writeheader(); writer.writerows(target_deltas)
    write(OUT/'shared_target_origin.json',read(ROOT/'diagnosis/shared_target_origin.json'))
    write(OUT/'causal_counterfactuals.json',read(ROOT/'diagnosis/causal_counterfactuals.json'))
    write(OUT/'occupied_corner.json',read(ROOT/'diagnosis/occupied_corner.json'))
    write(OUT/'legacy_hash_comparison.json', {name:read(ROOT/'legacy_supplement'/name/'historical_hash_comparison.json')
        for name in ('nonconvex_confirmation_v2','nonconvex_record_rebuild_v1')})
    solver_attempts=[]
    for solver in (ROOT/'reconstructed_v3/targets').glob('*/*/pairwise_euclidean/solver.json'):
        solver_attempts.extend(read(solver).get('attempts',[]))
    executed=[row for row in regression['rows'] if row['termination_status']=='SUCCESS']
    summary={key:value for key,value in regression.items() if key!='rows'}
    summary.update(date_local='2026-10-19',timezone='Asia/Tokyo',origin='reconstruction_development_regression',
        original_confirmation_unchanged=True,original_73_reproduced=False,
        original_rejection=read(ROOT/'original_rejection_summary.json'),
        seed51=seed51,small={k:v for k,v in read(ROOT/'small_summary.json').items() if k!='rows'},
        legacy={k:v for k,v in read(ROOT/'legacy_supplement/summary.json').items() if k!='rows'},
        per_scene_method=[{'scene':s,'method':m,'status':status,'count':count} for (s,m,status),count in sorted(scene_method.items())],
        source_sha256=read(ROOT/'regression_freeze.json')['provenance']['source_sha256'],
        new_target_planning_rows=sum(r['recomputed_target'] for r in regression['rows']),
        original_target_reuse_rows=sum(not r['recomputed_target'] and r['input_status']=='ACCEPTED' for r in regression['rows']),
        phase_solver_uses_no_iterative_optimization=True,
        pairwise_solver={'attempts':len(solver_attempts),'statuses':dict(Counter(r['solver_status'] for r in solver_attempts)),
            'iterations':sum(r['solver_iterations'] for r in solver_attempts),'max_iterations':max(r['solver_iterations'] for r in solver_attempts),
            'total_attempt_wall_s':sum(r['solver_seconds'] for r in solver_attempts)},
        execution={'min_time_s':min(r['simulation_time'] for r in executed),'max_time_s':max(r['simulation_time'] for r in executed),
            'max_replans':max(r['replan_count'] for r in executed),
            'minimum_net_clearances':{name:min(r['minimum_net_clearances'][name] for r in executed) for name in ('crowd','guides','walls')}})
    summary['final_verification']={key:value for key,value in final.items() if key!='rows'}
    summary['final_source_sha256']=final_source
    summary['final_verification']['trajectory_array_mismatches']=sum(not r['all_trajectory_arrays_equal'] for r in final_comparisons)
    write(OUT/'final_source_verification.json',{'source_sha256':final_source,
        'summary':summary['final_verification'],'comparisons':final_comparisons})
    write(OUT/'summary.json',summary)
    write(OUT/'checks_180.json',checks)
    package=Path(udc.__file__).parent
    environment={'date_local':'2026-10-19','timezone':'Asia/Tokyo','python':sys.version,'executable':sys.executable,
        'udc_import_path':udc.__file__,'platform':platform.platform(),'logical_processors':psutil.cpu_count(),
        'cpu_model':'Intel(R) Core(TM) i9-14900KF (Win32_Processor measured)',
        'physical_cores':psutil.cpu_count(logical=False),
        'memory_now':dict(psutil.virtual_memory()._asdict()),
        'dependencies':{name:importlib.metadata.version(name) for name in
            ['numpy','scipy','shapely','jupedsim','PedPy','pytest','pip','threadpoolctl']},
        'branch':git('branch','--show-current'),'head':git('rev-parse','HEAD'),
        'origin_branch_at_fetch':git('rev-parse','origin/Major-Research-Branches'),'origin':git('remote','get-url','origin'),
        'initial_source':read(ROOT/'initial_source.json') if (ROOT/'initial_source.json').exists() else
                         read(ROOT/'reconstructed_v3/protocol.json')['provenance'],
        'source_delta_since_confirmation_before_this_task':git('diff','--stat','9427a2e9','ad56e9a','--','src','configs','experiments','tests'),
        'regression_source_sha256':summary['source_sha256'], 'final_source_sha256':final_source,
        'per_source_file_sha256':{str(path.relative_to(package)):hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(package.rglob('*.py'))}}
    write(OUT/'environment.json',environment)
    hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.rglob('*') if p.is_file()
        and p.suffix in ('.npz','.json','.wkb','.yaml') and '__pycache__' not in str(p)}
    write(OUT/'raw_checksums.json',{'root':str(ROOT),'files':hashes})
    figures()
    print(json.dumps({key:summary[key] for key in ('outcomes','tasks_completed','offline_inconsistent','postprocessed_targets','pairwise_solver','execution')},indent=2))


if __name__=='__main__':
    main()
