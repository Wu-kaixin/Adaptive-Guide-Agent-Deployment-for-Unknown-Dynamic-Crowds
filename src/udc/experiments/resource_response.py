"""Frozen cross-N orchestration; shares the existing complete demand runner."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter

import numpy as np
from shapely.geometry import LineString, Polygon
import yaml

from udc.experiments import demand_confirmation as dc
from udc.experiments.demand_pilot import arrays, read
from udc.experiments.nonconvex_rebuild import write, sha, load_boundary, load_plan
from udc.experiments.provenance import provenance
from udc.experiments.runner import _atomic_npz, _atomic_text
from udc.environment import Environment
from udc.navigation import navigation_domain
from udc.safety import Safety, path_clearances, is_safe

SPEC='configs/step1/nonconvex_resource_response_v1.yaml'
OUTPUT='runs/step1_nonconvex_resource_response_v1'


def extend_guides(data, source, extra=4):
    """Only stored perimeter/arcs and existing starts; no target inputs."""
    line=LineString(source['streams']['inset_line']); length=line.length
    guides=list(data['guides'].copy()); arcs=list(data['guide_arcs'].copy()); additions=[]
    if not np.array_equal(np.array([line.interpolate(s).coords[0] for s in arcs]),data['guides']):
        raise RuntimeError('Stored original perimeter/arcs do not reproduce Guide positions exactly')
    for _ in range(extra):
        ordered=sorted(arcs); gaps=np.diff(np.r_[ordered,ordered[0]+length])
        # sorted starts provide deterministic exact-tie order, including the seam.
        index=int(np.argmax(gaps)); arc=float((ordered[index]+gaps[index]/2)%length)
        position=np.array(line.interpolate(arc).coords[0])
        additions.append({'id':len(guides),'arc_m':arc,'gap_start_m':ordered[index],
                          'split_gap_m':float(gaps[index]),'position':position.tolist()})
        guides.append(position); arcs.append(arc)
    return np.array(guides),np.array(arcs),additions


def quantity(length,count):
    if length is None:
        return {'count':count,'curve_length_m':None,'diagnosis':'curve unavailable; quantity not evaluated'}
    lower_plan=int(np.ceil(length/1.94)); lower_accept=int(np.ceil(length/2.0)); upper=int(np.floor(length/.75))
    return {'count':count,'curve_length_m':length,'average_gap_m':length/count,
        'planning_limit_m':1.94,'acceptance_limit_m':2.,'minimum_separation_m':.75,
        'planning_average_slack_m':1.94-length/count,'planning_lower':lower_plan,
        'acceptance_lower':lower_accept,'necessary_upper':upper,
        'planning_quantity_not_excluded':lower_plan<=count<=upper,
        'acceptance_quantity_not_excluded':lower_accept<=count<=upper,
        'diagnosis':'数量条件未排除' if lower_plan<=count<=upper else '违反规划数量必要条件；不等同于2m要求绝对不可行'}


def science_files():
    # All original scientific and infrastructure sources must match the actual
    # execution commit. Only the shared runner's reference-loading adapter differs.
    names=subprocess.check_output(['git','ls-tree','-r','--name-only','e9fe1bbb','src/udc'],text=True).splitlines()
    result={}
    original=read('reports/step1_nonconvex_demand_confirmation_v1/execution_source.json')['source_files']
    for name in names:
        if not name.endswith('.py') or name=='src/udc/experiments/demand_confirmation.py': continue
        old=subprocess.check_output(['git','show','e9fe1bbb:'+name])
        # Git stores LF while this Windows checkout uses CRLF. Verify both the
        # Git-normalized code and the raw bytes recorded at actual execution.
        if Path(name).read_bytes().replace(b'\r\n',b'\n')!=old.replace(b'\r\n',b'\n'):
            raise RuntimeError('Scientific baseline changed: '+name)
        digest=original[name]
        if sha(name)!=digest: raise RuntimeError('Actual execution source bytes changed: '+name)
        result[name]=digest
    return result


def verify_files(files):
    for name,digest in files.items():
        if sha(name)!=digest: raise RuntimeError('Frozen evidence changed: '+name)


def locked(root,full=False):
    lock=read(root/'protocol.json')
    current=provenance({},None,'fixed_n',None)['source_sha256']
    amended=(root/'orchestration_amendment.json').exists()
    amendment=read(root/'orchestration_amendment.json') if amended else None
    expected=amendment['provenance']['source_sha256'] if amended else lock['provenance']['source_sha256']
    if current!=expected:
        raise RuntimeError('Resource experiment source changed after freeze')
    if amended:
        verify_files(amendment['amendment_files'])
        if sha(root/'protocol.json')!=amendment['original_protocol_sha256']: raise RuntimeError('Original protocol changed')
        lock={**lock,'original_provenance':lock['provenance'],'provenance':amendment['provenance']}
    verify_files(lock['protocol_files'])
    if full:
        verify_files(lock['historical_files']); verify_files(read(root/'inputs_frozen.json')['files'])
        if amended: verify_files(amendment['completed_files'])
        if sha(root/'manifest_360.json')!=lock['manifest_sha256']: raise RuntimeError('Manifest changed')
    return lock


def amend(output):
    """Checkpoint a diagnosed accounting-only correction, never retry old rows."""
    root=Path(output).resolve(); lock=read(root/'protocol.json')
    if (root/'orchestration_amendment.json').exists(): raise FileExistsError('Only one explicit amendment')
    meta=provenance({},None,'fixed_n',None)
    if meta['dirty']: raise RuntimeError('Commit amendment before continuation')
    science_files(); verify_files(lock['historical_files']); verify_files(lock['protocol_files'])
    verify_files(read(root/'inputs_frozen.json')['files'])
    if sha(root/'manifest_360.json')!=lock['manifest_sha256']: raise RuntimeError('Manifest changed')
    stopped=read(root/'run_stop.json'); details=[read(p) for p in (root/'results').glob('*/protocol_metrics.json')]
    errors=[r for r in details if r['status']=='EXPERIMENT_ERROR']
    if len(details)!=52 or {r['task_id'] for r in errors}!={f'wide_u__seed_77__N_21__{m}' for m in dc.METHODS}:
        raise RuntimeError('Amendment only applies to the documented 52-row checkpoint')
    diagnosis=[]
    for r in errors:
        d=root/'results'/r['task_id']; m=read(d/'metrics.json'); a=arrays(d/'trajectory.npz')
        initial=arrays(root/'plans'/r['task_id']/'assignment.npz')['assignment']
        trace=dc.assignment_trace_check(initial,a['assignment'],a['targets'],m['recovery_attempts'])
        if not trace['accepted'] or not all(m['criteria'].values()): raise RuntimeError('Unexpected failure; cannot continue')
        diagnosis.append({'task_id':r['task_id'],'status_retained':'EXPERIMENT_ERROR','no_retry':True,
            'independent_physical_success':True,'trace':trace,'original_metrics_already_overwritten_by_old_wrapper':True})
    completed={str(p.resolve()):sha(p) for p in root.rglob('*') if p.is_file()}
    path='reports/step1_nonconvex_resource_response_v1/AMENDMENT.zh.md'
    write(root/'orchestration_amendment.json',{'provenance':meta,'original_protocol_sha256':sha(root/'protocol.json'),
        'original_provenance':lock['provenance'],'stop':stopped,'completed_files':completed,'completed_rows':52,
        'diagnosis':diagnosis,'amendment_files':{str(Path(path).resolve()):sha(path)},
        'policy':'no scientific changes, no rerun, original 2 errors retained; continue only unstarted rows'})
    print(json.dumps({'checkpoint_rows':52,'retained_program_errors':2,'remaining_rows':308}),flush=True)


def reuse_check(old,row,data,field,source,old_lock):
    """Decision before any new-N result; fail closed, with explicit reason."""
    directory=old/'results'/row['old_task_id']; detail=read(directory/'protocol_metrics.json')
    metrics=read(directory/'metrics.json'); a=arrays(directory/'trajectory.npz')
    execution_source=metrics.get('provenance',metrics)
    checks={'original_input_hash':sha(old/'physical'/row['physical_key']/'input.npz')==source['input_sha256'],
        'original_density_hash':sha(old/'conditions'/row['condition_key']/'demand.npz')==row['density_sha256'],
        'execution_source':not execution_source['dirty'] and execution_source['commit']==old_lock['provenance']['commit']
            and execution_source['source_sha256']==old_lock['provenance']['source_sha256'],
        'input_and_initial_exact':all(np.array_equal(x,y) for x,y in [(a['positions'][0],data['guides']),
            (a['crowd_positions'],data['people']),(a['crowd_radii'],data['radii']),
            (a['crowd_demand'],field['demand']),(a['room'],data['room'])]),
        'count':a['positions'].shape[1]==row['count']}
    if detail['target_valid']:
        b=load_boundary(old/'physical'/row['physical_key'])
        domain=navigation_domain(data['room'],.55,Polygon(b.crowd),.55)
        plan=load_plan(old/'plans'/row['old_task_id']/'final')
        assignment=arrays(old/'plans'/row['old_task_id']/'assignment.npz')['assignment']
        cfg=yaml.safe_load((directory/'config.yaml').read_text(encoding='utf-8'))
        frozen_cfg=yaml.safe_load((old/'conditions'/row['condition_key']/'input.yaml').read_text(encoding='utf-8'))
        checks.update(curve_exact=np.array_equal(a['boundary_deployment'],b.deployment)
            and np.array_equal(a['boundary_crowd'],b.crowd) and np.array_equal(a['boundary_arc'],b.arc),
            static_domain_exact=domain.geometry_sha256==detail['domain_sha256'],
            targets_assignment_exact=np.array_equal(a['targets'],plan.positions) and np.array_equal(a['assignment'],assignment),
            config_exact=cfg==frozen_cfg,hotspot_exact=np.array_equal(a['crowd_hotspot_mask'],field['hotspot_mask']))
    return {'eligible':all(checks.values()),'checks':checks,'reason':None if all(checks.values()) else
            'pre-run strict reuse mismatch: '+','.join(k for k,v in checks.items() if not v),
            'reference':str(directory),'hashes':{p.name:sha(p) for p in directory.iterdir() if p.is_file()}}


def freeze(output,spec_path=SPEC):
    root=Path(output).resolve()
    if root.exists(): raise FileExistsError('Use a new output; never overwrite frozen experiments')
    meta=provenance({},None,'fixed_n',None)
    if meta['dirty']: raise RuntimeError('Commit protocol, orchestration, analysis and tests before freeze')
    spec=yaml.safe_load(Path(spec_path).read_text(encoding='utf-8'))
    if (spec['increments']!=[0,2,4] or spec['seeds']!=list(range(75,95)) or spec['methods']!=dc.METHODS
        or spec['base_counts']!={'elongated':11,'tight_u':13,'wide_u':19}): raise ValueError('Changed resource factors')
    scientific=science_files(); old=Path(spec['source_run']).resolve(); previous=read(old/'protocol.json')
    historical=read(Path(spec['source_report'])/'raw_checksums.json')
    protected={str(old/k):v for k,v in historical['run_files'].items()}
    protected.update(historical['protected_baseline'])
    protected.update({str(p.resolve()):sha(p) for p in Path(spec['source_report']).rglob('*') if p.is_file() and '__pycache__' not in p.parts})
    verify_files(protected)
    old_frozen=read(old/'inputs_frozen.json'); root.mkdir(parents=True)
    rows=[]; starts={}; files={}; diagnostics=[]; reuse={}; sources=old_frozen['sources']
    for seed in spec['seeds']:
        for scene in spec['scenes']:
            key=f'{scene}__seed_{seed}'; source=sources[key]; n0=spec['base_counts'][scene]
            physical=old/'physical'/key; condition=old/'conditions'/(key+'__hotspot5')
            data=arrays(physical/'input.npz'); field=arrays(condition/'demand.npz')
            extended,arcs,additions=extend_guides(data,source)
            length=load_boundary(physical).length if source['status']=='ACCEPTED' else None
            diagnostics.append({'physical_key':key,'scene':scene,'seed':seed,'input_status':source['status'],**quantity(length,n0-1)})
            for increment in spec['increments']:
                count=n0+increment; init=root/'initials'/f'{key}__N_{count}'; init.mkdir(parents=True)
                guides=extended[:count]; guide_file=init/'guides.npz'
                _atomic_npz(guide_file,guides=guides,ids=np.arange(count),arcs=arcs[:count])
                cfg=yaml.safe_load((condition/'input.yaml').read_text(encoding='utf-8'))
                cfg['guides']['count']=count; cfg['guides']['initial_positions']=guides.tolist()
                cfgpath=init/'input.yaml'; _atomic_text(cfgpath,yaml.safe_dump(cfg,sort_keys=False))
                clearances=path_clearances(guides,guides,data['people'],data['room'])
                safe=is_safe(clearances,Safety(.75,.75,.55,1.,1e-7))
                if source['people_quality']['accepted']:
                    env=Environment(cfg); obs=env.observe()
                    if not all(np.array_equal(x,y) for x,y in [(obs.positions,data['people']),
                        (obs.radii,data['radii']),(obs.demand,field['demand']),(env.initialize_guides(cfg['guides']),guides)]):
                        raise RuntimeError('Config does not reproduce full-precision frozen input')
                record={'physical_key':key,'scene':scene,'seed':seed,'count':count,'increment':increment,
                    'ids':list(range(count)),'original_ids':list(range(n0)), 'appended':additions[:increment],
                    'guides_sha256':sha(guide_file),'original_prefix_exact':np.array_equal(guides[:n0],data['guides']),
                    'initialization_accepted':safe,'clearances_m':clearances,'quantity':quantity(length,count),
                    'physical':str(physical),'condition':str(condition),'config':str(cfgpath),'guides':str(guide_file)}
                write(init/'mapping.json',record); starts[f'{key}__N_{count}']=record
                for p in (guide_file,cfgpath,init/'mapping.json',physical/'input.npz',condition/'demand.npz'):
                    files[str(p)]=sha(p)
                if length is not None: files[str(physical/'boundary.npz')]=sha(physical/'boundary.npz')
                for method in spec['methods']:
                    cid=key+'__hotspot5'; oldid=cid+'__'+method
                    row={'task_id':f'{key}__N_{count}__{method}','physical_key':key,'condition_key':cid,
                        'scene':scene,'seed':seed,'count':count,'increment':increment,'method':method,'demand':'hotspot5',
                        'method_label':method+'+all_phase_256','initial_key':f'{key}__N_{count}',
                        'input_status':source['status'],'input_sha256':old_frozen['conditions'][cid]['observation_sha256'],
                        'physical_sha256':source['physical_sha256'],'density_sha256':sha(condition/'demand.npz'),
                        'old_task_id':oldid,'mode':'new_execution' if increment else 'pending_reuse_check'}
                    if increment==0:
                        decision=reuse_check(old,row,data,field,source,previous); reuse[row['task_id']]=decision
                        row['mode']='reference' if decision['eligible'] else 'new_baseline_execution'
                        row['reuse_reason']=decision['reason']
                    rows.append(row)
    write(root/'quantity_diagnostics_60.json',diagnostics)
    write(root/'reuse_decisions_120.json',reuse)
    write(root/'manifest_360.json',{'rows':rows,'physical_units':60,'main_rows':360,'diagnostic_rows':60})
    for name in ('quantity_diagnostics_60.json','reuse_decisions_120.json'): files[str(root/name)]=sha(root/name)
    write(root/'inputs_frozen.json',{'sources':sources,'starts':starts,'files':files,
        'new_people_draws':0,'all_N_initials_before_any_new_method_results':True})
    protocol_files={str(Path(p).resolve()):sha(p) for p in [spec_path,
        'reports/step1_nonconvex_resource_response_v1/PROTOCOL.zh.md',
        previous['spec']['demand_spec'],previous['spec']['phase_spec'],previous['spec']['generator_spec'],
        spec['evaluation']['fine_evaluator'],'pyproject.toml','requirements.txt']}
    write(root/'protocol.json',{'spec':spec,'pilot':previous['pilot'],'phase':previous['phase'],
        'base':previous['base'],'provenance':meta,'protocol_files':protocol_files,'historical_files':protected,
        'unchanged_scientific_files':scientific,'manifest_sha256':sha(root/'manifest_360.json'),
        'baseline_source':previous['provenance'],'shared_runner_change':'reference/input adapter only; scientific call sequence unchanged'})
    write(root/'environment.json',{'python':sys.version,'executable':sys.executable,'provenance':meta,
        'freeze_modes':dict(Counter(r['mode'] for r in rows))})
    print(json.dumps({'rows':len(rows),'diagnostics':len(diagnostics),'modes':dict(Counter(r['mode'] for r in rows)),
        'initialization_rejections':sum(not v['initialization_accepted'] for v in starts.values())}),flush=True)


def reference_rejudge(directory):
    # Rejected references are reevaluated in memory; reevaluate_result writes to
    # its argument, so it is intentionally never called on historical results.
    m=read(directory/'metrics.json'); a=arrays(directory/'trajectory.npz')
    if len(a['boundary_deployment'])==0:
        from udc.experiments.runner import _empty_criteria
        criteria=_empty_criteria(); diagnostics={}; motion=None
        replay={'criteria':criteria,'diagnostics':diagnostics,'success':all(criteria.values()),
                'criteria_consistent':criteria==m['criteria'],'motion':motion}
    else:
        cfg=yaml.safe_load((directory/'config.yaml').read_text(encoding='utf-8'))
        replay=dc.independent_rejudge(directory/'trajectory.npz',cfg,m)
    compared={}
    for k in ('actual_max_gap','mean_curve_distance','tracking_rmse','actual_demand_weighted_cost',
              'planned_demand_weighted_cost','actual_hotspot_concentration'):
        x,y=m.get(k),replay['diagnostics'].get(k)
        compared[k]=x==y if x is None or y is None else abs(x-y)<=1e-9
    replay.update(diagnostics_consistent=all(compared.values()),diagnostic_comparison=compared,
                  executed=len(a['positions'])>1,success_consistent=replay['success']==m['success'])
    return replay


def run_row(task):
    rootstr,row=task; root=Path(rootstr); lock=locked(root); frozen=read(root/'inputs_frozen.json')
    destination=root/'results'/row['task_id']
    if destination.exists(): raise FileExistsError('Never repeat a row')
    init=frozen['starts'][row['initial_key']]
    if row['mode']=='reference':
        decision=read(root/'reuse_decisions_120.json')[row['task_id']]; old=Path(decision['reference'])
        detail=read(old/'protocol_metrics.json'); replay=reference_rejudge(old)
        detail.update(row); detail.update(offline_consistent=replay['criteria_consistent'] and replay['diagnostics_consistent']
            and replay['success_consistent'],motion=replay['motion'],offline_success=replay['success'])
        destination.mkdir(parents=True); write(destination/'reuse_reference.json',decision)
        write(destination/'independent_rejudge.json',replay); write(destination/'protocol_metrics.json',detail)
    else:
        context={'lock':lock,'frozen':frozen,**{k:init[k] for k in ('physical','condition','config','guides','initialization_accepted')}}
        detail=dc.run_row(task,context=context)
    return detail


def run(output,stage,workers):
    root=Path(output).resolve(); lock=locked(root,full=True)
    if not 1<=workers<=2 or (stage=='first' and workers!=1): raise ValueError('First unit serial; maximum two workers')
    if ((root/'run_stop.json').exists() and stage!='continue') or (root/(stage+'_freeze.json')).exists(): raise RuntimeError('Stage already attempted or experiment stopped')
    if stage=='continue' and not (root/'orchestration_amendment.json').exists(): raise RuntimeError('Explicit committed amendment required')
    if stage=='remaining' and not read(root/'first_gate.json')['passed']: raise RuntimeError('First-unit gate failed')
    dc._official_monitor()
    if any(os.environ.get(k,'1')!='1' for k in ('UDC_COMPLETE_SEGMENT','UDC_VERTEX_ARRIVAL')): raise RuntimeError('Original switches required')
    os.environ.update(OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1')
    rows=read(root/'manifest_360.json')['rows']
    rows=([r for r in rows if not (root/'results'/r['task_id']/'protocol_metrics.json').exists()] if stage=='continue' else
          [r for r in rows if (r['physical_key']==lock['spec']['first_unit'])==(stage=='first')])
    write(root/(stage+'_freeze.json'),{'workers':workers,'numerical_threads':1,'counterfactual':os.environ['UDC_PRIORITY_STALL_COUNTERFACTUAL'],
        'tasks':[r['task_id'] for r in rows],'source':lock['provenance']})
    results=[]; stopped=None; start=perf_counter()
    if workers==1:
        for row in rows:
            r=run_row((str(root),row)); results.append(r)
            print(json.dumps({'task':r['task_id'],'status':r['status'],'mode':r['mode']}),flush=True)
            if dc.stop_required(r): stopped=r['task_id']; break
    else:
        iterator=iter(rows)
        with ProcessPoolExecutor(max_workers=workers) as pool:
            pending={}
            def submit():
                row=next(iterator,None)
                if row: pending[pool.submit(run_row,(str(root),row))]=row
            for _ in range(workers): submit()
            while pending:
                completed,_=wait(pending,return_when=FIRST_COMPLETED)
                for future in completed:
                    pending.pop(future); r=future.result(); results.append(r)
                    print(json.dumps({'task':r['task_id'],'status':r['status'],'mode':r['mode']}),flush=True)
                    if dc.stop_required(r): stopped=r['task_id']
                if not stopped:
                    for _ in completed: submit()
    write(root/(stage+'_summary.json'),{'expected':len(rows),'completed':len(results),'wall_s':perf_counter()-start,
        'outcomes':dict(Counter(r['status'] for r in results)),'stop_task':stopped})
    if stopped:
        write(root/('continuation_stop.json' if stage=='continue' else 'run_stop.json'),{'trigger':stopped})
        raise RuntimeError('Safety/program/rejudge stop; retain all evidence')
    if stage=='first':
        write(root/'first_gate.json',{'passed':len(results)==6 and all(r['offline_consistent'] and r.get('exact_pairing',True) for r in results),
            'main_matrix_rows':6,'new_N_rows':4,'counted_once':True,'ID_mappings_checked_at_freeze':True})


def rejudge(output):
    root=Path(output).resolve(); lock=locked(root,full=True); checks=[]; integrations=[]
    frozen=read(root/'inputs_frozen.json'); integral=dc.load_fine_evaluator(lock)
    for row in read(root/'manifest_360.json')['rows']:
        directory=root/'results'/row['task_id']
        if not (directory/'protocol_metrics.json').exists():
            checks.append({'task_id':row['task_id'],'status':'NOT_STARTED'}); continue
        old=Path(lock['spec']['source_run']).resolve()/'results'/row['old_task_id']
        replay=reference_rejudge(old) if row['mode']=='reference' else dc.independent_row(root,row)
        detail=read(directory/'protocol_metrics.json')
        replay['success_consistent']=replay['success']==detail['success']
        write(directory/'independent_rejudge_final.json',replay)
        checks.append({'task_id':row['task_id'],'mode':row['mode'],**{k:replay[k] for k in
            ('criteria_consistent','diagnostics_consistent','success_consistent','executed','success')}})
        if detail['target_valid']:
            init=frozen['starts'][row['initial_key']]
            b=load_boundary(Path(init['physical'])); field=arrays(Path(init['condition'])/'demand.npz')
            plan_dir=(Path(lock['spec']['source_run']).resolve()/'plans'/row['old_task_id'] if row['mode']=='reference'
                      else root/'plans'/row['task_id'])
            points=load_plan(plan_dir/'final').positions
            main=integral(b,field,points,2048); fine=integral(b,field,points,8192)
            record={'task_id':row['task_id'],'main':main,'fine':fine,
                'main_matches':all(abs(main[k]-detail['planned_cost'][k])<=1e-10 for k in main),
                'fine_matches':all(abs(fine[k]-detail['fine_cost'][k])<=1e-10 for k in fine)}
            integrations.append(record); write(directory/'independent_integration.json',record)
    verify_files(lock['historical_files'])
    write(root/'all_independent_checks.json',checks)
    write(root/'all_independent_integrations.json',integrations)
    print(json.dumps({'rows':len(checks),'inconsistent':sum(any(c.get(k) is False for k in
        ('criteria_consistent','diagnostics_consistent','success_consistent')) for c in checks),
        'not_started':sum(c.get('status')=='NOT_STARTED' for c in checks)}),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--stage',required=True,choices=['freeze','first','remaining','amend','continue','rejudge'])
    p.add_argument('--output',default=OUTPUT); p.add_argument('--spec',default=SPEC); p.add_argument('--workers',type=int,default=1)
    a=p.parse_args()
    if a.stage=='freeze': freeze(a.output,a.spec)
    elif a.stage=='amend': amend(a.output)
    elif a.stage=='rejudge': rejudge(a.output)
    else:
        try: run(a.output,a.stage,a.workers)
        except BaseException as error:
            root=Path(a.output).resolve()
            if (root/'manifest_360.json').exists() and not (root/'run_stop.json').exists():
                write(root/'run_stop.json',{'type':type(error).__name__,'error':str(error),'stage':a.stage})
            raise


if __name__=='__main__': main()
