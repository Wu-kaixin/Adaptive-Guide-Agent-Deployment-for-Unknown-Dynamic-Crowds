
from pathlib import Path
from heapq import heappush, heappop
import csv

import numpy as np
import matplotlib.pyplot as plt
from scipy.optimize import linear_sum_assignment
from pedpy import load_trajectory_from_jupedsim_sqlite

# ==========================================
# 1. Parameters
# ==========================================

OUT = Path("results")

DT = 0.1
DURATION = 90.0
MAX_SPEED = 1.0
KP = 1.2
TOL = 0.05
GRID_STEP = 0.5

PED_RADIUS = 0.20
GUIDE_RADIUS = 0.25
PED_SAFE = PED_RADIUS + GUIDE_RADIUS + 1.0
GUIDE_SAFE = 2 * GUIDE_RADIUS + 0.1

# ==========================================
# 2. Load previous experiment
# ==========================================

traj = load_trajectory_from_jupedsim_sqlite(
    trajectory_file=OUT / "static_50.sqlite"
)

frame = traj.data["frame"].min()
pedestrians = traj.data.loc[
    traj.data["frame"] == frame, ["x", "y"]
].to_numpy()

def read_csv(filename):
    with open(OUT / filename, newline="") as f:
        return list(csv.DictReader(f))

target_rows = sorted(
    read_csv("guide_targets.csv"),
    key=lambda r: int(r["guide_id"])
)

targets = np.array([
    [float(r["x"]), float(r["y"])]
    for r in target_rows
])

route_rows = read_csv("guide_safe_routes.csv")
first_time = min(float(r["time"]) for r in route_rows)

start_rows = sorted(
    [
        r for r in route_rows
        if abs(float(r["time"]) - first_time) < 1e-8
    ],
    key=lambda r: int(r["guide_id"])
)

positions = np.array([
    [float(r["x"]), float(r["y"])]
    for r in start_rows
])

N = len(positions)
assert N == len(targets)

initial = positions.copy()

# Same Hungarian assignment as previous experiment
cost = np.linalg.norm(
    positions[:, None, :] - targets[None, :, :],
    axis=2
)

rows, cols = linear_sum_assignment(cost)
goals = np.empty_like(targets)
goals[rows] = targets[cols]

# ==========================================
# 3. Continuous collision checking
# ==========================================

def segment_min_distance(a, b, points):
    if len(points) == 0:
        return float("inf")

    v = b - a
    vv = float(np.dot(v, v))

    if vv < 1e-12:
        return float(
            np.linalg.norm(points - a, axis=1).min()
        )

    t = np.clip(
        ((points - a) @ v) / vv,
        0.0, 1.0
    )

    closest = a + t[:, None] * v

    return float(
        np.linalg.norm(points - closest, axis=1).min()
    )


def segment_safe(a, b, obstacles, radii):
    # Wall clearance for the whole guide disk
    for p in (a, b):
        if np.any(p < GUIDE_RADIUS):
            return False
        if np.any(p > 40.0 - GUIDE_RADIUS):
            return False

    if len(obstacles) == 0:
        return True

    v = b - a
    vv = float(np.dot(v, v))

    if vv < 1e-12:
        closest = np.broadcast_to(
            a, obstacles.shape
        )
    else:
        t = np.clip(
            ((obstacles - a) @ v) / vv,
            0.0, 1.0
        )
        closest = a + t[:, None] * v

    distances = np.linalg.norm(
        obstacles - closest, axis=1
    )

    return bool(
        np.all(distances >= radii - 1e-7)
    )


def moving_distance(a, b, c, d):
    """Minimum distance between two moving guides."""
    r = a - c
    v = (b - a) - (d - c)
    vv = float(np.dot(v, v))

    if vv < 1e-12:
        t = 0.0
    else:
        t = np.clip(
            -np.dot(r, v) / vv, 0.0, 1.0
        )

    return float(np.linalg.norm(r + t * v))


# ==========================================
# 4. Grid-based A* planner
# ==========================================

axis = np.arange(0.5, 39.51, GRID_STEP)
xx, yy = np.meshgrid(axis, axis, indexing="ij")
grid = np.stack([xx, yy], axis=-1)
SIZE = len(axis)

MOVES = [
    (dx, dy)
    for dx in (-1, 0, 1)
    for dy in (-1, 0, 1)
    if dx != 0 or dy != 0
]


