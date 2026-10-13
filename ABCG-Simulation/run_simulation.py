
from pathlib import Path

import jupedsim as jps
import numpy as np
from shapely.geometry import Polygon

# 1. Create environment
output_dir = Path("results")
output_dir.mkdir(exist_ok=True)

output_file = output_dir / "static_50.sqlite"

if output_file.exists():
    output_file.unlink()

walkable_area = Polygon([
    (0, 0),
    (40, 0),
    (40, 40),
    (0, 40),
])

# 2. Create simulation
dt = 0.02

sim = jps.Simulation(
    model=jps.CollisionFreeSpeedModel(),
    geometry=walkable_area,
    dt=dt,
    trajectory_writer=jps.SqliteTrajectoryWriter(
        output_file=output_file,
        every_nth_frame=5,
    ),
)

# 3. Set initial pedestrian positions
# 5 rows x 10 columns = 50 pedestrians
positions = [
    (16.0 + col * 0.75, 18.0 + row * 0.75)
    for row in range(5)
    for col in range(10)
]

agent_ids = []

# 4. Add pedestrians
for position in positions:

    # Each pedestrian waits at its initial position
    stage_id = sim.add_waiting_set_stage([position])
    sim.get_stage(stage_id).state = (
        jps.WaitingSetState.ACTIVE
    )

    journey_id = sim.add_journey(
        jps.JourneyDescription([stage_id])
    )

    agent_id = sim.add_agent(
        jps.CollisionFreeSpeedModelAgentParameters(
            position=position,
            radius=0.2,
            desired_speed=1.2,
            journey_id=journey_id,
            stage_id=stage_id,
        )
    )

    agent_ids.append(agent_id)

print(f"Created {len(agent_ids)} pedestrians")

# 5. Run simulation for 10 seconds
for _ in range(int(10.0 / dt)):
    sim.iterate()

# 6. Check pedestrian movement
final_positions = np.array([
    sim.agent(agent_id).position
    for agent_id in agent_ids
])

initial_positions = np.array(positions)

max_displacement = np.max(
    np.linalg.norm(
        final_positions - initial_positions,
        axis=1,
    )
)

print(f"Maximum displacement: {max_displacement:.6f} m")
print(f"Saved trajectory: {output_file}")
