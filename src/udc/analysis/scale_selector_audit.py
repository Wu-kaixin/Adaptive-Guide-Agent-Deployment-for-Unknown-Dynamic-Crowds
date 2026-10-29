"""Pinned selector measurements and independent source-box arithmetic replay."""
from fractions import Fraction as F
import math
from pathlib import Path
import traceback

import numpy as np

from .convex_transfer_audit import read,checked
from .native_precision_selectors import NativePrecisionSelectors
from .round_buffer_source_audit import contains_coordinates
from udc.multigroup.offset_interval import Interval
from udc.multigroup.precision_enclosure import PrecisionModel
from udc.multigroup.lower_scale_enclosure import lower_scale_precision_box
from udc.experiments.static_multigroup import write_json,digest
from udc.experiments.frozen_resume import atomic_json


def parent_input(m,index):
    task=m['tasks'][index];p=Path(m['parent'])/'inputs'/task['task_id'];q=Path(m['coordinate_parent'])/'inputs'/task['task_id']
    for folder,table in ((p,m['parent_receipts']),(q,m['coordinate_receipts'])):
        checked(folder/'receipt.json',table[task['task_id']])
        for n,h in read(folder/'receipt.json').items():checked(folder/n,h)
    return task,p,q


def worker(argument):
    study,index=argument;study=Path(study);m=read(study/'manifest.json');task,parent,coords=parent_input(m,index)
    folder=study/'inputs'/task['task_id'];folder.mkdir(parents=True,exist_ok=False);rows=[];current=None
    try:
        with NativePrecisionSelectors() as native:
            assert native.identity==m['native_identity']
            for old in read(parent/'records.json'):
                if (study/'STOP.json').exists():break
                with np.load(coords/old['group_file'],allow_pickle=False) as data:
                    before=data[old['source_stage']].copy();after=data[old['target_stage']].copy()
                magnitude=float(np.abs(before).max());distance=old['distance']
                current=dict(group_file=old['group_file'],stage=old['stage'],magnitude_hex=magnitude.hex(),distance_hex=distance.hex())
                native_result=native.evaluate(magnitude,distance,m['design']['buffer_precision_digits'])
                assert native_result['buffer_scales_hex']==native_result['common_expression_scales_hex']
                assert native_result['overlay_safe_scale_hex']==native_result['common_expression_safe_hex']
                scales=[float.fromhex(v) for v in native_result['buffer_scales_hex']]
                models=[PrecisionModel.from_scale(s) for s in scales];floor=min(q.scale for q in models)
                source=tuple(Interval(F(q[0]),F(q[1])) for q in old['enclosure']['raw_source_box'])
                bound,errors=lower_scale_precision_box(source,floor,stages=m['design']['quantizer_stages'])
                inside=contains_coordinates(bound,after)
                width=sum(q.hi-q.lo for q in source)
                row=dict(**current,native=native_result,normalized_scales=[str(q.scale) for q in models],scale_floor=str(floor),
                    source_box=[q.serial() for q in source],bounded_scale_box=[q.serial() for q in bound],
                    coordinate_displacement_bounds=[str(e) for e in errors],contains_saved_output=inside,
                    saved_output_coordinates=len(after),width_inflation_vs_raw=float(sum(q.hi-q.lo for q in bound)/width),
                    all_admissible_world_scales_proved=False,original_reference_enclosed=False,service_certificate=False)
                rows.append(row)
                if not inside:
                    write_json(folder/'counterexample.json',row);np.savez_compressed(folder/'counterexample_coordinates.npz',before=before,after=after)
                    atomic_json(study/'STOP.json',dict(reason='declared_lower_scale_coordinate_bound_refuted',input=task['task_id'],scientific_retry=False));break
    except BaseException:
        write_json(folder/'error.json',dict(current=current,partial_records=rows,traceback=traceback.format_exc(),scientific_retry=False));raise
    write_json(folder/'records.json',rows)
    result=dict(task_id=task['task_id'],stage_transfers=len(rows),saved_output_coordinates=sum(r['saved_output_coordinates'] for r in rows),
        refuted=any(not r['contains_saved_output'] for r in rows),native_point_creations=len(rows),native_selector_calls=16*len(rows),
        minimum_scale_floor=min(float(F(r['scale_floor'])) for r in rows),maximum_width_inflation=max(r['width_inflation_vs_raw'] for r in rows),
        minimum_width_inflation=min(r['width_inflation_vs_raw'] for r in rows))
    write_json(folder/'result.json',result)
    names=['records.json','result.json']+(['counterexample.json','counterexample_coordinates.npz'] if result['refuted'] else [])
    write_json(folder/'receipt.json',{n:digest(folder/n) for n in names});return result