def plan_path(start, goal, extra=None):
    if extra is None:
        extra = np.empty((0, 2))

    obstacles = np.vstack([pedestrians, extra])
    radii = np.concatenate([
        np.full(len(pedestrians), PED_SAFE),
        np.full(len(extra), GUIDE_SAFE)
    ])

    if segment_safe(start, goal, obstacles, radii):
        return [start.copy(), goal.copy()]

    valid = np.ones((SIZE, SIZE), dtype=bool)

    for p, radius in zip(obstacles, radii):
        valid &= (
            np.sum((grid - p) ** 2, axis=2)
            >= radius ** 2
        )

    START = (-1, -1)
    END = (-2, -2)

    def location(node):
        if node == START:
            return start
        if node == END:
            return goal
        return grid[node]

    distances = np.linalg.norm(
        grid - start, axis=2
    )

    start_links = [
        tuple(idx)
        for idx in np.argwhere(
            valid & (distances <= 1.25)
        )
        if segment_safe(
            start, grid[tuple(idx)],
            obstacles, radii
        )
    ]

    queue = [(0.0, START)]
    gscore = {START: 0.0}
    previous = {}

    while queue:
        _, current = heappop(queue)

        if current == END:
            nodes = [END]
            while nodes[-1] != START:
                nodes.append(previous[nodes[-1]])

            path = [
                np.array(location(node), dtype=float)
                for node in nodes[::-1]
            ]

            # Line-of-sight path simplification
            simplified = [path[0]]
            i = 0

            while i < len(path) - 1:
                j = len(path) - 1
                while j > i + 1 and not segment_safe(
                    path[i], path[j], obstacles, radii
                ):
                    j -= 1

                simplified.append(path[j])
                i = j

            return simplified

        neighbors = []

        if current == START:
            neighbors.extend(start_links)
        else:
            x, y = current

            for dx, dy in MOVES:
                nx, ny = x + dx, y + dy

                if not (0 <= nx < SIZE and 0 <= ny < SIZE):
                    continue

                if not valid[nx, ny]:
                    continue

                candidate = (nx, ny)

                if segment_safe(
                    location(current),
                    location(candidate),
                    obstacles, radii
                ):
                    neighbors.append(candidate)

            if np.linalg.norm(
                location(current) - goal
            ) <= 1.25:
                if segment_safe(
                    location(current), goal,
                    obstacles, radii
                ):
                    neighbors.append(END)

        for neighbor in neighbors:
            new_cost = (
                gscore[current]
                + np.linalg.norm(
                    location(current) - location(neighbor)
                )
            )

            if new_cost < gscore.get(
                neighbor, float("inf")
            ):
                gscore[neighbor] = new_cost
                previous[neighbor] = current

                heuristic = np.linalg.norm(
                    location(neighbor) - goal
                )

                heappush(
                    queue,
                    (new_cost + heuristic, neighbor)
                )

    raise RuntimeError("No safe A* route found")


# ==========================================
# 5. Initialize all routes
# ==========================================

paths = [
    plan_path(positions[i], goals[i])
    for i in range(N)
]

waypoint_index = np.ones(N, dtype=int)
blocked_steps = np.zeros(N, dtype=int)

history = [positions.copy()]
replan_count = 0

min_ped_clearance = float("inf")
min_guide_clearance = float("inf")
converged = False

# ==========================================
# 6. Simultaneous deployment
# ==========================================

