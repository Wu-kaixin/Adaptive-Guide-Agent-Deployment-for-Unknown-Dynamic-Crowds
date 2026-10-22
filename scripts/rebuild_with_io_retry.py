"""Retry transient Windows atomic-renaming failures, never scientific execution."""
from pathlib import Path
import argparse
import json
import os
import time

import rebuild_missing_data as driver

BASE_INITIALIZER = driver.initialize
ORIGINAL_REPLACE = os.replace

def initialize_with_io_retry(ref):
    BASE_INITIALIZER(ref)
    def replace(source, destination, *args, **kwargs):
        in_output = Path(destination).resolve().is_relative_to(driver.OUTPUT.resolve())
        for attempt in range(10):
            try:
                return ORIGINAL_REPLACE(source, destination, *args, **kwargs)
            except PermissionError as error:
                if not in_output or attempt == 9: raise
                log = driver.OUTPUT / 'io_retry_logs' / f'process_{os.getpid()}.jsonl'
                log.parent.mkdir(parents=True, exist_ok=True)
                with log.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps({'operation': 'os.replace', 'source': str(source), 'destination': str(destination),
                                             'attempt': attempt + 1, 'error': repr(error), 'scientific_execution_retried': False}) + '\n')
                time.sleep(min(.05 * (attempt + 1), .5))
    os.replace = replace

def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--suite', choices=list(driver.SUITES), required=True)
    args = parser.parse_args(); ref = driver.SUITES[args.suite][2]
    driver.snapshot(ref)
    driver.write(driver.OUTPUT / ('runtime_io_adapter_' + args.suite + '.json'), {
        'adapter': str(Path(__file__).resolve()), 'adapter_sha256': driver.sha(Path(__file__)),
        'scope': 'retry same atomic rename on PermissionError inside reconstruction output only', 'maximum_attempts': 10,
        'scientific_execution_retry': False, 'scientific_sources_or_thresholds_modified': False,
        'original_driver_sha256': driver.sha(Path(driver.__file__))})
    driver.initialize = initialize_with_io_retry
    initialize_with_io_retry(ref)
    driver.run_suite(args.suite, 2)

if __name__ == '__main__': main()
