import math

from udc.analysis.sequential_audit import sequential_total


def test_readonly_audit_mirrors_runner_accumulation_without_tolerance():
    values=[0.1]*50
    runner=0.
    for value in values:runner+=value
    assert runner!=math.fsum(values)
    assert sequential_total(iter(values)).hex()==runner.hex()
    assert sequential_total([])==0.