for step in range(int(DURATION / DT)):

    # All guides share the same simulation time.
    next_positions = positions.copy()

    # Rotate priorities to reduce persistent unfairness.
    shift = (step // 10) % N
    order = np.roll(np.arange(N), -shift)

    processed = set()

    for i in order:

        # Skip finished guides
        if np.linalg.norm(
            goals[i] - positions[i]
        ) < TOL:
            processed.add(i)
            continue

        # Update waypoint
        while (
            waypoint_index[i] < len(paths[i]) - 1
            and np.linalg.norm(
                paths[i][waypoint_index[i]] - positions[i]
            ) < 0.08
        ):
            waypoint_index[i] += 1

        waypoint = paths[i][waypoint_index[i]]

        error = waypoint - positions[i]
        velocity = KP * error

        speed = np.linalg.norm(velocity)

        if speed > MAX_SPEED:
            velocity *= MAX_SPEED / speed

        movement = DT * velocity

        # Try progressively shorter safe movements.
        accepted = False

        for scale in (1.0, 0.5, 0.25, 0.125, 0.0):

            candidate = positions[i] + scale * movement

            if not segment_safe(
                positions[i], candidate,
                pedestrians,
                np.full(len(pedestrians), PED_SAFE)
            ):
                continue

            collision = False

            for j in range(N):
                if i == j:
                    continue

                # Processed guides use planned movement.
                # Others are temporarily treated as stationary.
                other_next = (
                    next_positions[j]
                    if j in processed
                    else positions[j]
                )

                distance = moving_distance(
                    positions[i], candidate,
                    positions[j], other_next
                )

                if distance < GUIDE_SAFE - 1e-7:
                    collision = True
                    break

            if not collision:
                next_positions[i] = candidate
                accepted = True
                break

        processed.add(i)

        # Detect and resolve blocked paths
        moved = np.linalg.norm(
            next_positions[i] - positions[i]
        )

        if (
            moved < 0.002
            and np.linalg.norm(goals[i] - positions[i]) > TOL
        ):
            blocked_steps[i] += 1
        else:
            blocked_steps[i] = 0

        if blocked_steps[i] >= 20:
            other = np.delete(positions, i, axis=0)

            try:
                paths[i] = plan_path(
                    positions[i], goals[i], extra=other
                )
            except RuntimeError:
                paths[i] = plan_path(
                    positions[i], goals[i]
                )

            waypoint_index[i] = 1
            blocked_steps[i] = 0
            replan_count += 1

    # Verify the complete synchronized movement
    for i in range(N):
        ped_distance = segment_min_distance(
            positions[i], next_positions[i],
            pedestrians
        )
        min_ped_clearance = min(
            min_ped_clearance,
            ped_distance - PED_RADIUS - GUIDE_RADIUS
        )

        for j in range(i + 1, N):
            distance = moving_distance(
                positions[i], next_positions[i],
                positions[j], next_positions[j]
            )
            min_guide_clearance = min(
                min_guide_clearance,
                distance - 2 * GUIDE_RADIUS
            )

    if (
        min_ped_clearance < 1.0 - 1e-6
        or min_guide_clearance < 0.1 - 1e-6
    ):
        raise RuntimeError("Safety invariant violated")

    # Apply all movements simultaneously
    positions = next_positions
    history.append(positions.copy())

    errors = np.linalg.norm(
        positions - goals, axis=1
    )

    if np.all(errors < TOL):
        converged = True
        break

history = np.asarray(history)
elapsed = (len(history) - 1) * DT

# ==========================================
# 7. Evaluation
# ==========================================

final_errors = np.linalg.norm(
    positions - goals, axis=1
)

sequential_duration = max(
    float(r["time"]) for r in route_rows
) - first_time

print("\n===== Step 1.6 Results =====")
print(f"Converged: {converged}")
print(f"Simultaneous time: {elapsed:.2f} s")
print(f"Previous sequential time: {sequential_duration:.2f} s")
print(f"Maximum target error: {final_errors.max():.4f} m")
print(f"Minimum pedestrian clearance: {min_ped_clearance:.4f} m")
print(f"Minimum guide clearance: {min_guide_clearance:.4f} m")
print(f"Replanning events: {replan_count}")

# ==========================================
# 8. Save trajectories
# ==========================================

with open(
    OUT / "simultaneous_trajectories.csv",
    "w", newline=""
) as f:
    writer = csv.writer(f)
    writer.writerow(["time", "guide_id", "x", "y"])

    for k, state in enumerate(history):
        for i, (x, y) in enumerate(state):
            writer.writerow([k * DT, i, x, y])

# ==========================================
# 9. Visualization
# ==========================================

fig, ax = plt.subplots(figsize=(9, 9))

ax.scatter(
    pedestrians[:, 0],
    pedestrians[:, 1],
    s=25,
    color="steelblue",
    label="Static Pedestrians"
)

for i in range(N):
    ax.plot(
        history[:, i, 0],
        history[:, i, 1],
        linewidth=1.5
    )

ax.scatter(
    initial[:, 0],
    initial[:, 1],
    marker="x",
    s=100,
    color="orange",
    label="Initial Positions"
)

ax.scatter(
    goals[:, 0],
    goals[:, 1],
    marker="*",
    s=160,
    color="green",
    label="Guide Targets"
)

ax.scatter(
    positions[:, 0],
    positions[:, 1],
    s=50,
    color="red",
    label="Final Positions"
)

for i, p in enumerate(positions):
    ax.annotate(
        f"G{i}",
        p,
        xytext=(5, 5),
        textcoords="offset points"
    )

ax.set_xlim(0, 40)
ax.set_ylim(0, 40)
ax.set_aspect("equal")
ax.set_xlabel("X (m)")
ax.set_ylabel("Y (m)")
ax.set_title("Step 1.6 - Simultaneous Guide Deployment")
ax.legend()
ax.grid(alpha=0.3)

plt.savefig(
    OUT / "simultaneous_deployment.png",
    dpi=200,
    bbox_inches="tight"
)

plt.show()
