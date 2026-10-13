
from pathlib import Path

import matplotlib.pyplot as plt
from pedpy import (
    Cutoff,
    DENSITY_COL,
    MeasurementArea,
    compute_individual_voronoi_polygons,
    compute_neighbors,
    compute_voronoi_density,
    load_trajectory_from_jupedsim_sqlite,
    load_walkable_area_from_jupedsim_sqlite,
    plot_neighborhood,
    plot_voronoi_cells,
)

# 1. Load previous simulation
output_dir = Path("results")
db_file = output_dir / "static_50.sqlite"

traj = load_trajectory_from_jupedsim_sqlite(
    trajectory_file=db_file
)

area = load_walkable_area_from_jupedsim_sqlite(
    trajectory_file=db_file
)

frame = int(traj.data["frame"].min())

print("Pedestrians:", traj.data["id"].nunique())
print("Frame rate:", traj.frame_rate)

# 2. Compute individual Voronoi polygons
voronoi = compute_individual_voronoi_polygons(
    traj_data=traj,
    walkable_area=area,
    cut_off=Cutoff(radius=1.0, quad_segments=8),
)

# 3. Plot Voronoi diagram
fig, ax = plt.subplots(figsize=(8, 7))

plot_voronoi_cells(
    voronoi_data=voronoi,
    traj_data=traj,
    frame=frame,
    walkable_area=area,
    color_by_column=DENSITY_COL,
    axes=ax,
    vmin=0,
    vmax=4,
)

ax.set_xlim(14, 25)
ax.set_ylim(16, 24)
ax.set_aspect("equal")
ax.set_title("Pedestrian Voronoi Diagram")

fig.savefig(
    output_dir / "voronoi.png",
    dpi=200,
    bbox_inches="tight",
)

# 4. Compute neighborhood
neighbors = compute_neighbors(
    voronoi,
    as_list=False,
)

neighbors.to_csv(
    output_dir / "neighbors.csv",
    index=False,
)

# Choose a pedestrian near the crowd center
frame_data = traj.data[
    traj.data["frame"] == frame
].copy()

dist_sq = (
    (frame_data["x"] - 19.5) ** 2
    + (frame_data["y"] - 19.5) ** 2
)

pedestrian_id = int(
    frame_data.loc[dist_sq.idxmin(), "id"]
)

fig, ax = plt.subplots(figsize=(8, 7))

plot_neighborhood(
    pedestrian_id=pedestrian_id,
    voronoi_data=voronoi,
    frame=frame,
    neighbors=neighbors,
    walkable_area=area,
    axes=ax,
)

ax.set_xlim(14, 25)
ax.set_ylim(16, 24)
ax.set_aspect("equal")
ax.set_title(f"Neighborhood of Pedestrian {pedestrian_id}")

fig.savefig(
    output_dir / "neighborhood.png",
    dpi=200,
    bbox_inches="tight",
)

# 5. Compute crowd density in a central region
measurement_area = MeasurementArea([
    (17.0, 18.5),
    (22.0, 18.5),
    (22.0, 21.0),
    (17.0, 21.0),
])

density, _ = compute_voronoi_density(
    individual_voronoi_data=voronoi,
    measurement_area=measurement_area,
)

density.to_csv(
    output_dir / "density.csv",
    index=False,
)

# 6. Plot density over time
fig, ax = plt.subplots(figsize=(8, 4))

ax.plot(
    density["frame"] / traj.frame_rate,
    density["density"],
)

ax.set_xlabel("Time (s)")
ax.set_ylabel("Density (pedestrians/m²)")
ax.set_title("Voronoi Density Over Time")
ax.grid(True)

fig.savefig(
    output_dir / "density.png",
    dpi=200,
    bbox_inches="tight",
)

print("Mean density:", density["density"].mean())
print("Saved 3 figures and 2 CSV files")

plt.show()