def exact_decimal_magnitude(value):
    value=F.from_float(value);k=0
    while value>=10**(k+1):k+=1
    while value<F(10)**k:k-=1
    return k+1


def calibrate(study):
    m=read(study/'manifest.json');design=m['design'];records=[]
    with NativePrecisionSelectors() as native:
        assert native.identity==m['native_identity']
        powers={str(k):native.precision(1.,k+1).hex() for k in range(design['power_table_exponents'][0],design['power_table_exponents'][1]+1)}
        reverse={v:int(k) for k,v in powers.items()};assert len(reverse)==len(powers)
        for exponent in design['calibration_decimal_exponents']:
            center=float(F(10)**exponent)
            for magnitude in (math.nextafter(center,0.),center,math.nextafter(center,math.inf)):
                for distance in design['calibration_distances']:
                    r=native.evaluate(magnitude,distance,design['buffer_precision_digits'])
                    assert r['buffer_scales_hex']==r['common_expression_scales_hex'] and r['overlay_safe_scale_hex']==r['common_expression_safe_hex']
                    expanded=float.fromhex(r['expanded_magnitude_hex']);comment_digits=exact_decimal_magnitude(expanded)
                    actual_digits=[p-reverse[s] for p,s in zip(design['buffer_precision_digits'],r['buffer_scales_hex'])]
                    assert len(set(actual_digits))==1
                    records.append(dict(magnitude_hex=magnitude.hex(),distance_hex=distance.hex(),native=r,
                        inferred_computed_magnitude_digits=actual_digits[0],exact_floor_log10_plus_one=comment_digits,
                        differs_from_decimal_comment=actual_digits[0]!=comment_digits))
    write_json(study/'calibration.json',dict(native_power_table_hex=powers,records=records,
        native_point_creations=len(records),native_selector_calls=16*len(records)+len(powers),
        decimal_comment_differences=sum(r['differs_from_decimal_comment'] for r in records),
        finite_probes_are_all_input_selector_proof=False))


