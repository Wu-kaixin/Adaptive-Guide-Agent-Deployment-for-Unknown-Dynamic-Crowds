
from pathlib import Path
import csv

import numpy as np
import matplotlib.pyplot as plt
from pedpy import load_trajectory_from_jupedsim_sqlite

# ==================================
# 1. Parameters
# ==================================

OUTPUT = Path("results")

DT = 0.1
DURATION = 60.0
MAX_SPEED = 1.0
KP = 1.2
TOLERANCE = 0.05

PED_RADIUS = 0.20
GUIDE_RADIUS = 0.25
SAFETY_CLEARANCE = 1.0

MIN_PED_DISTANCE = (
    PED_RADIUS + GUIDE_RADIUS + SAFETY_CLEARANCE
)
MIN_GUIDE_DISTANCE = 2 * GUIDE_RADIUS + 0.1

rng = np.random.default_rng(42)

# ==================================
# 2. Load pedestrian positions
# ==================================

traj = load_trajectory_from_jupedsim_sqlite(
    trajectory_file=OUTPUT / "static_50.sqlite"
)

data = traj.data[
    traj.data["frame"] == traj.data["frame"].min()
]

pedestrians = data[["x", "y"]].to_numpy()
center = pedestrians.mean(axis=0)

# ==================================
# 3. Load guide targets
# ==================================

with open(OUTPUT / "guide_targets.csv", newline="") as f:
    target_rows = sorted(
        csv.DictReader(f),
        key=lambda row: int(row["guide_id"]),
    )

targets = np.array([
    [float(row["x"]), float(row["y"])]
    for row in target_rows
])

N = len(targets)

# ==================================
# 4. Initialize guide positions
# ==================================
# Random outward distances from assigned targets.
# This creates simple, feasible initial routes.

directions = targets - center
directions /= np.linalg.norm(
    directions, axis=1, keepdims=True
)

distances = rng.uniform(2.0, 4.0, size=(N, 1))

positions = targets + directions * distances
initial_positions = positions.copy()

print("Guide initial positions:")
print(positions)

# ==================================
# 5. Safety checks
# ==================================

def segment_distance_to_points(a, b, points):
    """Minimum distance from a movement segment to points."""
    direction = b - a
    denom = np.dot(direction, direction)

    if denom < 1e-12:
        return np.min(np.linalg.norm(points - a, axis=1))

    t = np.clip(
        ((points - a) @ direction) / denom,
        0.0,
        1.0,
    )

    closest = a + t[:, None] * direction

    return np.min(np.linalg.norm(points - closest, axis=1))


def pair_path_distance(a, b, c, d):
    """Minimum simultaneous separation of two agents."""
    relative_start = a - c
    relative_motion = (b - a) - (d - c)

    denom = np.dot(relative_motion, relative_motion)

    if denom < 1e-12:
        t = 0.0
    else:
        t = np.clip(
            -np.dot(relative_start, relative_motion) / denom,
            0.0,
            1.0,
        )

    return np.linalg.norm(
        relative_start + t * relative_motion
    )


# ==================================
# 6. Closed-loop control
# ==================================

history = [positions.copy()]
converged = False

steps = int(DURATION / DT)

for step in range(steps):

    errors = targets - positions
    velocities = KP * errors

    speeds = np.linalg.norm(
        velocities, axis=1, keepdims=True
    )

    velocities *= np.minimum(
        1.0,
        MAX_SPEED / np.maximum(speeds, 1e-12),
    )

    proposed = positions + DT * velocities

    # Environment safety: 40 x 40 m
    if np.any(proposed < GUIDE_RADIUS) or np.any(
        proposed > 40.0 - GUIDE_RADIUS
    ):
        raise RuntimeError("Guide would leave the environment.")

    # Check continuous segment clearance from pedestrians
    for i in range(N):
        distance = segment_distance_to_points(
            positions[i], proposed[i], pedestrians
        )

        if distance < MIN_PED_DISTANCE - 1e-6:
            raise RuntimeError(
                f"Guide {i}: pedestrian safety violation"
            )

    # Check guide-guide separation during each timestep
    for i in range(N):
        for j in range(i + 1, N):
            distance = pair_path_distance(
                positions[i], proposed[i],
                positions[j], proposed[j],
            )

            if distance < MIN_GUIDE_DISTANCE - 1e-6:
                raise RuntimeError(
                    f"Collision risk: Guide {i} and {j}"
                )

    positions = proposed
    history.append(positions.copy())

    max_error = np.max(
        np.linalg.norm(targets - positions, axis=1)
    )

    if max_error < TOLERANCE:
        converged = True
        break

history = np.array(history)

elapsed = (len(history) - 1) * DT

print(f"Converged: {converged}")
print(f"Elapsed time: {elapsed:.2f} s")
print(f"Maximum target error: {max_error:.4f} m")

# ==================================
# 7. Save trajectories
# ==================================

with open(
    OUTPUT / "guide_trajectories.csv",
    "w",
    newline="",
) as f:

    writer = csv.writer(f)
    writer.writerow(["time", "guide_id", "x", "y"])

    for k, frame in enumerate(history):
        for i, (x, y) in enumerate(frame):
            writer.writerow([k * DT, i, x, y])

# ==================================
# 8. Visualization
# ==================================

fig, ax = plt.subplots(figsize=(8, 8))

ax.scatter(
    pedestrians[:, 0],
    pedestrians[:, 1],
    s=25,
    color="steelblue",
    label="Pedestrians",
)

for i in range(N):
    ax.plot(
        history[:, i, 0],
        history[:, i, 1],
        linewidth=1.5,
    )

ax.scatter(
    initial_positions[:, 0],
    initial_positions[:, 1],
    marker="x",
    s=90,
    color="orange",
    label="Initial Positions",
)

ax.scatter(
    targets[:, 0],
    targets[:, 1],
    marker="*",
    s=160,
    color="green",
    label="Targets",
)

ax.scatter(
    positions[:, 0],
    positions[:, 1],
    marker="o",
    s=55,
    color="red",
    label="Final Positions",
)

for i, (x, y) in enumerate(positions):
    ax.annotate(f"G{i}", (x, y), xytext=(5, 5),
                textcoords="offset points")

ax.set_xlim(8, 32)
ax.set_ylim(8, 32)
ax.set_aspect("equal")
ax.set_xlabel("X (m)")
ax.set_ylabel("Y (m)")
ax.set_title("Step 1: Guide Deployment Control")
ax.legend()
ax.grid(alpha=0.3)

plt.savefig(
    OUTPUT / "guide_deployment.png",
    dpi=200,
    bbox_inches="tight",
)

plt.show()
