"""Read-only inventory for the Step-1 baseline PR; never run experiments."""
import hashlib
import json
from pathlib import Path
import re
import subprocess

from udc.experiments.provenance import provenance


BASE='6d9bc09dd56942fa7d45e1907ab09121f37d8a6f'
CANDIDATE='f9743abf8e36f32c8baa8c39c567697702dc1715'
SOURCE='83207e67b5feede1d5c8449053d8f764a5f09f9c50cf2d7bd21ff31acc9cf767'
DOCS=['README.md','docs/ARCHITECTURE.zh.md','docs/ROADMAP.zh.md',
      'docs/EXPERIMENT_PROTOCOL.zh.md','docs/DATA_AVAILABILITY.zh.md',
      'docs/STEP1_INTEGRATION_REVIEW.zh.md','docs/VALIDATION.zh.md']


def git(*args):return subprocess.check_output(['git',*args],text=True,encoding='utf-8')
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree(ref):
    values={}
    for line in git('ls-tree','-r','-l',ref).splitlines():
        info,path=line.split('\t',1);mode,kind,digest,size=info.split()
        values[path]={'path':path,'bytes':int(size),'git_blob':digest}
    return values


def main():
    current=tree(CANDIDATE);old=tree(BASE)
    deleted=[old[p] for p in git('diff','--name-only','--diff-filter=D',BASE+'...'+CANDIDATE).splitlines()]
    json_errors=[];json_count=0
    for p in current:
        if p.endswith('.json'):
            json_count+=1
            try:json.loads(Path(p).read_text(encoding='utf-8'))
            except Exception as exc:json_errors.append({'path':p,'error':str(exc)})
    links=[]
    for name in DOCS:
        for target in re.findall(r'\]\(([^)]+)\)',Path(name).read_text(encoding='utf-8')):
            if '://' in target or target.startswith('#'):continue
            local=target.split('#',1)[0].strip('<>')
            p=(Path(name).parent/local)
            links.append({'document':name,'target':target,'exists':p.exists()})
    historical=json.loads(Path('reports/step1_boundary_estimator_comparison_v1/raw_checksums.json').read_text())
    assert all(sha(p)==digest for p,digest in historical.items())
    assert provenance({},None,'main_readiness_docs_only',None)['source_sha256']==SOURCE
    reference=subprocess.run(['rg','-n',r'ABCG-Simulation|Test/|bottleneck_cfsm|getting_started',
        'src','tests','configs','experiments','.github'],capture_output=True,text=True)
    assert reference.returncode in (0,1)
    changed_science=git('diff',CANDIDATE,'--','src','configs','experiments','tests','pyproject.toml','requirements.txt','.github')
    assert not changed_science
    assert not json_errors and all(r['exists'] for r in links)
    assert reference.returncode==1,reference.stdout
    data={'base':BASE,'scientific_candidate':CANDIDATE,'source_sha256':SOURCE,
        'scope':'fixed pre-documentation Git trees; not final PR head/merge CI',
        'commits_ahead':int(git('rev-list','--count',BASE+'..'+CANDIDATE)),
        'changed_files':len(git('diff','--name-only',BASE+'...'+CANDIDATE).splitlines()),
        'deleted_files':deleted,'deletion_groups':{'ABCG-Simulation':sum(r['path'].startswith('ABCG-Simulation/') for r in deleted),'Test':sum(r['path'].startswith('Test/') for r in deleted)},
        'candidate_tree_bytes':sum(v['bytes'] for v in current.values()),
        'reports_tree_bytes':sum(v['bytes'] for p,v in current.items() if p.startswith('reports/')),
        'largest_tracked_files':sorted(current.values(),key=lambda v:v['bytes'],reverse=True)[:15],
        'historical_npz_retained':[v for p,v in current.items() if p.startswith('reports/') and p.endswith('.npz')],
        'tracked_json_checked':json_count,'json_errors':json_errors,'document_links':links,
        'removed_runtime_path_reference_count':0,'scientific_source_and_configs_unchanged':True,
        'frozen_comparison_files_unchanged':len(historical),
        'reused_local_tests':{'passed':175,'seconds':24.34,'source':'reports/step1_coverage_geometry_contract/verification.json'},
        'candidate_cross_platform_ci':{'run_id':39255838311,'ubuntu':'success','windows':'success',
            'url':'https://github.com/Wu-kaixin/Adaptive-Guide-Agent-Deployment-for-Unknown-Dynamic-Crowds/actions/runs/39255838311'},
        'new_experiment_rows':0,'new_input_draws':0,'historical_reports_deleted_or_rewritten':False}
    out=Path(__file__).with_name('review_scope.json')
    out.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({k:data[k] for k in ('changed_files','deletion_groups','candidate_tree_bytes','reports_tree_bytes','tracked_json_checked','frozen_comparison_files_unchanged','scientific_source_and_configs_unchanged')}))


if __name__=='__main__':main()
