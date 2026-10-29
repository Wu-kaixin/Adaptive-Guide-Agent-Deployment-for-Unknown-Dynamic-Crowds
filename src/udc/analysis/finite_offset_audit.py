"""Finite-branch offset checks; undefined cases remain in full accounting."""
from fractions import Fraction as F
import math
from pathlib import Path

import numpy as np

from .convex_transfer_audit import read,checked
from udc.multigroup.offset_interval import Interval,point_box
from udc.multigroup.finite_offset_enclosure import finite_offset_box
from udc.experiments.static_multigroup import write_json,digest
from udc.experiments.frozen_resume import atomic_json


def numeric_expression(points,distance,side):
    operations=[]
    class Nonfinite(Exception):pass
    def op(name,a,b=None):
        if name=='sqrt':
            value=math.sqrt(a)
        else:
            exact={'sub':lambda:F(a)-F(b),'add':lambda:F(a)+F(b),'mul':lambda:F(a)*F(b),'div':lambda:F(a)/F(b)}[name]()
            try:rounded=float(exact)
            except OverflowError:raise Nonfinite(name)
            if not math.isfinite(rounded):raise Nonfinite(name)
            value={'sub':lambda:a-b,'add':lambda:a+b,'mul':lambda:a*b,'div':lambda:a/b}[name]()
        if not math.isfinite(value):raise Nonfinite(name)
        operations.append({'operation':name,'arguments_hex':[float(a).hex()]+([] if b is None else [float(b).hex()]),'output_hex':value.hex()})
        return value
    a,b=points
    try:
        dx,dy=op('sub',b[0],a[0]),op('sub',b[1],a[1])
        sx,sy=op('mul',dx,dx),op('mul',dy,dy);q=op('add',sx,sy)
        if q==0:
            return {'status':'UNDEFINED_ZERO_COMPUTED_NORM','reason':'coincident_endpoints' if dx==dy==0 else 'distinct_endpoints_squared_norm_underflow',
                'operations':operations,'output':None}
        length=op('sqrt',q);signed=side*distance
        ux,uy=op('div',op('mul',signed,dx),length),op('div',op('mul',signed,dy),length)
        output=[[op('sub',p[0],uy),op('add',p[1],ux)] for p in points]
        return {'status':'FINITE_OFFSET_OUTPUT','operations':operations,'output':output,
            'computed_delta':[dx,dy],'rounded_squares':[sx,sy],'squared_norm':q,'length':length,'normal_components':[ux,uy]}
    except Nonfinite as exc:
        return {'status':'UNCERTIFIED_FINITE_ARITHMETIC','reason':str(exc),'operations':operations,'output':None}


def execute(study):
    study=Path(study);m=read(study/'manifest.json');results=[];counterexamples=[]
    for case in m['cases']:
        a,b=[point_box(p,case['coordinate_uncertainty_m']) for p in case['center_points']]
        result=finite_offset_box(a,b,Interval.point(m['design']['offset_distance_m']),m['design']['side'])
        traces=[]
        for index,points in enumerate(case['input_samples']):
            row=numeric_expression(points,m['design']['offset_distance_m'],m['design']['side'])
            if row['status']=='FINITE_OFFSET_OUTPUT':
                if any(not q.contains(F.from_float(v)) for p,bounds in zip(row['output'],result.endpoints) for q,v in zip(bounds,p)):
                    write_json(study/'failed_trace.json',{'case':case,'trace':row,'enclosure':result.serial()})
                    raise ValueError('MODEL_BOUND_VIOLATION: finite offset output escaped conditional box')
                if any(abs(F.from_float(v))>F.from_float(m['design']['offset_distance_m']) for v in row['normal_components']):
                    counterexamples.append({'case_id':case['case_id'],'sample_index':index,'input_points':points,
                        'distance':m['design']['offset_distance_m'],'normal_components':row['normal_components'],
                        'squared_norm':row['squared_norm'],'length':row['length'],
                        'scope':'source-expression unit-component diagnostic only; not an observed native C++ or deployment violation'})
            traces.append(row)
        folder=study/'cases'/case['case_id'];folder.mkdir(parents=True,exist_ok=False)
        write_json(folder/'enclosure.json',result.serial());write_json(folder/'traces.json',traces)
        write_json(folder/'receipt.json',{name:digest(folder/name) for name in ['enclosure.json','traces.json']})
        results.append({'case_id':case['case_id'],'kind':case['kind'],'parent_case_id':case.get('parent_case_id'),
            'parent_full_family_status':case.get('parent_full_family_status'),'trace_count':len(traces),
            'finite_outputs':sum(t['status']=='FINITE_OFFSET_OUTPUT' for t in traces),
            'zero_computed_norm':sum(t['status']=='UNDEFINED_ZERO_COMPUTED_NORM' for t in traces),
            'finite_domain_refusals':sum(t['status']=='UNCERTIFIED_FINITE_ARITHMETIC' for t in traces),
            'operation_checks':sum(len(t['operations']) for t in traces),'all_inputs_defined':False})
        atomic_json(study/'status.json',{'completed_cases':len(results),'scheduled_cases':len(m['cases'])})
    write_json(study/'results.json',results);write_json(study/'unit_normal_counterexamples.json',counterexamples)
    summary={'scheduled_cases':len(m['cases']),'completed_cases':len(results),
        **{k:sum(r[k] for r in results) for k in ['trace_count','finite_outputs','zero_computed_norm','finite_domain_refusals','operation_checks']},
        'unit_normal_diagnostic_counterexamples':len(counterexamples),
        'parent_full_family_refusals_preserved':sum(r['kind']=='parent_offset' and r['parent_full_family_status']=='UNCERTIFIED' for r in results),
        'all_family_domain_certification_restored':False,'new_native_geometry_calls':0,'new_independent_samples':0,
        'new_motion_executions':0,'runtime_service_gate_changed':False,'whole_geometry_pipeline_proved':False,'goal_complete':False}
    write_json(study/'summary.json',summary)


