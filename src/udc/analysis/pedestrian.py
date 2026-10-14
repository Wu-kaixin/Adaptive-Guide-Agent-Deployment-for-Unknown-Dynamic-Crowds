"""PedPy analysis of an immutable Step 1 snapshot or a JuPedSim trajectory."""
import argparse
import json
from pathlib import Path

import numpy as np


def analyze(output, result=None, sqlite_file=None, cutoff_radius=1.0):
    if (result is None) == (sqlite_file is None):
        raise ValueError('Provide exactly one result directory or SQLite file')
    if not np.isfinite(cutoff_radius) or cutoff_radius <= 0:
        raise ValueError('Cutoff radius must be positive')
    try:
        import pandas as pd
        import pedpy
    except ImportError as error:
        raise ImportError('Install analysis dependencies: pip install -e ".[analysis]"') from error
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('Use an empty analysis output directory')
    output.mkdir(parents=True, exist_ok=True)
    if result is not None:
        with np.load(Path(result)/'trajectory.npz', allow_pickle=False) as data:
            positions = data['crowd_positions'].copy()
            room = data['room'].copy()
            time = data['time'].copy()
        if not len(positions) or room.shape != (2,):
            raise ValueError('Saved result contains no valid crowd snapshot')
        dt = float(time[1]-time[0]) if len(time) > 1 else 1.0
        trajectory = pedpy.TrajectoryData(pd.DataFrame({
            'id': np.arange(len(positions)), 'frame': np.zeros(len(positions), int),
            'x': positions[:, 0], 'y': positions[:, 1],
        }), frame_rate=1.0/dt)
        area = pedpy.WalkableArea([(0, 0), (room[0], 0), tuple(room), (0, room[1])])
        source_kind = 'frozen_snapshot'
    else:
        trajectory = pedpy.load_trajectory_from_jupedsim_sqlite(trajectory_file=Path(sqlite_file))
        area = pedpy.load_walkable_area_from_jupedsim_sqlite(trajectory_file=Path(sqlite_file))
        positions = trajectory.data[['x', 'y']].to_numpy()
        source_kind = 'recorded_trajectory'
    # The measurement region and Voronoi cutoff are recorded explicitly.
    lower, upper = positions.min(axis=0)-1.0, positions.max(axis=0)+1.0
    bounds = area.polygon.bounds
    lower = np.maximum(lower, bounds[:2]); upper = np.minimum(upper, bounds[2:])
    measurement = pedpy.MeasurementArea([
        tuple(lower), (upper[0], lower[1]), tuple(upper), (lower[0], upper[1]),
    ])
    voronoi = pedpy.compute_individual_voronoi_polygons(
        traj_data=trajectory, walkable_area=area,
        cut_off=pedpy.Cutoff(radius=cutoff_radius, quad_segments=8),
    )
    density, _ = pedpy.compute_voronoi_density(
        individual_voronoi_data=voronoi, measurement_area=measurement,
    )
    classic = pedpy.compute_classic_density(traj_data=trajectory, measurement_area=measurement)
    neighbors = pedpy.compute_neighbors(voronoi, as_list=False)
    density.to_csv(output/'voronoi_density.csv', index=False)
    classic.to_csv(output/'classic_density.csv', index=False)
    neighbors.to_csv(output/'neighbors.csv', index=False)
    trajectory.data.drop(columns=['point'], errors='ignore').to_csv(output/'crowd_trajectory.csv', index=False)
    summary = {
        'source_kind': source_kind, 'pedpy_version': pedpy.__version__,
        'pedestrians': int(trajectory.data['id'].nunique()),
        'frames': int(trajectory.data['frame'].nunique()),
        'frame_rate': float(trajectory.frame_rate),
        'measurement_bounds': [*lower.tolist(), *upper.tolist()],
        'voronoi_cutoff_radius': cutoff_radius,
        'mean_voronoi_density': float(density['density'].mean()),
        'mean_classic_density': float(classic['density'].mean()),
    }
    (output/'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False)+'\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--result'); source.add_argument('--sqlite')
    parser.add_argument('--output', required=True)
    parser.add_argument('--cutoff-radius', type=float, default=1.0)
    args = parser.parse_args()
    print(json.dumps(analyze(args.output, args.result, args.sqlite, args.cutoff_radius), indent=2))


if __name__ == '__main__':
    main()
