# Development contract

This repository studies Adaptive Guide-Agent Deployment for Unknown Dynamic Crowds.
The integration baseline implements Step 1 only: one static crowd, known closed
square/rectangle, global observations, no crowd-guide interaction and unlimited
communication. Step 2/3 belong in separate experiment protocols.

Read `docs/ARCHITECTURE.zh.md` first. Core scientific logic is in `src/udc/`;
root Python files and `experiments/validation_runner.py` are thin CLI wrappers.
Do not place algorithms in notebooks or CLI entry points.

Controller inputs are `Observation(positions, radii, demand)`, guide positions,
and known scene geometry. Spawn polygons and evaluator-only truth must not be
used as the crowd boundary. Step 1 sources must remain frozen after initialization.
Demand is an observable numerical planning weight, not a validated risk model.

Expected failures must produce metrics, trajectory and state artifacts. Program
errors remain distinct from scientific failures and stay in accounting. Do not
relax safety or success thresholds to make migration pass. Do not reuse historic
validation percentages as evidence for the current code. Validation seeds
5000–5029 are reserved: do not run them during development.

Setup: `python -m pip install -e ".[dev,analysis]"`.
Checks: `python -m pytest -q`, `python -m pip check`.
Small smoke: `python experiments/validation_runner.py --phase development --smoke
--workers 4 --output runs/smoke`.
Replay: `python experiments/validation_runner.py --reevaluate --output runs/smoke`.
Use process parallelism across episodes and cap numerical threads within workers.

New experiments use `configs/`, `src/udc/`, and `runs/`.
After changes, document measured checks and limits in `docs/VALIDATION.zh.md`.