def audit(study,output):
    study,output=Path(study),Path(output);output.mkdir(parents=True,exist_ok=False);m=read(study/'manifest.json')
    checked(study/'manifest.json',read(study/'seal.json')['manifest_sha256'])
    operations=finite=zero=refused=counter=0;u=F(1,2**53);eta=F(1,2**1075);min_length=F(1,2**537)
    parent=Path(m['parent']);checked(parent/'manifest.json',m['parent_manifest_sha256'])
    for name,h in m['parent_receipts'].items():
        checked(parent/name,h)
        for artifact,value in read(parent/name).items():checked((parent/name).parent/artifact,value)
    for case in m['cases']:
        if case['kind'].startswith('parent_'):
            original=parent/'cases'/case['parent_case_id'];old_result=read(original/'result.json')
            if old_result['status']!=case['parent_full_family_status']:raise ValueError('Old full-family status changed')
            if case['kind']=='parent_offset':
                with np.load(original/'input.npz',allow_pickle=False) as data:
                    if not np.array_equal(data['points'],case['center_points']):raise ValueError('Old endpoint centers changed')
                if case['input_samples']!=[r['input_points'] for r in read(original/'traces.json')]:raise ValueError('Old samples changed')
            elif case['center_points']!=[old_result['admissible_zero_length_witness']]*2:
                raise ValueError('Old singular witness changed')
        folder=study/'cases'/case['case_id']
        for name,h in read(folder/'receipt.json').items():checked(folder/name,h)
        bound=read(folder/'enclosure.json');cap=F(bound['normal_displacement_limit']);d=F.from_float(m['design']['offset_distance_m'])
        root=(cap-(1+u)*eta/min_length-eta)*(1-u)/((1+u)**2*d)
        radicand=F(3,2)/(1-u);unit=F(1,2**160)
        if root*root<radicand or (root-unit)**2>=radicand or (root/unit).denominator!=1:
            raise ValueError('Analytic cap root bracket not independently verified')
        if bound['all_inputs_defined'] or bound['service_certificate']:raise ValueError('Conditional-domain limitation lost')
        for center,endpoint in zip(case['center_points'],bound['bounds']):
            e=F.from_float(case['coordinate_uncertainty_m'])
            for c,q in zip(center,endpoint):
                lo,hi=F.from_float(c)-e,F.from_float(c)+e;a,b=float(lo),float(hi)
                if F.from_float(a)<lo:a=math.nextafter(a,math.inf)
                if F.from_float(b)>hi:b=math.nextafter(b,-math.inf)
                if list(map(F,q))!=[F.from_float(float(F.from_float(a)-cap)),F.from_float(float(F.from_float(b)+cap))]:
                    raise ValueError('Endpoint finite-output range formula differs')
        traces=read(folder/'traces.json')
        if len(traces)!=len(case['input_samples']):raise ValueError('Expression trace denominator changed')
        for points,row in zip(case['input_samples'],traces):
            for source,point in zip(case['center_points'],points):
                error=F.from_float(case['coordinate_uncertainty_m'])
                if any(not F.from_float(c)-error<=F.from_float(v)<=F.from_float(c)+error for c,v in zip(source,point)):
                    raise ValueError('Expression input escaped its declared endpoint box')
            vals=[float.fromhex(op['output_hex']) for op in row['operations']]
            a,b=points;signed=m['design']['side']*m['design']['offset_distance_m']
            bindings=[('sub',lambda:[b[0],a[0]]),('sub',lambda:[b[1],a[1]]),
                ('mul',lambda:[vals[0],vals[0]]),('mul',lambda:[vals[1],vals[1]]),
                ('add',lambda:[vals[2],vals[3]]),('sqrt',lambda:[vals[4]]),
                ('mul',lambda:[signed,vals[0]]),('div',lambda:[vals[6],vals[5]]),
                ('mul',lambda:[signed,vals[1]]),('div',lambda:[vals[8],vals[5]]),
                ('sub',lambda:[a[0],vals[9]]),('add',lambda:[a[1],vals[7]]),
                ('sub',lambda:[b[0],vals[9]]),('add',lambda:[b[1],vals[7]])]
            if len(vals)>len(bindings):raise ValueError('Extra expression operations')
            for index,op in enumerate(row['operations']):
                name,args_for=bindings[index]
                if op['operation']!=name or op['arguments_hex']!=[float(v).hex() for v in args_for()]:
                    raise ValueError('Operation trace is not anchored to the original expression inputs')
                args=[F.from_float(float.fromhex(v)) for v in op['arguments_hex']];value=float.fromhex(op['output_hex']);vf=F.from_float(value)
                if op['operation']=='sqrt':
                    lower=(F.from_float(math.nextafter(value,-math.inf))+vf)/2
                    upper=(F.from_float(math.nextafter(value,math.inf))+vf)/2
                    if not lower*lower<=args[0]<=upper*upper:raise ValueError('Sqrt nearest bracket violated')
                else:
                    exact={'sub':lambda:args[0]-args[1],'add':lambda:args[0]+args[1],
                        'mul':lambda:args[0]*args[1],'div':lambda:args[0]/args[1]}[op['operation']]()
                    if F.from_float(float(exact))!=vf:raise ValueError('Basic operation nearest value differs')
                operations+=1
            if row['status']=='FINITE_OFFSET_OUTPUT':
                if len(vals)!=14 or row['computed_delta']!=vals[:2] or row['rounded_squares']!=vals[2:4] or row['squared_norm']!=vals[4] or row['length']!=vals[5]:
                    raise ValueError('Finite trace source summaries differ')
                if row['normal_components']!=[vals[7],vals[9]] or row['output']!=[vals[10:12],vals[12:14]]:
                    raise ValueError('Finite output is not the final expression trace')
                q=F.from_float(row['squared_norm']);length=F.from_float(row['length'])
                if q<F(1,2**1074) or length<min_length:raise ValueError('Positive norm/length floor differs')
                for component,square in zip(row['computed_delta'],row['rounded_squares']):
                    if not F.from_float(square)<=q or F.from_float(component)**2>(q+eta)/(1-u):
                        raise ValueError('Norm-component dependency inequality differs')
                if any(abs(F.from_float(v))>cap for v in row['normal_components']):raise ValueError('Analytic normal cap violated')
                for point,limit in zip(row['output'],bound['bounds']):
                    if any(not F(q[0])<=F.from_float(v)<=F(q[1]) for v,q in zip(point,limit)):raise ValueError('Returned offset box differs')
                counter+=any(abs(F.from_float(v))>d for v in row['normal_components']);finite+=1
            else:
                # Admission/status replay is a separate explicit check. The
                # operation algebra above is independent of this reproducer.
                other=numeric_expression(points,m['design']['offset_distance_m'],m['design']['side'])
                if other['status']!=row['status'] or other['reason']!=row['reason']:raise ValueError('Undefined/domain refusal changed')
                zero+=row['status']=='UNDEFINED_ZERO_COMPUTED_NORM';refused+=row['status']=='UNCERTIFIED_FINITE_ARITHMETIC'
    if counter!=len(read(study/'unit_normal_counterexamples.json')):raise ValueError('Ideal-normal diagnostic count changed')
    write_json(output/'audit.json',{'study_manifest_sha256':digest(study/'manifest.json'),'cases_checked':len(m['cases']),
        'finite_outputs_checked':finite,'zero_computed_norms_preserved':zero,'finite_domain_refusals_preserved':refused,
        'nearest_operation_checks':operations,'unit_normal_diagnostic_counterexamples':counter,'all_match':True,
        'all_family_domain_certification_restored':False,'new_native_geometry_calls':0,'new_independent_samples':0,
        'new_motion_executions':0,'runtime_service_gate_changed':False,'whole_geometry_pipeline_proved':False})
