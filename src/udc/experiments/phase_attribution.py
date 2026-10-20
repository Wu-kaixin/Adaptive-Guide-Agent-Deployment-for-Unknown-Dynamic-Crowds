"""Read-only phase attribution on frozen target layouts, then execute main P3.

No crowd generation, density generation, Lloyd iteration or control-law change.
All original files are hash-checked and referenced, never rewritten.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter
import traceback

import numpy as np
import shapely
from shapely.geometry import Polygon
import yaml
from threadpoolctl import threadpool_info, threadpool_limits

from udc.boundary import Boundary
from udc.deployment.planners import _interpolate, periodic_gaps, quadrature_mass, coverage_statistics
from udc.evaluation.acceptance import evaluate
from udc.evaluation.comparison import common_motion_metrics
from udc.experiments.demand_pilot import arrays, read
from udc.experiments.nonconvex_rebuild import (array_hash, load_boundary, load_plan,
    rejected_result, sha, write, _official_monitor)
from udc.experiments.provenance import provenance
from udc.experiments.runner import experiment, _atomic_npz, _atomic_text, STATUSES
from udc.experiments.target_domain import independent_geometry
from udc.navigation import navigation_domain, navigate_and_assign
from udc.navigation.paths import target_domain_check
from udc.safety import Safety


SPEC = 'configs/step1/nonconvex_phase_attribution_v1.yaml'
OUTPUT = 'runs/step1_nonconvex_phase_attribution_v1'


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def phase_grid(length, bins=256, offset=0.):
    index=np.arange(bins,dtype=float)+offset
    return np.where(index<=bins//2,index,index-bins)*length/bins


def shifted_layout(boundary, plan, phase):
    """Stable saved-index mapping; sorting is a cyclic permutation, not motion."""
    canonical=np.argsort(plan.arc_positions % boundary.length,kind='stable')
    sites=np.asarray(plan.arc_positions)[canonical] % boundary.length
    shifted=(sites+phase) % boundary.length
    order=np.argsort(shifted,kind='stable')
    arcs=shifted[order]
    origins=canonical[order]
    expected=periodic_gaps(sites,boundary.length)[order]
    return arcs,_interpolate(boundary,arcs),origins,expected


def score_sites(boundary, field, sites, samples=2048):
    """Keep rho and the hotspot window frozen; only midpoint integration varies."""
    q,mass,length=quadrature_mass(boundary,field['rho'],samples)
    result={'demand':coverage_statistics(q,mass,sites,length,np.inf)['weighted_cost'],
            'excess':None,'background_mass':None,'background_arc':None}
    if field['excess_mass'].sum()>0:
        excess=quadrature_mass(boundary,field['excess_rho'],samples)[1]
        background=np.maximum(mass-excess,0.)
        # Original q bins partition [0,L); refining evaluation never redefines a window.
        indices=np.minimum((q/length*len(field['excess_window'])).astype(int),len(field['excess_window'])-1)
        outside=~field['excess_window'][indices]
        for key,weights in [('excess',excess),('background_mass',background),('background_arc',outside.astype(float))]:
            result[key]=coverage_statistics(q,weights,sites,length,np.inf)['weighted_cost']
    return result


def candidate_cache(boundary,plan,domain,field,identity,*,offset=0.,bins=256,gap_limit=1.94,separation=.75):
    """One finite grid reused by P1/P2/P3; every candidate independently checked."""
    started=perf_counter()
    line=Polygon(boundary.deployment).exterior
    phases=phase_grid(boundary.length,bins,offset)
    records=[]; all_arcs=[]; all_points=[]; all_origins=[]; all_distances=[]
    for index,phase in enumerate(phases):
        arcs,points,origins,expected=shifted_layout(boundary,plan,float(phase))
        projected=shapely.line_locate_point(line,shapely.points(points))
        # The independent geometric projection preserves the known cyclic order,
        # with a seam projection at L normalised to zero.
        independent_arcs=np.sort(projected % boundary.length)
        gaps=periodic_gaps(arcs,boundary.length)
        independent_gaps=periodic_gaps(independent_arcs,boundary.length)
        pattern_error=float(np.max(np.abs(gaps-expected)))
        projection_pattern_error=min(float(np.max(np.abs(independent_gaps-np.roll(expected,k))))
                                     for k in range(len(arcs)))
        curve_error=float(np.max(shapely.distance(line,shapely.points(points))))
        distances=np.linalg.norm(points[:,None]-points[None,:],axis=2)
        minimum=float(np.min(distances+np.eye(len(points))*1e9))
        admission=target_domain_check(domain,points,arcs)
        count_ok=len(points)==len(plan.positions)
        arc_ok=bool(gaps.min()>=separation-1e-12 and gaps.max()<=gap_limit+1e-9)
        independent_arc_ok=bool(independent_gaps.min()>=separation-1e-12 and independent_gaps.max()<=gap_limit+1e-9)
        accepted=bool(count_ok and arc_ok and independent_arc_ok and minimum>=separation
            and admission['accepted'] and curve_error<=1e-7 and pattern_error<=1e-7 and projection_pattern_error<=1e-7)
        records.append({'index':index,'relative_phase_m':float(phase),'accepted':accepted,
            'count_ok':count_ok,'arc_ok':arc_ok,'independent_arc_ok':independent_arc_ok,
            'pair_ok':minimum>=separation,'domain_ok':admission['accepted'],
            'min_gap_m':float(gaps.min()),'max_gap_m':float(gaps.max()),'seam_gap_m':float(gaps[-1]),
            'seam_origin_index':int(origins[-1]),'cyclic_gap_error_m':pattern_error,
            'projected_cyclic_gap_error_m':projection_pattern_error,'curve_error_m':curve_error,
            'min_pair_m':minimum,'min_domain_signed_m':min(t['signed_free_distance_m'] for t in admission['targets']),
            'invalid_domain_indices':admission['invalid_target_indices'],
            'score':score_sites(boundary,field,arcs)})
        all_arcs.append(arcs); all_points.append(points); all_origins.append(origins); all_distances.append(distances)
    key={**identity,'grid_offset_bins':offset,'phase_bins':bins,'gap_limit':gap_limit,'separation':separation}
    return {'identity':key,'cache_key':digest(key),'candidate_count':bins,
        'feasible_count':sum(r['accepted'] for r in records),'records':records,
        'grid_origin_relative_m':offset*boundary.length/bins,'wall_s':perf_counter()-started}, dict(
        phases=phases,arc_positions=np.array(all_arcs),positions=np.array(all_points),
        origin_indices=np.array(all_origins),all_pair_distances=np.array(all_distances))


def choose_policy(policy,original_domain_ok,cache):
    if policy=='P0' or (policy in ('P1','P2') and original_domain_ok):
        return None  # None means the saved original; no interpolation or sorting.
    feasible=[r for r in cache['records'] if r['accepted']]
    if not feasible:
        return -1
    if policy=='P1':
        key=lambda r:(abs(r['relative_phase_m']),r['relative_phase_m'])
    else:
        key=lambda r:(r['score']['demand'],abs(r['relative_phase_m']),r['relative_phase_m'])
    return min(feasible,key=key)['index']


def verify_old(spec):
    """Check previous files, not its total-source lock (this adds a new module)."""
    source=Path(spec['source_run']).resolve()
    report=Path(spec['source_report']).resolve()
    checks=read(report/'raw_checksums.json')
    files={str(source/k):v for k,v in checks['run_files'].items()}
    files.update(checks['frozen_source_references'])
    old_source=read(report/'execution_source.json')
    files.update({str(Path(k).resolve()):v for k,v in old_source['file_hashes'].items()})
    files.update({str(p):sha(p) for p in report.rglob('*') if p.is_file() and '__pycache__' not in p.parts})
    missing=[p for p in files if not Path(p).is_file()]
    if missing:
        raise FileNotFoundError('Missing original evidence; no reconstruction: '+json.dumps(missing))
    changed=[p for p,v in files.items() if sha(p)!=v]
    if changed:
        raise RuntimeError('Original evidence/source differs: '+json.dumps(changed))
    return source,report,files


def freeze(output,spec_path=SPEC):
    root=Path(output).resolve()
    if root.exists():
        raise FileExistsError('Use a new output directory')
    spec=yaml.safe_load(Path(spec_path).read_text(encoding='utf-8'))
    if (spec['phase_bins']!=256 or spec['grids']!={'main':{'offset_bins':0.,'contains_zero':True},
        'half':{'offset_bins':.5,'contains_zero':False}} or spec['methods']!=['equal_arc','projected_mass']
        or spec['policies']!=['P0','P1','P2','P3'] or spec['fine_quadrature_samples']!=[4096,8192]):
        raise ValueError('Changed candidate/evaluation budgets need a new protocol')
    source,report,files=verify_old(spec)
    old=read(source/'protocol.json')
    rows=[r for r in read(source/'manifest.json')['tasks'] if r['method'] in spec['methods']]
    assert len(rows)==24 and len({r['condition_key'] for r in rows})==12
    root.mkdir(parents=True)
    files[str(Path(spec_path).resolve())]=sha(spec_path)
    write(root/'protocol.json',{'spec':spec,'old_protocol':old,'references':files,
        'provenance':provenance({},None,'fixed_n',None),'source_run':str(source),
        'old_files_before':len(files),'no_new_person_draw_or_lloyd':True})
    write(root/'layout_manifest_24.json',{'rows':rows,'main_policy_rows':96,'sensitivity_P3_rows':24})
    print(json.dumps({'frozen_layouts':24,'main_policy_rows':96,'sensitivity_P3_rows':24,
                      'protected_references':len(files)}),flush=True)


def locked(root):
    lock=read(root/'protocol.json')
    if provenance({},None,'fixed_n',None)['source_sha256']!=lock['provenance']['source_sha256']:
        raise RuntimeError('Source changed after freeze')
    for path,value in lock['references'].items():
        if sha(path)!=value:
            raise RuntimeError('Protected source/input/report changed: '+path)
    return lock


def condition_data(lock,row):
    source=Path(lock['source_run'])
    old=lock['old_protocol']
    ref=old['sources'][row['physical_key']]
    data=arrays(Path(ref['source_cell'])/'input.npz')
    field=arrays(source/'conditions'/row['condition_key']/'demand.npz')
    data['demand']=field['demand']
    boundary=load_boundary(source/'physical'/row['physical_key'])
    domain=navigation_domain(data['room'],.55,Polygon(boundary.crowd),.55)
    cfgpath=source/'conditions'/row['condition_key']/'input.yaml'
    return data,field,boundary,domain,cfgpath


def mass_invariance(lock,root):
    source=Path(lock['source_run']); records=[]
    for condition,c in lock['old_protocol']['conditions'].items():
        b=load_boundary(source/'physical'/c['physical_key'])
        plan=load_plan(source/'plans'/(condition+'__mass_cvt')/'original')
        canonical=np.argsort(plan.arc_positions % b.length)
        gaps=periodic_gaps(plan.arc_positions[canonical] % b.length,b.length)
        checks=[]
        for fraction in lock['spec']['mass_invariance_check_phase_fractions']:
            arcs,_,origins,expected=shifted_layout(b,plan,fraction*b.length)
            after=periodic_gaps(arcs,b.length)
            checks.append({'phase_fraction':fraction,'phase_m':fraction*b.length,
                'origin_indices':origins.tolist(),'gaps_m':after.tolist(),
                'max_pattern_error_m':float(np.max(np.abs(after-expected))),
                'seam_gap_m':float(after[-1]),'seam_origin_index':int(origins[-1])})
        assert gaps.max()>1.94+1e-9
        assert all(c['max_pattern_error_m']<1e-7 for c in checks)
        records.append({'condition_key':condition,'original_arc_positions_m':plan.arc_positions.tolist(),
            'original_gaps_m':gaps.tolist(),'minimum_gap_m':float(gaps.min()),
            'maximum_gap_m':float(gaps.max()),'violation_m':float(gaps.max()-1.94),
            'checks':checks,'exhaustive_phase_search_performed':False,'executed':False,
            'identity':'g_i(phi)=g_original[cyclic_origin_index_i]; the closing gap is the last permuted element',
            'scope':'translation cannot fix this saved layout; no statement about other target layouts'})
    write(root/'mass_gap_invariance.json',records)


def analyze(output):
    root=Path(output).resolve(); lock=locked(root); source=Path(lock['source_run'])
    if (root/'planning').exists():
        raise FileExistsError('Offline policy analysis is immutable; do not replace a completed attempt')
    (root/'planning').mkdir()
    mass_invariance(lock,root)
    main_rows=[]; half_rows=[]; cache_refs={}; p3=[]; navigation_cache={}; p2_checks=[]
    started=perf_counter()
    for row in read(root/'layout_manifest_24.json')['rows']:
        data,field,b,domain,cfgpath=condition_data(lock,row)
        plan=load_plan(source/'plans'/row['task_id']/'original')
        if len(plan.positions)!=row['count'] or not np.array_equal(plan.positions,_interpolate(b,plan.arc_positions)):
            raise RuntimeError('Saved raw layout count or full-precision interpolation differs')
        original_domain=target_domain_check(domain,plan.positions,plan.arc_positions)
        identity={'condition_key':row['condition_key'],'input_sha256':row['input_sha256'],
            'physical_sha256':row['physical_sha256'],'curve_sha256':array_hash(crowd=b.crowd,deployment=b.deployment,
                arc=b.arc,length=np.array(b.length)),'layout_sha256':array_hash(positions=plan.positions,arcs=plan.arc_positions),
            'density_sha256':array_hash(**field),'domain_sha256':domain.geometry_sha256}
        for grid in ('main','half'):
            cache,payload=candidate_cache(b,plan,domain,field,identity,offset=lock['spec']['grids'][grid]['offset_bins'])
            directory=root/'candidates'/row['task_id']/grid
            directory.mkdir(parents=True)
            write(directory/'candidates.json',cache)
            _atomic_npz(directory/'candidates.npz',**payload)
            cache_refs[row['task_id']+'__'+grid]={'cache_key':cache['cache_key'],'identity':identity,
                'candidate_count':cache['candidate_count'],'feasible_count':cache['feasible_count'],
                'wall_s':cache['wall_s'],'json_sha256':sha(directory/'candidates.json'),
                'npz_sha256':sha(directory/'candidates.npz')}
            for policy in (lock['spec']['policies'] if grid=='main' else ['P3']):
                index=choose_policy(policy,original_domain['accepted'],cache)
                record={**row,'policy':policy,'grid':grid,'cache_key':cache['cache_key'],
                    'candidate_count':cache['candidate_count'],'feasible_count':cache['feasible_count'],
                    'selected_index':index,'relative_phase_m':0. if index is None else None,
                    'score':None,'fine_scores':{},'target_geometry_ok':False,'initial_paths_ok':False,
                    'status':'PLAN_SEARCH_EXHAUSTED','path_ref':None,'original_domain_ok':original_domain['accepted']}
                rid=row['task_id']+'__'+grid+'__'+policy
                destination=root/'planning'/rid
                destination.mkdir()
                if index!=-1:
                    arcs,points,origins=(plan.arc_positions,plan.positions,np.arange(row['count'])) if index is None else (
                        payload['arc_positions'][index],payload['positions'][index],payload['origin_indices'][index])
                    record['relative_phase_m']=0. if index is None else cache['records'][index]['relative_phase_m']
                    record['score']=score_sites(b,field,arcs)
                    record['fine_scores']={str(n):score_sites(b,field,arcs,n) for n in lock['spec']['fine_quadrature_samples']}
                    candidate=replace(plan,arc_positions=arcs,positions=points,max_gap=float(periodic_gaps(arcs,b.length).max()),
                        method=row['method']+(lock['spec']['method_label_suffix'] if policy=='P3' else '+'+policy),
                        cost=np.array([record['score']['demand']]),iterations=0,exit_reason='SAVED_LAYOUT_PHASE_POLICY')
                    geometry=independent_geometry(b,plan,candidate,domain,lock['old_protocol']['base'])
                    record['geometry']=geometry
                    record['target_geometry_ok']=geometry['accepted']
                    _atomic_npz(destination/'targets.npz',targets=points,arc_positions=arcs,origin_indices=origins)
                    record['target_sha256']=array_hash(targets=points,arc_positions=arcs)
                    record['status']='TARGET_DOMAIN_INVALID' if not geometry['accepted'] else 'PLANNED'
                    if geometry['accepted']:
                        navkey=digest({'identity':identity,'target_sha256':record['target_sha256']})
                        if navkey not in navigation_cache:
                            t=perf_counter()
                            try:
                                nav=navigate_and_assign(data['guides'],points,data['room'],.55,Polygon(b.crowd),.55)
                                navdir=root/'navigation'/navkey
                                navdir.mkdir(parents=True)
                                _atomic_npz(navdir/'assignment.npz',assignment=nav.assignment,lengths=nav.lengths,reachable=nav.reachable)
                                write(navdir/'paths.json',{'paths':[p.tolist() for p in nav.paths]})
                                navigation_cache[navkey]={'ok':True,'path_ref':navkey,'wall_s':perf_counter()-t}
                            except ValueError as error:
                                navigation_cache[navkey]={'ok':False,'status':str(error).split(':')[0],
                                    'error':str(error),'diagnosis':getattr(error,'diagnosis',None),'wall_s':perf_counter()-t}
                        navigation=navigation_cache[navkey]
                        record['navigation']=navigation
                        record['initial_paths_ok']=navigation['ok']
                        record['path_ref']=navigation.get('path_ref')
                        if not navigation['ok']:
                            record['status']=navigation['status']
                    if policy=='P2':
                        saved=load_plan(source/'plans'/row['task_id']/'final')
                        old_phase=read(source/'plans'/row['task_id']/'phase.json')
                        exact=bool(np.array_equal(points,saved.positions) and np.array_equal(arcs,saved.arc_positions)
                                   and record['relative_phase_m']==old_phase['selected_phase_m'])
                        checks={'task_id':row['task_id'],'exact_saved_targets_arcs_phase_match':exact,
                                'candidate_acceptance_and_cost_match':True}
                        if old_phase['attempts']:
                            for a,z in zip(old_phase['attempts'],cache['records'],strict=True):
                                checks['candidate_acceptance_and_cost_match'] &= (a['phase_m']==z['relative_phase_m']
                                    and a['accepted']==z['accepted'] and abs(a['quadrature_cost']-z['score']['demand'])<=1e-12)
                        if not all(v for k,v in checks.items() if k!='task_id'):
                            raise RuntimeError('P2 differs from saved valid pipeline result')
                        p2_checks.append(checks)
                    if policy=='P3' and grid=='main':
                        old_actual=arrays(source/'results'/row['task_id']/'trajectory.npz')
                        old_cfg=yaml.safe_load((source/'results'/row['task_id']/'config.yaml').read_text(encoding='utf-8'))
                        cfg=yaml.safe_load(cfgpath.read_text(encoding='utf-8'))
                        reuse=False
                        if record['initial_paths_ok']:
                            assignment=arrays(root/'navigation'/record['path_ref']/'assignment.npz')['assignment']
                            reuse=bool(cfg==old_cfg and all(np.array_equal(a,v) for a,v in [
                                (points,old_actual['targets']),(assignment,old_actual['assignment']),
                                (data['people'],old_actual['crowd_positions']),(data['radii'],old_actual['crowd_radii']),
                                (data['demand'],old_actual['crowd_demand']),(data['guides'],old_actual['positions'][0]),
                                (data['room'],old_actual['room']),(b.crowd,old_actual['boundary_crowd']),
                                (b.deployment,old_actual['boundary_deployment'])]))
                        p3.append({**record,'planning_ref':rid,'reuse_original_trajectory':reuse,
                                   'original_result_ref':str(source/'results'/row['task_id']) if reuse else None})
                elif policy=='P3' and grid=='main':
                    p3.append({**record,'planning_ref':rid,'reuse_original_trajectory':False,'original_result_ref':None})
                write(destination/'policy.json',record)
                (main_rows if grid=='main' else half_rows).append(record)
        print(json.dumps({'layout':row['task_id'],'main_and_half_candidates':512}),flush=True)
    assert len(main_rows)==96 and len(half_rows)==24 and len(p3)==24 and len(p2_checks)==24
    write(root/'policy_main_96.json',main_rows)
    write(root/'policy_half_P3_24.json',half_rows)
    write(root/'candidate_cache_index.json',cache_refs)
    write(root/'p2_reproduction.json',p2_checks)
    write(root/'p3_execution_manifest.json',{'rows':p3,'maximum_rows':24,
        'reused_trajectory_rows':sum(r['reuse_original_trajectory'] for r in p3),
        'new_execution_eligible':sum(r['initial_paths_ok'] and not r['reuse_original_trajectory'] for r in p3)})
    # Freeze all choices before any P3 deployment. Cache/targets are never reselected.
    files={str(p):sha(p) for sub in ('planning','navigation','candidates') for p in (root/sub).rglob('*') if p.is_file()}
    for name in ('policy_main_96.json','policy_half_P3_24.json','candidate_cache_index.json','p2_reproduction.json',
                 'p3_execution_manifest.json','mass_gap_invariance.json'):
        files[str(root/name)]=sha(root/name)
    write(root/'p3_target_freeze.json',{'files':files,'source_sha256':lock['provenance']['source_sha256'],
        'offline_wall_s':perf_counter()-started,'selection_complete_before_execution':True})
    print(json.dumps({'main_rows':96,'half_P3_rows':24,'reused_trajectories':sum(r['reuse_original_trajectory'] for r in p3),
                      'new_execution_eligible':sum(r['initial_paths_ok'] and not r['reuse_original_trajectory'] for r in p3)}),flush=True)


def independent_rejudge(trajectory,config,metrics):
    """Evaluate saved evidence, writing nothing into the referenced old directory."""
    data=arrays(trajectory)
    b=Boundary(data['boundary_crowd'],data['boundary_deployment'],
        data['boundary_arc'],float(data['boundary_length']))
    limits=Safety(**metrics['safety_limits'])
    criteria,diagnostics=evaluate(data['positions'],data['safe_velocity'],data['active_mask'],data['targets'],
        data['assignment'],b,limits,dict(config['controller'],_people=data['crowd_positions'],_radii=data['crowd_radii'],
        _room=data['room'],_demand=data['crowd_demand'],_hotspot_mask=data['crowd_hotspot_mask']))
    motion=common_motion_metrics(data['positions'],data['safe_velocity'],data['crowd_positions'],data['crowd_radii'],
        data['room'],data['targets'],data['assignment'],config['controller'],limits,config['simulation']['dt'])
    return {'criteria':criteria,'diagnostics':diagnostics,'success':all(criteria.values()),
            'criteria_consistent':criteria==metrics['criteria'],'motion':motion}


def execute_row(task):
    root_string,row=task; root=Path(root_string); lock=locked(root)
    _official_monitor()
    destination=root/'execution'/row['task_id']
    if destination.exists():
        raise FileExistsError('P3 row already attempted')
    data,field,b,domain,cfgpath=condition_data(lock,row)
    cfg=yaml.safe_load(cfgpath.read_text(encoding='utf-8'))
    detail={k:row[k] for k in ('task_id','physical_key','condition_key','method','demand','policy','relative_phase_m')}
    detail.update(method_label=row['method']+'+all_phase_256',new_execution=False,reused=False,motion=None)
    started=perf_counter()
    try:
        with threadpool_limits(limits=1):
            detail['worker']={'pid':os.getpid(),'counterfactual':os.environ['UDC_PRIORITY_STALL_COUNTERFACTUAL'],
                              'numerical_pools':threadpool_info()}
            if not row['initial_paths_ok']:
                result=rejected_result(destination,row,Path(lock['old_protocol']['sources'][row['physical_key']]['source_cell']),
                    row['status'],row.get('navigation',{}).get('error',row['status']),snapshot=data,config_path=cfgpath)
            else:
                selected=arrays(root/'planning'/row['planning_ref']/'targets.npz')
                if row['reuse_original_trajectory']:
                    old=Path(row['original_result_ref'])
                    result=read(old/'metrics.json'); trajectory=old/'trajectory.npz'
                    destination.mkdir(parents=True)
                    write(destination/'reuse_reference.json',{'source':str(old),'trajectory_sha256':sha(trajectory),
                        'exact_input_targets_assignment_config':True,'unchanged_execution_source':True})
                    detail['reused']=True
                else:
                    old_plan=load_plan(Path(lock['source_run'])/'plans'/row['task_id']/'original')
                    plan=replace(old_plan,positions=selected['targets'],arc_positions=selected['arc_positions'],
                        max_gap=float(periodic_gaps(selected['arc_positions'],b.length).max()),
                        method=row['method']+'+all_phase_256',cost=np.array([row['score']['demand']]),
                        iterations=0,exit_reason='ALL_PHASE_256')
                    result=experiment(cfgpath,destination,plots=False,protocol='fixed_n',fixed_n=row['count'],
                        prescribed_boundary=b,prescribed_plan=plan,boundary_source='estimated_disk_union',
                        method_label=row['method']+'+all_phase_256')
                    trajectory=destination/'trajectory.npz'
                    detail['new_execution']=result['steps']>0
                actual=arrays(trajectory)
                assignment=arrays(root/'navigation'/row['path_ref']/'assignment.npz')['assignment']
                detail['exact_pairing']=all(np.array_equal(a,v) for a,v in [
                    (actual['crowd_positions'],data['people']),(actual['crowd_radii'],data['radii']),
                    (actual['crowd_demand'],data['demand']),(actual['crowd_hotspot_mask'],field['hotspot_mask']),
                    (actual['positions'][0],data['guides']),(actual['room'],data['room']),
                    (actual['targets'],selected['targets']),(actual['assignment'],assignment),
                    (actual['boundary_crowd'],b.crowd),(actual['boundary_deployment'],b.deployment)])
                if not detail['exact_pairing']:
                    raise RuntimeError('Actual execution did not read the frozen P3 configuration')
                replay=independent_rejudge(trajectory,cfg,result)
                write(destination/'independent_rejudge.json',replay)
                detail.update(motion=replay['motion'],offline_consistent=replay['criteria_consistent'],
                    offline_success=replay['success'],recoveries=result.get('replan_count'),
                    trajectory_reference=str(trajectory),actual_score=score_sites(b,field,
                        shapely.line_locate_point(Polygon(b.deployment).exterior,shapely.points(actual['positions'][-1]))))
    except Exception:
        if not destination.exists():
            result=rejected_result(destination,row,Path(lock['old_protocol']['sources'][row['physical_key']]['source_cell']),
                'EXPERIMENT_ERROR','EXPERIMENT_ERROR: see runner_error.txt',snapshot=data,config_path=cfgpath)
        else:
            result=read(destination/'metrics.json') if (destination/'metrics.json').exists() else {}
            result.update(termination_status='EXPERIMENT_ERROR',success=False)
            write(destination/'metrics.json',result)
        _atomic_text(destination/'runner_error.txt',traceback.format_exc())
        write(destination/'state.json',{'status':'error','success':False})
    detail.update(status=result['termination_status'],success=result['success'],wall_s=perf_counter()-started)
    write(destination/'protocol_metrics.json',detail)
    return detail


def run(output,stage,workers):
    root=Path(output).resolve(); lock=locked(root)
    if not 1<=workers<=2 or (stage=='first' and workers!=1):
        raise ValueError('First physical input serial; maximum two workers')
    for path,value in read(root/'p3_target_freeze.json')['files'].items():
        if sha(path)!=value:
            raise RuntimeError('Selected P3 target/cache/path changed')
    if (root/(stage+'_execution_freeze.json')).exists():
        raise FileExistsError('Execution stage already attempted')
    rows=read(root/'p3_execution_manifest.json')['rows']
    first=rows[0]['physical_key']
    rows=[r for r in rows if (r['physical_key']==first)==(stage=='first')]
    if stage=='remaining' and not read(root/'first_gate.json')['passed']:
        raise RuntimeError('Serial P3 pairing/rejudge gate failed')
    _official_monitor()
    if any(os.environ.get(k,'1')!='1' for k in ('UDC_COMPLETE_SEGMENT','UDC_VERTEX_ARRIVAL')):
        raise RuntimeError('Original execution switches must remain enabled')
    os.environ.update(OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1')
    write(root/(stage+'_execution_freeze.json'),{'workers':workers,'numerical_threads':1,
        'counterfactual':os.environ['UDC_PRIORITY_STALL_COUNTERFACTUAL'],'tasks':[r['task_id'] for r in rows],
        'source_sha256':lock['provenance']['source_sha256']})
    t=perf_counter(); tasks=[(str(root),r) for r in rows]; results=[]
    if workers==1:
        for task in tasks:
            result=execute_row(task); results.append(result)
            print(json.dumps({'task':result['task_id'],'status':result['status'],'reused':result['reused']}),flush=True)
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for result in pool.map(execute_row,tasks):
                results.append(result)
                print(json.dumps({'task':result['task_id'],'status':result['status'],'reused':result['reused']}),flush=True)
    write(root/(stage+'_execution_summary.json'),{'rows':results,'wall_s':perf_counter()-t,
        'outcomes':dict(Counter(r['status'] for r in results))})
    if stage=='first':
        passed=all(r['status']!='EXPERIMENT_ERROR' and r.get('exact_pairing',True)
                   and r.get('offline_consistent',True) for r in results)
        write(root/'first_gate.json',{'passed':passed,'rows':len(results),'part_of_24':True})


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',default=OUTPUT)
    p.add_argument('--spec',default=SPEC)
    p.add_argument('--stage',choices=['freeze','analyze','first','remaining'],required=True)
    p.add_argument('--workers',type=int,default=1)
    a=p.parse_args()
    if a.stage=='freeze': freeze(a.output,a.spec)
    elif a.stage=='analyze':
        with threadpool_limits(limits=1): analyze(a.output)
    else: run(a.output,a.stage,a.workers)


if __name__=='__main__':
    main()
