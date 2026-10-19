"""Offline saved-geometry audit; no input generation, planning or control."""
import argparse
from collections import Counter
import csv
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
from time import perf_counter

import numpy as np
import shapely
from shapely.geometry import Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
import yaml

from udc.boundary.coverage import (audit_person_disk_coverage,
    exact_point_boundary_distance, radial_discretization_error,exact_convex_reference_gap_enclosure,
    CONTRACT_VERSION)
from udc.experiments.provenance import provenance


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path,value):
    path=Path(path);path.parent.mkdir(exist_ok=True,parents=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_contract_spec(spec):
    frozen={'protocol':'step1_coverage_geometry_contract_v1','contract_version':CONTRACT_VERSION,
        'canonical_reference_quad_segs':32,'legacy_reference_quad_segs':16,
        'candidate_mode':'strict_diagnostic_only','default_estimator_acceptance_change':False,
        'default_evaluator_acceptance_change':False,'approximate_acceptance_enabled':False,
        'coverage_acceptance_tolerance_m':0.,'geometry_coordinate_displacement_m':0.,
        'analytic_disk_strict_containment_claim':False,'redraw_inputs':False,
        'controller_executions':0,'workers':1,'numerical_threads':1}
    if any(spec.get(k)!=v for k,v in frozen.items()):
        raise ValueError('Changed coverage policy requires a new contract version; this entry is diagnostic only')


def polygon_structure(geometry):
    parts=[geometry] if geometry.geom_type=='Polygon' else list(geometry.geoms)
    return {'holes':sum(len(p.interiors) for p in parts),'components':len(parts)}


def saved_audit(root,row,out):
    task=row['task_id']; artifacts=root/'future/plans'/task
    cell=root/'future_inputs'/row['scene']/f"seed_{row['seed']}"
    with np.load(cell/'input.npz') as data:
        people,radii=data['people'].copy(),data['radii'].copy()
    occupied=shapely.from_wkb((artifacts/'occupied.wkb').read_bytes())
    assert shapely.from_wkb(occupied.wkb).wkb==occupied.wkb
    if (artifacts/'boundary.npz').exists():
        with np.load(artifacts/'boundary.npz') as boundary:
            outer=Polygon(boundary['crowd'])
        installed=True
    else:
        outer=Polygon(occupied.exterior);installed=False
    results={}
    for name,region in [('full_occupied',occupied),('controller_outer' if installed else 'extracted_outer_not_installed',outer)]:
        results[name]={str(q):audit_person_disk_coverage(region,people,radii,quad_segs=q) for q in (16,32)}
    record={'task_id':task,'source_input':str(cell/'input.npz'),'input_file_sha256':sha(cell/'input.npz'),
        'source_occupied_wkb':str(artifacts/'occupied.wkb'),'occupied_sha256':sha(artifacts/'occupied.wkb'),
        'saved_only_before_reconstruction':True,'controller_outer_installed':installed,
        'wkb_read_write_coordinate_loss':False,'outer_fills_holes':len(occupied.interiors),
        'geometry_coordinate_displacement_m':0.,'results':results}
    write(out/'saved_audits'/task/'audit.json',record)
    return record


def seed70_trace(root,out,saved):
    """Called only after the saved-only seed70 audit has been written."""
    assert (out/'saved_audits/wide_u__seed_70__disk_union_closed/audit.json').exists()
    cell=root/'future_inputs/wide_u/seed_70'
    with np.load(cell/'input.npz') as data:
        people,radii=data['people'].copy(),data['radii'].copy()
    artifact=root/'future/plans/wide_u__seed_70__disk_union_closed'
    occupied=shapely.from_wkb((artifact/'occupied.wkb').read_bytes())
    q16=Point(people[16]).buffer(float(radii[16]),quad_segs=16)
    point=list(q16.exterior.coords)[37]
    # Exhaust all saved segments in rational arithmetic, no GEOS distance selection.
    exact=exact_point_boundary_distance(point,occupied)
    exact['reference_person_index']=16;exact['reference_q16_vertex_index']=37
    exact['reference_q32_vertex_index']=74
    write(out/'seed70_saved_exact_witness.json',exact)
    disks=[Point(p).buffer(float(r),quad_segs=32) for p,r in zip(people,radii)]
    original=unary_union(disks)
    assert original.wkb==(artifact/'physical_reference_disks.wkb').read_bytes()
    dilated=original.buffer(.3,quad_segs=32)
    eroded=dilated.buffer(-.3,quad_segs=32)
    restored=unary_union([original,eroded])
    assert restored.wkb==occupied.wkb, 'Diagnostic reconstruction differs from actual saved occupied set'
    oriented=orient(restored,sign=1)
    exterior=Polygon(oriented.exterior)
    stages=[]
    for name,g in [('original_union',original),('dilation_0_30',dilated),('erosion_0_30',eroded),
            ('restore_original_union',restored),('orient',oriented),('extract_outer',exterior),
            ('wkb_roundtrip',shapely.from_wkb(restored.wkb))]:
        p=out/'seed70_chain'/f'{name}.wkb';p.parent.mkdir(exist_ok=True,parents=True);p.write_bytes(g.wkb)
        stage={'stage':name,'wkb_sha256':sha(p),'person16':{},'is_valid':bool(g.is_valid),
            **polygon_structure(g),'saved_occupied_bitwise_match':g.wkb==occupied.wkb,
            'all_person32_strict':all(g.covers(d) for d in disks)}
        for q in (16,32):
            d=Point(people[16]).buffer(float(radii[16]),quad_segs=q);diff=d.difference(g)
            stage['person16'][str(q)]={'strict_covers':bool(g.covers(d)), 'de9im':g.relate(d),
                'difference_wkt':diff.wkt,'difference_type':diff.geom_type,
                'difference_area_m2':float(diff.area),'difference_length_m':float(diff.length)}
        stages.append(stage)
    write(out/'seed70_chain.json',{'diagnostic_reconstruction_only':True,'no_result_replacement':True,
        'saved_before_reconstruction':True,'restoration_matches_saved_wkb':True,'stages':stages})


def run(spec_path,output=None):
    spec=yaml.safe_load(Path(spec_path).read_text(encoding='utf-8'))
    validate_contract_spec(spec)
    root,out=Path(spec['input_root']),Path(output or spec['output'])
    if out.exists():raise FileExistsError('Never overwrite a coverage audit attempt')
    # Freeze hash references to all prior comparison/protocol artifacts before reads.
    historical=read('reports/step1_boundary_estimator_comparison_v1/raw_checksums.json')
    assert all(sha(path)==digest for path,digest in historical.items())
    out.mkdir(parents=True)
    started=perf_counter()
    write(out/'preflight.json',{'spec':spec,'spec_sha256':sha(spec_path),
        'provenance':provenance({},None,'coverage_diagnostic_only',None),
        'python_executable':sys.executable,'GEOS':shapely.geos_version_string,
        'dependencies':{name:importlib.metadata.version(name) for name in ('numpy','scipy','shapely','jupedsim')},
        'historical_files_checked':len(historical),'original_files_unchanged_before':True})
    rows=[r for r in read(root/'future_summary.json')['rows'] if r['common_input_legal']]
    assert len(rows)==102
    # The actual saved geometry is examined first; only then reconstruct chain.
    seed=next(r for r in rows if r['task_id']=='wide_u__seed_70__disk_union_closed')
    seed_record=saved_audit(root,seed,out)
    seed70_trace(root,out,seed_record)
    records=[seed_record]+[saved_audit(root,r,out) for r in rows if r is not seed]
    ledger=[]
    for record in records:
        estimator='convex_hull' if record['task_id'].endswith('__convex_hull') else 'disk_union_closed'
        for scope,reports in record['results'].items():
            for q,report in reports.items():
                ledger.append({'task_id':record['task_id'],'estimator':estimator,'scope':scope,'q':int(q),
                    'strict_individuals':report['raw_strict_individuals_all'],'strict_union_overlay':report['raw_strict_union_overlay'],
                    'candidate_accepted':report['candidate_accepted'],
                    'failed_people':sum(not p['raw_strict_covers'] for p in report['persons']),
                    'exact_outside_vertex_certificates':sum(p['exact_outside_vertex_witness'] is not None for p in report['persons']),
                    'difference_empty_failed_people':sum(not p['raw_strict_covers'] and p['difference_empty'] for p in report['persons']),
                    'holes':report['region_holes'],'coordinate_change_m':0.,'default_behavior_changed':False})
    counts={}
    for estimator in ('convex_hull','disk_union_closed'):
        for kind in ('full','outer'):
            for q in (16,32):
                subset=[r for r in ledger if r['estimator']==estimator and r['q']==q and
                    (r['scope']=='full_occupied' if kind=='full' else r['scope']!='full_occupied')]
                counts[f'{estimator}/{kind}/q{q}']={'rows':len(subset),'strict_accept':sum(r['strict_individuals'] for r in subset),
                    'strict_reject':sum(not r['strict_individuals'] for r in subset),
                    'exact_outside_vertex_certificates':sum(r['exact_outside_vertex_certificates'] for r in subset),
                    'failed_people':sum(r['failed_people'] for r in subset),
                    'failed_people_empty_difference':sum(r['difference_empty_failed_people'] for r in subset)}
    changes=[]
    for record in records:
        scope=next(name for name in record['results'] if name!='full_occupied')
        reports=record['results'][scope]
        changes.append({'task_id':record['task_id'],'legacy_q16':reports['16']['candidate_accepted'],
            'candidate_q32':reports['32']['candidate_accepted'],'default_behavior_changed':False,
            'geometry_changed':False,'classification_change':reports['16']['candidate_accepted']!=reports['32']['candidate_accepted']})
    old=[]
    for task in ('elongated__seed_35__disk_union_closed','tight_u__seed_41__disk_union_closed'):
        a=root/'smoke/plans'/task;g=shapely.from_wkb((a/'occupied.wkb').read_bytes())
        scene,seednum=task.split('__')[0],int(task.split('__')[1].split('_')[1])
        ref=read(root/'protocol.json')['references'][f'{scene}__seed_{seednum}']
        with np.load(Path(ref['cell'])/'input.npz') as data:
            audits={str(q):audit_person_disk_coverage(g,data['people'],data['radii'],quad_segs=q) for q in (16,32)}
        old.append({'task_id':task,'supplementary_saved_case_not_new_sample':True,'occupied_type':g.geom_type,
            'original_status':read(root/'smoke/results'/task/'metrics.json')['termination_status'],
            'supported_single_controller_component':g.geom_type=='Polygon','audit':audits})
    write(out/'old_rejection_checks.json',old)
    assert all(sha(path)==digest for path,digest in historical.items())
    summary={'audited_legal_input_units':51,'audited_estimator_rows':102,'region_reference_checks':len(ledger),
        'counts':counts,'classification_changes':changes,'changed_default_classifications':0,
        'changed_geometries':0,'controller_executions':0,'approximate_acceptance_enabled':False,
        'decision':'retain legacy default; canonical q32 strict is a diagnostic candidate, no tolerance-based repair',
        'historical_files_unchanged_after':True,'historical_file_count':len(historical),
        'radial_discretization_error_m':{str(r):{str(q):radial_discretization_error(r,q) for q in (16,32)} for r in (.05,.2,.5)},
        'wall_seconds':perf_counter()-started}
    write(out/'audit_summary.json',summary)
    with (out/'audit_102_regions_q16_q32.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(ledger[0]));writer.writeheader();writer.writerows(ledger)
    write(out/'checksums.json',{str(p):sha(p) for p in sorted(out.rglob('*')) if p.is_file()})
    print(json.dumps({k:v for k,v in summary.items() if k not in ('classification_changes',)},ensure_ascii=False))


def local_proof(spec_path,output):
    """Append a distinct certificate after the saved-only full audit; no overlays."""
    spec=yaml.safe_load(Path(spec_path).read_text(encoding='utf-8'))
    validate_contract_spec(spec)
    root=Path(spec['input_root']);out=Path(output or spec['output'])
    destination=out/'seed70_exact_complete_q16_gap_enclosure.json'
    if destination.exists():raise FileExistsError('Never overwrite a local certificate')
    ref=read(out/'saved_audits/wide_u__seed_70__disk_union_closed/audit.json')
    assert sha(ref['source_input'])==ref['input_file_sha256']
    assert sha(ref['source_occupied_wkb'])==ref['occupied_sha256']
    with np.load(ref['source_input']) as data:
        reference=Point(data['people'][16]).buffer(float(data['radii'][16]),quad_segs=16)
    region=shapely.from_wkb(Path(ref['source_occupied_wkb']).read_bytes())
    started=perf_counter();proof=exact_convex_reference_gap_enclosure(reference,region)
    proof.update(provenance=provenance({},None,'exact_saved_polygon_local_diagnostic',None),
        source_input_sha256=ref['input_file_sha256'],source_occupied_sha256=ref['occupied_sha256'],
        person_index=16,reference_quad_segs=16,wall_seconds=perf_counter()-started)
    write(destination,proof)
    print(json.dumps({k:v for k,v in proof.items() if k not in ('provenance','gap_enclosing_box_exact','gap_boundary_components')},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec',default='configs/step1/coverage_geometry_contract_v1.yaml')
    parser.add_argument('--output',help='New diagnostic attempt directory; existing evidence is never overwritten')
    parser.add_argument('--local-proof',action='store_true',help='Append an exact all-edge gap enclosure to a completed audit')
    args=parser.parse_args()
    if args.local_proof:local_proof(args.spec,args.output)
    else:run(args.spec,args.output)
