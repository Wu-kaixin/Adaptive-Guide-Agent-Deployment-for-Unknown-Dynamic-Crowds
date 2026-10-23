"""The Step 1 index and the Step 2a protocol stay reviewable and unimplemented."""
import inspect
import json
from pathlib import Path

import yaml

from udc.interfaces import AllocationDecision, GroupDemand, ResourceAllocator

ROOT = Path(__file__).resolve().parents[1]


def test_delivery_index_paths_match_this_checkout():
    index = json.loads((ROOT / 'docs' / 'step1_delivery_index.json').read_text(encoding='utf-8'))
    runs_present = (ROOT / 'runs').is_dir()
    for entry in index['entries']:
        path = ROOT / entry['path']
        availability = entry['availability']
        if availability == 'present':
            assert path.exists(), entry['id']
        elif availability == 'absent':
            assert not path.exists(), entry['id']
        elif availability == 'local_gitignored':
            if runs_present:
                assert path.exists(), entry['id']
            assert entry['required'] is False
        else:
            raise AssertionError(f"Unknown availability for {entry['id']}")
    for entry in index['unrecoverable']:
        assert entry['required'] is False
        assert entry['availability'] == 'unrecoverable'
        assert entry['reason']
    for relative in index['minimal_data']:
        if relative.startswith('.venv') and not runs_present and not (ROOT / relative).exists():
            continue
        assert (ROOT / relative).exists(), relative


def test_step2a_protocol_is_specified_and_the_step1_seam_is_unchanged():
    protocol = yaml.safe_load((ROOT / 'configs' / 'step2a' / 'static_two_groups.yaml').read_text(encoding='utf-8'))
    assert protocol['implementation_status'] == 'development_execution_wired'
    assert protocol['execution_policy'] == 'fixed_paths_no_recovery_v1'
    assert protocol['execution_wired'] is True
    assert protocol['recovery_policy'] == 'disabled'
    assert protocol['experiments_open'] is False
    assert protocol['run_in_this_round'] is False
    assert protocol['current_interface']['sufficient_for_step2a'] is False
    assert protocol['current_interface']['modify_in_this_round'] is False
    assert protocol['proposed_interface']['implemented'] is True
    assert protocol['proposed_interface']['execution_wired'] is False
    assert protocol['floor_policies']['curve_ceil']['is_allocation_baseline'] is False
    assert protocol['allocation_strategies']['floor_then_size']['implemented'] is True
    assert protocol['allocation_strategies']['frozen_initial_split']['implemented'] is True
    assert 'FROZEN_FLOOR_VIOLATION' in protocol['denominator']['retains']
    assert 'motion' in protocol['scope']['excluded']
    assert 'BUDGET_SHORTFALL' in protocol['denominator']['retains']
    signature = inspect.signature(ResourceAllocator.allocate)
    assert list(signature.parameters) == ['self', 'groups', 'guide_count']
    assert {'group_id', 'pedestrian_ids', 'demand_total'} <= set(GroupDemand.__dataclass_fields__)
    assert set(AllocationDecision.__dataclass_fields__) == {'guides_per_group'}
    runner = (ROOT / 'src' / 'udc' / 'experiments' / 'runner.py').read_text(encoding='utf-8')
    assert 'ResourceAllocator' not in runner
    assert 'static_two_groups' not in runner
