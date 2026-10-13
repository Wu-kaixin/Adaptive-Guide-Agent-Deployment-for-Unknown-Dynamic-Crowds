
from pathlib import Path
from heapq import heappush, heappop
import csv

import numpy as np
import matplotlib.pyplot as plt
from scipy.optimize import linear_sum_assignment
from pedpy import load_trajectory_from_jupedsim_sqlite

# =========================================
# 1. Parameters
# =========================================

OUT = Path("results")
DT = 0.1
MAX_SPEED = 1.0
KP = 1.2
TOLERANCE = 0.05
GRID_STEP = 0.5
SEED = 42

# Required center-to-center distances
PED_CLEARANCE = 0.20 + 0.25 + 1.0
GUIDE_CLEARANCE = 2 * 0.25 + 0.1

rng = np.random.default_rng(SEED)

# =========================================
# 2. Load pedestrians and targets
# =========================================

traj = load_trajectory_from_jupedsim_sqlite(
    trajectory_file=OUT / "static_50.sqlite"
)

frame = traj.data["frame"].min()
data = traj.data[traj.data["frame"] == frame]
pedestrians = data[["x", "y"]].to_numpy()

with open(OUT / "guide_targets.csv", newline="") as f:
    rows = sorted(
        csv.DictReader(f),
        key=lambda r: int(r["guide_id"])
    )

targets = np.array([
    [float(r["x"]), float(r["y"])]
    for r in rows
])

N_GUIDES = len(targets)

# =========================================
# 3. Random initial positions
# =========================================

guides = []

for i in range(N_GUIDES):
    for attempt in range(10000):
        p = rng.uniform(2.0, 38.0, size=2)

        if np.min(np.linalg.norm(
            pedestrians - p, axis=1
        )) < PED_CLEARANCE + 0.2:
            continue

        if np.min(np.linalg.norm(
            targets - p, axis=1
        )) < 1.0:
            continue

        if guides and np.min(np.linalg.norm(
            np.asarray(guides) - p, axis=1
        )) < 1.0:
            continue

        guides.append(p)
        break
    else:
        raise RuntimeError("Cannot sample guide position")

guides = np.asarray(guides)
initial = guides.copy()

# Match guides to targets to reduce travel distance
cost = np.linalg.norm(
    guides[:, None, :] - targets[None, :, :],
    axis=2
)

row_idx, col_idx = linear_sum_assignment(cost)
assigned_targets = np.empty_like(targets)
assigned_targets[row_idx] = targets[col_idx]

# =========================================
# 4. Continuous segment safety
# =========================================

def segment_safe(a, b, obstacles, radii):
    a = np.asarray(a)
    b = np.asarray(b)

    # Keep full guide disk inside 40 x 40 area
    if np.any(a < 0.25) or np.any(a > 39.75):
        return False
    if np.any(b < 0.25) or np.any(b > 39.75):
        return False

    v = b - a
    length_sq = np.dot(v, v)

    if length_sq < 1e-12:
        closest = np.repeat(
            a[None, :], len(obstacles), axis=0
        )
    else:
        t = np.clip(
            ((obstacles - a) @ v) / length_sq,
            0.0, 1.0
        )
        closest = a + t[:, None] * v

    d = np.linalg.norm(
        obstacles - closest, axis=1
    )

    return bool(np.all(d >= radii - 1e-6))


# =========================================
# 5. A* path planner
# =========================================

axis = np.arange(0.5, 40.0, GRID_STEP)
gx, gy = np.meshgrid(axis, axis, indexing="ij")
grid = np.stack([gx, gy], axis=-1)
SIZE = len(axis)

MOVES = [
    (dx, dy)
    for dx in (-1, 0, 1)
    for dy in (-1, 0, 1)
    if (dx, dy) != (0, 0)
]


def astar(start, goal, obstacles, radii):
    valid = np.ones((SIZE, SIZE), dtype=bool)

    for p, r in zip(obstacles, radii):
        valid &= (
            np.sum((grid - p) ** 2, axis=2)
            >= (r - 1e-6) ** 2
        )

    START = (-1, -1)
    GOAL = (-2, -2)

    def pos(node):
        if node == START:
            return start
        if node == GOAL:
            return goal
        return grid[node]

    # Connect exact start to nearby grid cells
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

    queue = []
    heappush(queue, (0.0, START))

    g_score = {START: 0.0}
    previous = {}

    while queue:
        _, current = heappop(queue)

        if current == GOAL:
            path = [GOAL]

            while path[-1] != START:
                path.append(previous[path[-1]])

            return [pos(n) for n in path[::-1]]

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
                    pos(current), pos(candidate),
                    obstacles, radii
                ):
                    neighbors.append(candidate)

        if (
            np.linalg.norm(pos(current) - goal) <= 1.25
            or current == START
        ):
            if segment_safe(
                pos(current), goal,
                obstacles, radii
            ):
                neighbors.append(GOAL)

        for neighbor in neighbors:
            tentative = (
                g_score[current]
                + np.linalg.norm(
                    pos(current) - pos(neighbor)
                )
            )

            if tentative < g_score.get(
                neighbor, float("inf")
            ):
                previous[neighbor] = current
                g_score[neighbor] = tentative

                heuristic = np.linalg.norm(
                    pos(neighbor) - goal
                )

                heappush(
                    queue,
                    (tentative + heuristic, neighbor)
                )

    raise RuntimeError("A* could not find a safe path")


