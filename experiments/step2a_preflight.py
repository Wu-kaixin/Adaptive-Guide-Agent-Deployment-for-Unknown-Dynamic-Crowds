"""Thin entry point for the Step 2a planning-only preflight."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from udc.preflight import reevaluate_preflight, run_preflight, separated_demo_input


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--demo', action='store_true', help='Run the handwritten two-group fixture')
    parser.add_argument('--reevaluate', action='store_true')
    args = parser.parse_args(argv)
    output = Path(args.output)
    if args.reevaluate:
        print(json.dumps(reevaluate_preflight(output), indent=2))
        return
    if not args.demo:
        parser.error('pass --demo or --reevaluate')
    record = run_preflight(separated_demo_input(), output)
    print(json.dumps({key: record.get(key) for key in (
        'schema', 'status', 'preflight_accepted', 'allocation_feasible', 'deployment_success',
        'motion_executed', 'failure_stage', 'failure_code',
    )}, indent=2))


if __name__ == '__main__':
    main()
