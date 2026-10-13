
from pathlib import Path
import csv

import numpy as np
import matplotlib.pyplot as plt
from shapely.geometry import MultiPoint, Point, Polygon
from pedpy import load_trajectory_from_jupedsim_sqlite

# =====================================
# 1. Configuration
# =====================================

OUTPUT = Path("results")
DB_FILE = OUTPUT / "static_50.sqlite"

N_GUIDES = 8

PEDESTRIAN_RADIUS = 0.20
GUIDE_RADIUS = 0.25
SAFETY_CLEARANCE = 1.0

# Distance from pedestrian center boundary
# to target guide center
OFFSET = (
    PEDESTRIAN_RADIUS
    + GUIDE_RADIUS
    + SAFETY_CLEARANCE
)

# Same 40 x 40 m environment as Step 1
walkable_area = Polygon([
    (0, 0),
    (40, 0),
    (40, 40),
    (0, 40),
])

# =====================================
# 2. Load pedestrian positions
# =====================================

traj = load_trajectory_from_jupedsim_sqlite(
    trajectory_file=DB_FILE
)

first_frame = traj.data["frame"].min()

data = traj.data[
    traj.data["frame"] == first_frame
]

positions = np.column_stack([
    data["x"].to_numpy(),
    data["y"].to_numpy(),
])

print(f"Pedestrians: {len(positions)}")

# =====================================
# 3. Estimate crowd boundary
# =====================================

crowd_hull = MultiPoint(
    positions
).convex_hull

if not isinstance(crowd_hull, Polygon):
    raise ValueError(
        "Crowd must contain non-collinear points."
    )

# =====================================
# 4. Generate deployment curve
# =====================================

deployment_region = crowd_hull.buffer(OFFSET)

deployment_curve = deployment_region.exterior

print(f"Crowd perimeter: {crowd_hull.length:.2f} m")
print(f"Deployment perimeter: {deployment_curve.length:.2f} m")

# =====================================
# 5. Generate 8 guide target positions
# =====================================

guide_targets = []

hull_boundary = crowd_hull.exterior

for i in range(N_GUIDES):

    distance = (
        i / N_GUIDES
    ) * deployment_curve.length

    point = deployment_curve.interpolate(distance)

    # buffer() replaces circular corners with straight chords, so a
    # sample can sit slightly inside the true offset curve.
    nearest = hull_boundary.interpolate(
        hull_boundary.project(point)
    )

    direction = np.array([
        point.x - nearest.x,
        point.y - nearest.y,
    ])
    direction_length = np.linalg.norm(direction)

    if direction_length < 1e-12:
        raise ValueError(
            f"Guide {i} sample lies on the crowd boundary."
        )

    corrected = (
        np.array([nearest.x, nearest.y])
        + direction / direction_length * OFFSET
    )

    guide_targets.append(corrected)

guide_targets = np.array(guide_targets)

# =====================================
# 6. Check target validity
# =====================================

for i, target in enumerate(guide_targets):

    guide_disk = Point(target).buffer(GUIDE_RADIUS)

    if not walkable_area.covers(guide_disk):
        raise ValueError(
            f"Guide {i} target is outside the environment."
        )

    minimum_distance = np.min(
        np.linalg.norm(
            positions - target,
            axis=1
        )
    )

    actual_clearance = (
        minimum_distance
        - PEDESTRIAN_RADIUS
        - GUIDE_RADIUS
    )

    if actual_clearance < SAFETY_CLEARANCE - 1e-6:
        raise ValueError(
            f"Guide {i} violates safety clearance."
        )

# =====================================
# 7. Save guide targets
# =====================================

with open(
    OUTPUT / "guide_targets.csv",
    "w",
    newline="",
) as f:

    writer = csv.writer(f)
    writer.writerow(["guide_id", "x", "y"])

    for i, p in enumerate(guide_targets):
        writer.writerow([i, p[0], p[1]])

# =====================================
# 8. Visualization
# =====================================

fig, ax = plt.subplots(figsize=(8, 8))

# Pedestrians
ax.scatter(
    positions[:, 0],
    positions[:, 1],
    s=35,
    label="Pedestrians",
)

# Crowd convex hull
hx, hy = crowd_hull.exterior.xy

ax.plot(
    hx, hy,
    linewidth=2,
    label="Crowd Boundary",
)

# Deployment curve
dx, dy = deployment_curve.xy

ax.plot(
    dx, dy,
    "--",
    linewidth=2,
    label="Deployment Curve",
)

# Guide target locations
ax.scatter(
    guide_targets[:, 0],
    guide_targets[:, 1],
    marker="o",
    s=120,
    edgecolors="black",
    label="Guide Targets",
)

# Guide labels
for i, (x, y) in enumerate(guide_targets):
    ax.annotate(
        f"G{i}",
        (x, y),
        xytext=(5, 5),
        textcoords="offset points",
    )

ax.set_xlim(12, 28)
ax.set_ylim(14, 26)
ax.set_aspect("equal")

ax.set_xlabel("X (m)")
ax.set_ylabel("Y (m)")
ax.set_title("Crowd Boundary and Guide Deployment")

ax.legend()
ax.grid(alpha=0.3)

plt.savefig(
    OUTPUT / "step1_boundary_and_targets.png",
    dpi=200,
    bbox_inches="tight",
)

plt.show()

print("Boundary and guide targets generated.")
