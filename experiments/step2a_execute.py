"""Thin entry point for fixed-path Step 2a execution."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from udc.execution import execute_preflight, reevaluate_execution


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight')
    parser.add_argument('--output')
    parser.add_argument('--reevaluate')
    args = parser.parse_args(argv)
    if args.reevaluate:
        print(json.dumps(reevaluate_execution(args.reevaluate), indent=2))
        return
    if not args.preflight or not args.output:
        parser.error('--preflight and --output are required')
    record = execute_preflight(args.preflight, args.output)
    print(json.dumps({key: record.get(key) for key in (
        'schema', 'policy', 'status', 'allocation_feasible', 'preflight_accepted',
        'deployment_success', 'orchestration_status', 'motion_executed',
        'executed_path_length_m', 'steps', 'failure_reason', 'recovery_called',
    )}, indent=2))


if __name__ == '__main__':
    main()