# =========================================
# 6. Simplify paths
# =========================================

def simplify_path(path, obstacles, radii):
    simplified = [path[0]]
    i = 0

    while i < len(path) - 1:
        for j in range(len(path) - 1, i, -1):
            if segment_safe(
                path[i], path[j],
                obstacles, radii
            ):
                break

        simplified.append(path[j])
        i = j

    return simplified


# =========================================
# 7. Execute routes safely
# =========================================

history = [guides.copy()]
time = 0.0

for i in range(N_GUIDES):
    other_guides = np.delete(guides, i, axis=0)

    obstacles = np.vstack([
        pedestrians,
        other_guides
    ])

    radii = np.concatenate([
        np.full(len(pedestrians), PED_CLEARANCE),
        np.full(len(other_guides), GUIDE_CLEARANCE)
    ])

    route = astar(
        guides[i],
        assigned_targets[i],
        obstacles,
        radii
    )

    route = simplify_path(route, obstacles, radii)

    for waypoint in route[1:]:
        for step in range(600):
            error = waypoint - guides[i]
            distance = np.linalg.norm(error)

            if distance < 0.02:
                break

            velocity = KP * error
            speed = np.linalg.norm(velocity)

            if speed > MAX_SPEED:
                velocity *= MAX_SPEED / speed

            new_position = guides[i] + DT * velocity

            if not segment_safe(
                guides[i], new_position,
                obstacles, radii
            ):
                raise RuntimeError(
                    f"Safety violation for Guide {i}"
                )

            guides[i] = new_position
            time += DT
            history.append(guides.copy())
        else:
            raise RuntimeError(
                f"Guide {i} did not reach waypoint"
            )

    error = np.linalg.norm(
        guides[i] - assigned_targets[i]
    )

    print(
        f"Guide {i}: "
        f"route waypoints={len(route)}, "
        f"error={error:.3f} m"
    )

history = np.asarray(history)

final_errors = np.linalg.norm(
    guides - assigned_targets, axis=1
)

print(f"Deployment time: {time:.1f} s")
print(f"Maximum error: {final_errors.max():.3f} m")
print(f"Converged: {bool(np.all(final_errors < TOLERANCE))}")

# =========================================
# 8. Save trajectory CSV
# =========================================

with open(
    OUT / "guide_safe_routes.csv",
    "w", newline=""
) as f:
    writer = csv.writer(f)
    writer.writerow(["time", "guide_id", "x", "y"])

    for k, state in enumerate(history):
        for i, (x, y) in enumerate(state):
            writer.writerow([k * DT, i, x, y])

# =========================================
# 9. Plot trajectories
# =========================================

fig, ax = plt.subplots(figsize=(9, 9))

ax.scatter(
    pedestrians[:, 0],
    pedestrians[:, 1],
    s=22, color="steelblue",
    label="Pedestrians"
)

for i in range(N_GUIDES):
    ax.plot(
        history[:, i, 0],
        history[:, i, 1],
        linewidth=1.6
    )

ax.scatter(
    initial[:, 0],
    initial[:, 1],
    marker="x", s=100,
    color="orange",
    label="Random Initial Positions"
)

ax.scatter(
    assigned_targets[:, 0],
    assigned_targets[:, 1],
    marker="*", s=180,
    color="green",
    label="Deployment Targets"
)

ax.scatter(
    guides[:, 0],
    guides[:, 1],
    s=35,
    color="red",
    label="Final Guide Positions"
)

for i, p in enumerate(initial):
    ax.annotate(
        f"G{i}", p, xytext=(5, 5),
        textcoords="offset points"
    )

ax.set_xlim(0, 40)
ax.set_ylim(0, 40)
ax.set_aspect("equal")
ax.set_xlabel("X (m)")
ax.set_ylabel("Y (m)")
ax.set_title("A* Safe Guide Deployment")
ax.grid(alpha=0.3)
ax.legend()

plt.savefig(
    OUT / "safe_deployment.png",
    dpi=200, bbox_inches="tight"
)

plt.show()
