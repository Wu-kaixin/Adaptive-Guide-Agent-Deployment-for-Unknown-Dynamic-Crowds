
from pathlib import Path

import matplotlib.pyplot as plt
from pedpy import (
    load_trajectory_from_jupedsim_sqlite,
    load_walkable_area_from_jupedsim_sqlite,
    plot_walkable_area,
    is_trajectory_valid,
)

# 1. Load JuPedSim output
file = Path("results/static_50.sqlite")

traj = load_trajectory_from_jupedsim_sqlite(
    trajectory_file=file
)

area = load_walkable_area_from_jupedsim_sqlite(
    trajectory_file=file
)

# 2. Check data
print("Pedestrians:", traj.data["id"].nunique())
print("Frame rate:", traj.frame_rate)
print("Trajectory valid:", is_trajectory_valid(
    traj_data=traj,
    walkable_area=area,
))

# 3. Select first frame
first_frame = traj.data["frame"].min()

data = traj.data[
    traj.data["frame"] == first_frame
]

# 4. Plot crowd positions
fig, ax = plt.subplots(figsize=(7, 7))

plot_walkable_area(
    walkable_area=area,
    axes=ax,
)

ax.scatter(
    data["x"],
    data["y"],
    s=40,
    color="blue",
    label="Pedestrians",
)

ax.set_xlim(0, 40)
ax.set_ylim(0, 40)
ax.set_aspect("equal")
ax.set_title("Static Crowd - 50 Pedestrians")
ax.set_xlabel("X (m)")
ax.set_ylabel("Y (m)")
ax.legend()

plt.savefig(
    "results/static_crowd.png",
    dpi=200,
    bbox_inches="tight",
)

plt.show()
