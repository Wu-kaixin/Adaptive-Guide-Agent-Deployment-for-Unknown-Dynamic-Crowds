"""New-version fixed-action diagnostic, preserving the original unsafe result."""
import json
from pathlib import Path
import shutil

from udc.experiments.static_multigroup import (ROOT, digest, write_json, scientific_sources,
    _load_inputs, _save_task)
from udc.multigroup import execution
from udc.multigroup.accelerated_execution import install_lazy
from udc.multigroup.geometry import construct_groups
from udc.multigroup.evaluation import evaluate


def run():
    import numpy as np
    study = ROOT / 'runs/static_multigroup_20261026/confirmation_v2_frozen'
    output = ROOT / 'runs/step1_safety_gate_20261028/fixed_action_witness_v1'
    output.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((study / 'manifest.json').read_text(encoding='utf-8'))
    name = 'K5_irregular_s2004_e0.12_insufficient_projected_mass_perimeter'
    task = next(t for t in manifest['tasks'] if t['task_id'] == name)
    folder = study / 'results' / name
    receipt = json.loads((folder / 'receipt.json').read_text(encoding='utf-8'))
    if any(digest(folder / n) != sha for n, sha in receipt.items()):
        raise ValueError('Original task changed')
    sources = scientific_sources()
    for relative in sources:
        target = output / 'source_snapshot' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    truth, guides, observation = _load_inputs(study, task)
    with np.load(folder / 'trajectory.npz', allow_pickle=False) as data:
        targets, ids = data['targets'].copy(), data['target_groups'].copy()
    config = dict(manifest['spec']['model'], independent_segment_gate=True)
    write_json(output / 'protocol.json', {'mode': 'fixed-action new-version development diagnostic',
        'original_task_id': name, 'original_receipt_sha256': digest(folder / 'receipt.json'),
        'sources': sources, 'no_new_independent_seed': True, 'thresholds_unchanged': True})
    groups, domain = construct_groups(observation, manifest['spec']['room_m'], config)
    install_lazy(execution)
    result = execution.execute(guides[:task['budget']], targets, domain, manifest['spec']['room_m'], config)
    metrics = evaluate(result, targets, ids, groups, observation, truth, manifest['spec']['room_m'], config)
    metrics.update(task_id='guarded_' + name, status=result.status)
    _save_task(output / 'result', {'task_id': metrics['task_id']}, config, result, targets, ids,
               {'targets_fixed_from_old_receipt': True}, metrics)
    if scientific_sources() != sources:
        raise ValueError('Source changed during diagnostic')
    write_json(output / 'summary.json', {'status': result.status, 'evidence': result.evidence,
        'motion': metrics['motion'], 'old_unsafe_record_preserved': True,
        'independent_samples_added': 0, 'limit': 'Rejects unsafe path; does not construct an alternative route'})
    return metrics


if __name__ == '__main__':
    print(json.dumps(run(), indent=2))
