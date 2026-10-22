"""Export reconstruction tables and reuse the existing paired estimators."""
from pathlib import Path
from collections import Counter
import csv
import json

import yaml

from rebuild_missing_data import PROJECT, OUTPUT, SUITES, read, write, sha

def main():
    import sys
    sys.path.insert(0, str(PROJECT / 'src'))
    from udc.analysis.resource_response import units
    from udc.analysis.resource_confirmation import inference, motion_subsets
    from udc.analysis.demand_confirmation import paired_units, contrast, stratified_bootstrap
    report = PROJECT / 'reports/project_rebuild_20261023/reconstruction'
    report.mkdir(parents=True, exist_ok=True)
    overview = []
    for suite, (name, kind, ref) in SUITES.items():
        root = OUTPUT / ('step1_' + name)
        summary = read(root / 'reconstruction_summary.json')
        original_spec = PROJECT / f'configs/step1/{name}.yaml'
        requested = yaml.safe_load(original_spec.read_text(encoding='utf-8'))
        write(root / 'requested_protocol_factors.json', {'source_file': str(original_spec), 'source_sha256': sha(original_spec),
              'original_protocol': requested, 'reconstruction_suite': suite,
              'runtime_driver': str(PROJECT / 'scripts/rebuild_missing_data.py'),
              'notes': 'All-phase suites share the demand runner context, with fixed original seeds and resource increments; no seed audit for new independence is claimed.'})
        table = []
        manifest = read(root / 'manifest.json'); rows = manifest.get('rows', manifest.get('tasks'))
        details = []
        for row in rows:
            directory = root / 'baseline' / row['scene'] / f'seed_{row["seed"]}' / row['method'] if kind == 'nonconvex' else root / 'results' / row['task_id']
            metrics = read(directory / 'metrics.json')
            detail = read(directory / 'protocol_metrics.json') if (directory / 'protocol_metrics.json').exists() else {}
            if detail: details.append(detail)
            table.append({'task_id': row['task_id'], 'scene': row['scene'], 'seed': row['seed'], 'method': row['method'],
                          'count': row['count'], 'demand': row.get('demand', 'constant_curve_density'),
                          'status': metrics['termination_status'], 'success': metrics['success'],
                          'steps': metrics.get('steps'), 'simulation_time': metrics.get('simulation_time'),
                          'recoveries': metrics.get('replan_count'), 'code_ref': ref, 'historical_original': False,
                          'trajectory_sha256': sha(directory / 'trajectory.npz'), 'result': directory.relative_to(PROJECT).as_posix()})
        with (report / (suite + '_tasks.csv')).open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(table[0])); writer.writeheader(); writer.writerows(table)
        statistics = {'label': 'reconstructed development data, not independent new confirmation', 'suite': suite,
                      'statuses': dict(Counter(r['status'] for r in table)), 'tasks': len(table), 'source_ref': ref}
        if suite == 'demand_confirmation':
            all_units = paired_units(details)
            statistics['paired_planned_demand'] = {d: contrast(all_units, d) for d in ('ones', 'hotspot5')}
            statistics['bootstrap'] = stratified_bootstrap(all_units, requested['statistics'])
        elif suite in ('resource_response', 'resource_confirmation', 'recovery_regression'):
            all_units = units(details)
            settings = yaml.safe_load((PROJECT / 'configs/step1/nonconvex_resource_confirmation_v1.yaml').read_text(encoding='utf-8'))['statistics']
            statistics['resource_inference'] = inference(all_units, settings)
            statistics['motion_subsets'] = motion_subsets(all_units)
        write(report / (suite + '_analysis.json'), statistics)
        overview.append({'suite': suite, 'rows': len(table), 'successes': sum(r['success'] for r in table), 'statuses': statistics['statuses'],
                         'code_ref': ref, 'data': root.relative_to(PROJECT).as_posix()})
    write(report / 'overview.json', overview)
    lines = ['# 本次重建数据结果', '', '全部为2026-10-23数据重建／开发回归，不增加独立确认样本，不改写原报告。', '',
             '| 批次 | 完成行 | 成功行 | 状态 | 冻结源码 |', '|---|---:|---:|---|---|']
    for row in overview:
        states = ', '.join(f'{k}: {v}' for k, v in row['statuses'].items())
        lines.append(f'| {row["suite"]} | {row["rows"]} | {row["successes"]} | {states} | `{row["code_ref"]}` |')
    lines += ['', '相位归因、恢复A/B观测和截止诊断另见对应重建目录；原件缺口、字节一致的重建匹配和完整性核验分别保存。',
              '文件写入中断通过保存工件复判恢复；恢复未重新执行科学任务。']
    (report / 'REPORT.zh.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print({'suites': len(overview), 'rows': sum(r['rows'] for r in overview)})

if __name__ == '__main__': main()