def audit(study,output):
    study,output=Path(study),Path(output);output.mkdir(parents=True,exist_ok=False);m=read(study/'manifest.json')
    checked(study/'manifest.json',read(study/'seal.json')['manifest_sha256']);complete=read(study/'completion.json')
    assert complete['completed'];counts=dict(inputs_checked=0,stage_transfers=0,saved_output_coordinates=0,native_point_creations=0,native_selector_calls=0)
    u=F(1,2**53);eta=F(1,2**1075)
    with NativePrecisionSelectors() as native:
        assert native.identity==m['native_identity']
        for index,task in enumerate(m['tasks']):
            folder=study/'inputs'/task['task_id'];checked(folder/'receipt.json',complete['receipts'][task['task_id']])
            for n,h in read(folder/'receipt.json').items():checked(folder/n,h)
            _,parent,coords=parent_input(m,index);original=read(parent/'records.json');rows=read(folder/'records.json')
            assert len(rows)==len(original)
            for row,old in zip(rows,original):
                with np.load(coords/old['group_file'],allow_pickle=False) as data:
                    before=data[old['source_stage']].copy();after=data[old['target_stage']].copy()
                magnitude=float(np.abs(before).max());distance=old['distance']
                assert magnitude.hex()==row['magnitude_hex'] and distance.hex()==row['distance_hex']
                assert row['group_file']==old['group_file'] and row['stage']==old['stage']
                actual=native.evaluate(magnitude,distance,m['design']['buffer_precision_digits']);assert actual==row['native']
                raw=[F.from_float(float.fromhex(s)) for s in actual['buffer_scales_hex']]
                # Every measured old-stage scale is an exact binary64 integer
                # >=1: near-integer normalization leaves it unchanged.
                assert all(v>=1 and v.denominator==1 for v in raw)
                assert raw==list(map(F,row['normalized_scales'])) and min(raw)==F(row['scale_floor'])
                assert row['source_box']==old['enclosure']['raw_source_box']
                errors=[];bound=[]
                for lo,hi in row['source_box']:
                    lo,hi=F(lo),F(hi);size=max(abs(lo),abs(hi));error=F(0)
                    for _ in range(m['design']['quantizer_stages']):
                        first=u*(size+error)+(eta+F(1,2))/min(raw)
                        last=u*(size+error+first)+eta
                        error+=first+last
                    errors.append(error);bound.append((lo-error,hi+error))
                assert errors==list(map(F,row['coordinate_displacement_bounds']))
                assert bound==[tuple(map(F,q)) for q in row['bounded_scale_box']]
                assert all(lo<=F.from_float(float(a))<=F.from_float(float(b))<=hi for (lo,hi),a,b in zip(bound,after.min(axis=0),after.max(axis=0)))
                assert row['contains_saved_output'] and not row['all_admissible_world_scales_proved'] and not row['service_certificate']
                counts['stage_transfers']+=1;counts['saved_output_coordinates']+=len(after);counts['native_point_creations']+=1;counts['native_selector_calls']+=16
            counts['inputs_checked']+=1
        calibration=read(study/'calibration.json');checked(study/'calibration.json',complete['calibration_sha256'])
        for k,h in calibration['native_power_table_hex'].items():assert native.precision(1.,int(k)+1).hex()==h;counts['native_selector_calls']+=1
        reverse={v:int(k) for k,v in calibration['native_power_table_hex'].items()}
        diagnoses=[]
        for row in calibration['records']:
            assert native.evaluate(float.fromhex(row['magnitude_hex']),float.fromhex(row['distance_hex']),m['design']['buffer_precision_digits'])==row['native']
            value=F.from_float(float.fromhex(row['native']['expanded_magnitude_hex']))
            if value>=1:
                decimal_digits=len(str(value.numerator//value.denominator))
            else:
                scaled=value;steps=0
                while scaled<1:scaled*=10;steps+=1
                decimal_digits=1-steps
            exact_trunc=decimal_digits
            if value<F(1,10) and value!=F(10)**(decimal_digits-1):exact_trunc+=1
            actual={p-reverse[h] for p,h in zip(m['design']['buffer_precision_digits'],row['native']['buffer_scales_hex'])}
            assert len(actual)==1;actual=actual.pop()
            assert actual==row['inferred_computed_magnitude_digits'] and decimal_digits==row['exact_floor_log10_plus_one']
            assert (actual!=decimal_digits)==row['differs_from_decimal_comment']
            diagnoses.append(dict(magnitude_hex=row['magnitude_hex'],distance_hex=row['distance_hex'],
                expanded_magnitude_hex=row['native']['expanded_magnitude_hex'],actual_magnitude_digits=actual,
                exact_decimal_comment_digits=decimal_digits,exact_real_log_truncation_digits=exact_trunc,
                comment_vs_truncation=decimal_digits!=exact_trunc,native_vs_exact_truncation=actual!=exact_trunc))
            counts['native_selector_calls']+=16;counts['native_point_creations']+=1
        write_json(output/'calibration_diagnoses.json',diagnoses)
    write_json(output/'audit.json',dict(all_match=True,**counts,
        calibration_comment_vs_truncation=sum(r['comment_vs_truncation'] for r in diagnoses),
        calibration_native_vs_exact_truncation=sum(r['native_vs_exact_truncation'] for r in diagnoses),
        full_native_buffer_or_overlay_calls=0,new_independent_crowd_inputs=0,
        new_motion_executions=0,all_admissible_world_scales_proved=False,service_certificate=False))
