"""New-input resource confirmation; all scientific decisions use the shared runner."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
import hashlib
import importlib.metadata
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter

import numpy as np
from shapely.geometry import Polygon
import yaml

from udc.boundary import construct_boundary
from udc.environment import Environment
from udc.experiments import demand_confirmation as dc
from udc.experiments.demand_pilot import arrays, read, physical_hash, frozen_density
from udc.experiments.nonconvex_rebuild import (write, sha, array_hash, draw_cell, load_boundary, load_plan)
from udc.experiments.provenance import provenance
from udc.experiments.resource_response import extend_guides, quantity, verify_files
from udc.experiments.runner import _atomic_npz, _atomic_text
from udc.experiments.scene_closure import _execution_config
from udc.experiments.boundary_comparison import input_quality
from udc.safety import Safety, path_clearances, is_safe
from udc.navigation import navigation_domain

SPEC='configs/step1/nonconvex_resource_confirmation_v1.yaml'
OUTPUT='runs/step1_nonconvex_resource_confirmation_v1'
REPORT='reports/step1_nonconvex_resource_confirmation_v1'
SCENES=dc.SCENES
METHODS=dc.METHODS


def scientific_baseline():
    """Only the generic resource statistics exporter may differ from 865e6a48."""
    names=subprocess.check_output(['git','ls-tree','-r','--name-only','865e6a48','src/udc'],text=True).splitlines()
    files={}; normalized={}
    for name in names:
        if not name.endswith('.py') or name=='src/udc/analysis/resource_response.py': continue
        expected=subprocess.check_output(['git','show','865e6a48:'+name]).replace(b'\r\n',b'\n')
        actual=Path(name).read_bytes().replace(b'\r\n',b'\n')
        if actual!=expected: raise RuntimeError('Scientific baseline changed: '+name)
        files[str(Path(name).resolve())]=sha(name)
        normalized[name]=hashlib.sha256(expected).hexdigest()
    return {'baseline':'865e6a48','files':files,'normalized_git_sha256':normalized,
        'allowed_adapter':'src/udc/analysis/resource_response.py: injectable lock/export metadata only'}


def select_audit(root,spec):
    audit=dc.audit_seeds(exclude=root)
    selection=spec['seed_selection']
    exposed={tuple(x) for x in audit['exposed_scene_seed_pairs']}
    audit['selected_seeds']=dc.first_interval(exposed,selection['start'],selection['length'],selection['maximum_seed'])
    audit['selection_rule']='first common unexposed contiguous 20 from 95; local scope only; ambiguous same-family excluded'
    return audit


def prepare(output,spec_path=SPEC):
    root=Path(output).resolve()
    if root.exists(): raise FileExistsError('Use a new attempt; never overwrite confirmation')
    meta=provenance({},None,'fixed_n',None)
    if meta['dirty']: raise RuntimeError('Commit protocol, orchestration, statistics and tests before seed selection or drawing')
    spec=yaml.safe_load(Path(spec_path).read_text(encoding='utf-8'))
    if (spec['scientific_baseline']!='865e6a48' or spec['scenes']!=SCENES or spec['methods']!=METHODS
        or spec['increments']!=[0,2,4] or spec['base_counts']!={'elongated':11,'tight_u':13,'wide_u':19}
        or spec['demand']!='hotspot5' or spec['statistics']['bootstrap_repetitions']!=10000):
        raise ValueError('Changed confirmation factors')
    dep,pilot,phase,generator,base=dc.dependencies(spec['dependency_spec'])
    science=scientific_baseline(); audit=select_audit(root,spec)
    protected={}
    for old,key in [('step1_nonconvex_resource_response_v1','run_files'),
                    ('step1_resource_recovery_diagnostics_v1','new_run_files'),
                    ('step1_nonconvex_demand_confirmation_v1','run_files')]:
        oldroot=Path('runs',old).resolve(); report=Path('reports',old)
        checks=read(report/'raw_checksums.json')
        protected.update({str(oldroot/p):v for p,v in checks[key].items()})
        protected.update({str(p.resolve()):sha(p) for p in report.rglob('*') if p.is_file() and '__pycache__' not in p.parts})
    verify_files(protected)
    protocol_files={str(Path(p).resolve()):sha(p) for p in [spec_path,spec['dependency_spec'],dep['demand_spec'],
        dep['phase_spec'],dep['generator_spec'],generator['base_spec'],spec['evaluation']['fine_evaluator'],
        REPORT+'/PROTOCOL.zh.md','requirements.txt','pyproject.toml']}
    root.mkdir(parents=True)
    write(root/'seed_audit.json',audit)
    rows=[{'task_id':f'{scene}__seed_{seed}__N_{spec["base_counts"][scene]+n}__{method}',
        'physical_key':f'{scene}__seed_{seed}','condition_key':f'{scene}__seed_{seed}__hotspot5',
        'initial_key':f'{scene}__seed_{seed}__N_{spec["base_counts"][scene]+n}',
        'scene':scene,'seed':seed,'increment':n,'count':spec['base_counts'][scene]+n,'method':method,
        'method_label':method+'+all_phase_256','demand':'hotspot5','mode':'new_execution'}
        for seed in audit['selected_seeds'] for scene in SCENES for n in spec['increments'] for method in METHODS]
    write(root/'schedule_360.json',{'rows':rows,'physical_units':60,'input_generated':False})
    write(root/'protocol.json',{'spec':spec,'pilot':pilot,'phase':phase,'generator':generator,'base':base,
        'provenance':meta,'baseline_source':{'scientific_commit':'865e6a48','development_delivery':'dafc4aba'},
        'selected_seeds':audit['selected_seeds'],'scientific_consistency':science,'historical_files':protected,
        'protocol_files':protocol_files,'seed_audit_sha256':sha(root/'seed_audit.json'),
        'schedule_sha256':sha(root/'schedule_360.json')})
    write(root/'environment.json',{'executable':sys.executable,'python':sys.version,'udc_path':str(Path(dc.__file__).resolve()),
        'provenance':meta,'dependency_upgrade':False,'dependencies':{name:importlib.metadata.version(name) for name in
            ('numpy','scipy','shapely','PyYAML','jupedsim','pedpy','threadpoolctl','pytest','matplotlib')}})
    print({'selected_seeds':audit['selected_seeds'],'rows':len(rows),'source':meta},flush=True)


def locked(root,full=False):
    root=Path(root).resolve(); lock=read(root/'protocol.json')
    meta=provenance({},None,'fixed_n',None)
    if meta['source_sha256']!=lock['provenance']['source_sha256']: raise RuntimeError('Frozen source changed')
    verify_files(lock['protocol_files']); verify_files(lock['scientific_consistency']['files'])
    if sha(root/'seed_audit.json')!=lock['seed_audit_sha256'] or sha(root/'schedule_360.json')!=lock['schedule_sha256']:
        raise RuntimeError('Prepared seed interval or schedule changed')
    if full:
        verify_files(lock['historical_files'])
        frozen=read(root/'inputs_frozen.json'); verify_files(frozen['files'])
        if sha(root/'manifest_360.json')!=frozen['manifest_sha256']: raise RuntimeError('Manifest changed')
    return lock


def freeze_inputs(output):
    root=Path(output).resolve(); lock=locked(root)
    if (root/'physical').exists() or (root/'inputs_frozen.json').exists(): raise FileExistsError('One draw only')
    if provenance({},None,'fixed_n',None)['dirty']: raise RuntimeError('Clean source required before drawing')
    fresh=select_audit(root,lock['spec'])
    if fresh['selected_seeds']!=lock['selected_seeds']: raise RuntimeError('Seed exposure changed')
    write(root/'audit_at_input_freeze.json',fresh)
    spec,pilot,base=lock['spec'],lock['pilot'],lock['base']
    sources={}; conditions={}; starts={}; diagnostics=[]; started=perf_counter()
    for seed in lock['selected_seeds']:
        for scene in SCENES:
            key=f'{scene}__seed_{seed}'; n0=spec['base_counts'][scene]
            cell=root/'physical'/key; cell.mkdir(parents=True)
            data,streams=draw_cell(lock['generator'],base,next(s for s in base['scenes'] if s['name']==scene),seed)
            _atomic_npz(cell/'input.npz',**data)
            room=np.array([[0,0],[base['room_m'],0],[base['room_m'],base['room_m']],[0,base['room_m']]])
            cfgpath=_execution_config(cell,data['people'],room,n0,seed,base)
            cfg=yaml.safe_load(cfgpath.read_text(encoding='utf-8')); cfg['guides']['initial_positions']=data['guides'].tolist()
            _atomic_text(cfgpath,yaml.safe_dump(cfg,sort_keys=False))
            quality=input_quality(data); status='ACCEPTED' if quality['accepted'] else 'INITIALIZATION_INVALID'
            reason='' if quality['accepted'] else 'INITIALIZATION_INVALID: one-draw personnel/room constraints failed'
            b=None; description=None
            if quality['accepted']:
                b,description,occupied,_=construct_boundary('disk_union_closed',data['people'],data['radii'],data['room'],**pilot['boundary'])
                if occupied is not None: (cell/'occupied.wkb').write_bytes(occupied.wkb)
                if b is None: reason=description['failure_reason']; status=reason.split(':')[0]
            domain_hash=None
            if b is not None:
                _atomic_npz(cell/'boundary.npz',crowd=b.crowd,deployment=b.deployment,arc=b.arc,length=np.array(b.length))
                domain=navigation_domain(data['room'],.55,Polygon(b.crowd),.55); domain_hash=domain.geometry_sha256
                for name in ('free','physical_free','physical_obstacle','conservative_obstacle'):
                    (cell/(name+'.wkb')).write_bytes(getattr(domain,name).wkb)
                write(cell/'static_domain.json',{'geometry_sha256':domain_hash,'wall_margin_m':.55,'crowd_margin_m':.55,
                    'simplify_tolerance_m':domain.simplify_tolerance_m,'planning_margin_m':domain.planning_margin_m})
            source={'scene':scene,'seed':seed,'count':n0,'physical_sha256':physical_hash(data),'people_quality':quality,
                'status':status,'reason':reason,'streams':streams,'boundary_description':description,
                'input_sha256':sha(cell/'input.npz'),'boundary_sha256':sha(cell/'boundary.npz') if b is not None else None,
                'guide_initial_sha256':array_hash(guides=data['guides']),'static_domain_sha256':domain_hash}
            write(cell/'input_record.json',source); sources[key]=source
            cid=key+'__hotspot5'; condition=root/'conditions'/cid; condition.mkdir(parents=True)
            region=pilot['hotspots'][scene]; mask=np.linalg.norm(data['people']-region['center'],axis=1)<=region['radius']
            demand=np.where(mask,5.,1.)
            ccfg={**cfg,'crowd':dict(cfg['crowd'],demand={'mean':1.,'std':0.,'min':1.,'max':5.},demand_region=region)}
            _atomic_text(condition/'input.yaml',yaml.safe_dump(ccfg,sort_keys=False))
            payload={'demand':demand,'hotspot_mask':mask}; density=None
            if b is not None: payload,density=frozen_density(b,data,demand,mask,pilot,5.)
            _atomic_npz(condition/'demand.npz',**payload)
            crec={'physical_key':key,'demand_name':'hotspot5','status':status,'region':region,'density':density,
                'demand_vector':demand.tolist(),'hotspot_person_indices':np.flatnonzero(mask).tolist(),
                'missing_density_reason':reason if density is None else None,
                'observation_sha256':array_hash(people=data['people'],radii=data['radii'],demand=demand),
                'density_file_sha256':sha(condition/'demand.npz')}
            write(condition/'condition.json',crec); conditions[cid]=crec
            extended,arcs,additions=extend_guides(data,source)
            length=b.length if b is not None else None
            diagnostics.append({'physical_key':key,'scene':scene,'seed':seed,'input_status':status,**quantity(length,n0-1)})
            for n in spec['increments']:
                count=n0+n; ikey=f'{key}__N_{count}'; initial=root/'initials'/ikey; initial.mkdir(parents=True)
                guides=extended[:count]; guidefile=initial/'guides.npz'
                _atomic_npz(guidefile,guides=guides,ids=np.arange(count),arcs=arcs[:count])
                ncfg={**ccfg,'guides':dict(ccfg['guides'],count=count,initial_positions=guides.tolist())}
                ncfgpath=initial/'input.yaml'; _atomic_text(ncfgpath,yaml.safe_dump(ncfg,sort_keys=False))
                clearances=path_clearances(guides,guides,data['people'],data['room'])
                safe=is_safe(clearances,Safety(.75,.75,.55,1.,1e-7))
                if quality['accepted']:
                    env=Environment(ncfg); obs=env.observe()
                    if not all(np.array_equal(a,v) for a,v in [(obs.positions,data['people']),(obs.radii,data['radii']),
                        (obs.demand,demand),(env.initialize_guides(ncfg['guides']),guides)]):
                        raise RuntimeError('Frozen config does not reproduce full-precision input')
                record={'physical_key':key,'scene':scene,'seed':seed,'count':count,'increment':n,'ids':list(range(count)),
                    'original_ids':list(range(n0)),'appended':additions[:n],'guides_sha256':sha(guidefile),
                    'original_prefix_exact':np.array_equal(guides[:n0],data['guides']),'initialization_accepted':safe,
                    'clearances_m':clearances,'quantity':quantity(length,count),'physical':str(cell),'condition':str(condition),
                    'config':str(ncfgpath),'guides':str(guidefile)}
                write(initial/'mapping.json',record); starts[ikey]=record
    rows=[{**row,'input_status':sources[row['physical_key']]['status'],
        'input_sha256':conditions[row['condition_key']]['observation_sha256'],
        'physical_sha256':sources[row['physical_key']]['physical_sha256'],
        'density_sha256':conditions[row['condition_key']]['density_file_sha256']}
        for row in read(root/'schedule_360.json')['rows']]
    write(root/'manifest_360.json',{'rows':rows,'physical_units':60,'main_rows':360,'diagnostic_rows':60})
    write(root/'quantity_diagnostics_60.json',diagnostics)
    files={str(p.resolve()):sha(p) for folder in ('physical','conditions','initials') for p in (root/folder).rglob('*') if p.is_file()}
    files[str(root/'quantity_diagnostics_60.json')]=sha(root/'quantity_diagnostics_60.json')
    write(root/'inputs_frozen.json',{'sources':sources,'conditions':conditions,'starts':starts,'files':files,
        'manifest_sha256':sha(root/'manifest_360.json'),'draws':60,'replacement_draws':0,
        'all_inputs_initials_density_windows_before_any_method_result':True,'wall_s':perf_counter()-started})
    print({'physical_units':60,'starts':len(starts),'rows':len(rows),'statuses':dict(Counter(s['status'] for s in sources.values()))},flush=True)


def run_row(task):
    root=Path(task[0]); row=task[1]; lock=locked(root); frozen=read(root/'inputs_frozen.json')
    initial=frozen['starts'][row['initial_key']]
    context={'lock':lock,'frozen':frozen,**{k:initial[k] for k in ('physical','condition','config','guides','initialization_accepted')}}
    detail=dc.run_row(task,context=context)
    directory=root/'results'/row['task_id']; raw=read(directory/'metrics.json')
    detail.update(runner_status=raw['termination_status'],runner_success=raw['success'],
        orchestration_status='EXPERIMENT_ERROR' if (directory/'orchestration_error.json').exists() else 'OK',
        independent_physical_success=detail['offline_success'] if detail['executed'] else None)
    write(directory/'protocol_metrics.json',detail)
    return detail


def run(output,stage,workers):
    root=Path(output).resolve(); lock=locked(root,full=True)
    if not 1<=workers<=2 or (stage=='first' and workers!=1): raise ValueError('First seed serial, at most two workers')
    if (root/'run_stop.json').exists() or (root/(stage+'_freeze.json')).exists(): raise RuntimeError('Attempt already stopped or stage attempted')
    if stage=='remaining' and not read(root/'first_gate.json')['passed']: raise RuntimeError('First-seed gate failed')
    dc._official_monitor()
    if os.environ.get('UDC_PRIORITY_STALL_COUNTERFACTUAL')!='0': raise RuntimeError('Explicit counterfactual=0 required')
    if any(os.environ.get(k,'1')!='1' for k in ('UDC_COMPLETE_SEGMENT','UDC_VERTEX_ARRIVAL')): raise RuntimeError('Original switches required')
    os.environ.update(OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1')
    rows=[r for r in read(root/'manifest_360.json')['rows'] if (r['seed']==lock['selected_seeds'][0])==(stage=='first')]
    write(root/(stage+'_freeze.json'),{'workers':workers,'numerical_threads':1,'counterfactual':'0',
        'tasks':[r['task_id'] for r in rows],'source':lock['provenance']})
    results=[]; stopped=None; start=perf_counter()
    if workers==1:
        for row in rows:
            r=run_row((str(root),row)); results.append(r); print({'task':r['task_id'],'status':r['status']},flush=True)
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
                done,_=wait(pending,return_when=FIRST_COMPLETED)
                for future in done:
                    pending.pop(future); r=future.result(); results.append(r)
                    print({'task':r['task_id'],'status':r['status']},flush=True)
                    if dc.stop_required(r): stopped=r['task_id']
                if not stopped:
                    for _ in done: submit()
    write(root/(stage+'_summary.json'),{'expected':len(rows),'completed':len(results),'wall_s':perf_counter()-start,
        'outcomes':dict(Counter(r['status'] for r in results)),'stop_task':stopped})
    if stopped: raise RuntimeError('Safety/program/rejudge stop at '+stopped)
    if stage=='first': write(root/'first_gate.json',{'passed':len(results)==18 and all(not dc.stop_required(r) for r in results),
        'rows':18,'counted_once':True,'nested_ID_mappings_checked_at_freeze':True,'scientific_failures_kept':True})


def rejudge(output):
    root=Path(output).resolve(); lock=locked(root,full=True); frozen=read(root/'inputs_frozen.json')
    checks=[]; integrations=[]; integral=dc.load_fine_evaluator(lock)
    for row in read(root/'manifest_360.json')['rows']:
        directory=root/'results'/row['task_id']
        if not (directory/'protocol_metrics.json').exists():
            checks.append({'task_id':row['task_id'],'status':'NOT_STARTED'}); continue
        replay=dc.independent_row(root,row); raw=read(directory/'metrics.json'); detail=read(directory/'protocol_metrics.json')
        replay['success_consistent']=replay['success']==raw['success']
        write(directory/'independent_rejudge_final.json',replay)
        checks.append({'task_id':row['task_id'],'runner_status':raw['termination_status'],
            'orchestration_status':detail['orchestration_status'],**{k:replay[k] for k in
                ('criteria_consistent','diagnostics_consistent','success_consistent','executed','success')}})
        if detail['target_valid']:
            initial=frozen['starts'][row['initial_key']]; b=load_boundary(Path(initial['physical']))
            field=arrays(Path(initial['condition'])/'demand.npz'); points=load_plan(root/'plans'/row['task_id']/'final').positions
            main=integral(b,field,points,2048); fine=integral(b,field,points,8192)
            record={'task_id':row['task_id'],'main':main,'fine':fine,
                'main_matches':all(abs(main[k]-detail['planned_cost'][k])<=1e-10 for k in main),
                'fine_matches':all(abs(fine[k]-detail['fine_cost'][k])<=1e-10 for k in fine)}
            integrations.append(record); write(directory/'independent_integration.json',record)
    verify_files(lock['historical_files'])
    write(root/'all_independent_checks.json',checks); write(root/'all_independent_integrations.json',integrations)
    print({'rows':len(checks),'integrations':len(integrations),'inconsistent':sum(any(c.get(k) is False for k in
        ('criteria_consistent','diagnostics_consistent','success_consistent')) for c in checks)},flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--stage',required=True,choices=['prepare','freeze','first','remaining','rejudge'])
    p.add_argument('--output',default=OUTPUT); p.add_argument('--spec',default=SPEC); p.add_argument('--workers',type=int,default=1)
    a=p.parse_args()
    if a.stage=='prepare': prepare(a.output,a.spec)
    elif a.stage=='freeze': freeze_inputs(a.output)
    elif a.stage=='rejudge': rejudge(a.output)
    else:
        try: run(a.output,a.stage,a.workers)
        except BaseException as exc:
            root=Path(a.output).resolve()
            write(root/'run_stop.json',{'stage':a.stage,'type':type(exc).__name__,'error':str(exc),
                'not_started':[r['task_id'] for r in read(root/'manifest_360.json')['rows']
                    if not (root/'results'/r['task_id']/'metrics.json').exists()]})
            raise


if __name__=='__main__': main()
