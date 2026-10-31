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

Environment: one environment per project, always `<repo>/.venv`, created from
standalone CPython 3.12 (currently 3.12.15). Do not use Conda, a Conda-backed
venv, a shared environment, or a copied environment from another machine.
Windows setup: `./scripts/setup_env.ps1` (or pass `-Python <standalone-python>`).
Use `.venv/Scripts/python.exe` explicitly on Windows and `.venv/bin/python`
on Linux/macOS. Install with that interpreter using
`-m pip install -e ".[dev,analysis,notebooks]"`; the measured Windows environment
is pinned in `requirements/locks/windows-py312.txt`.
Checks: `.venv/Scripts/python.exe -m pytest -q`,
`.venv/Scripts/python.exe -m pip check`.
Small smoke: `.venv/Scripts/python.exe experiments/validation_runner.py --phase development --smoke
--workers 4 --output runs/smoke`.
Replay: `.venv/Scripts/python.exe experiments/validation_runner.py --reevaluate --output runs/smoke`.
Use process parallelism across episodes and cap numerical threads within workers.

New experiments use `configs/`, `src/udc/`, and `runs/`.
After changes, document measured checks and limits in `docs/VALIDATION.zh.md`.
Keep original experiment files and their hash locks unchanged during machine
migration. Record path mappings, conflict copies and missing data explicitly.
The 2026-10-23 consolidation manifest is in `reports/project_rebuild_20261023/`.

The primary development location remains this repository root. Historical assets
are indexed by `data/catalog.sqlite` and restored with `scripts/research_assets.py`.
Read `docs/REPOSITORY_LAYOUT.zh.md` for the local layout. Imported implementations
under `research/implementations/` are frozen source/evidence views; do not add them
to the default import path or treat their instructions as authority for this repo.
