"""JuPedSim static crowd and independent guide initialization."""
import numpy as np
from shapely.geometry import Polygon

from udc.interfaces import GuideState, PedestrianState, Perception
from udc.scene import build_scene
from udc.crowd.sources import static_positions


def sample_attribute(config, count, rng):
    mean, std, low, high = [float(config[k]) for k in ['mean', 'std', 'min', 'max']]
    if not np.isfinite([mean, std, low, high]).all() or std < 0 or low <= 0 or not low <= mean <= high:
        raise ValueError('Invalid attribute distribution')
    return np.clip(rng.normal(mean, std, count), low, high)


class Environment:
    def __init__(self, config):
        self.scene = build_scene(config['scene'])
        raw = config['crowd']
        seed = config['simulation']['seed']
        count = raw['count']
        radius_seed, demand_seed = np.random.SeedSequence(seed).spawn(2)
        self._radii = sample_attribute(raw['radius'], count, np.random.default_rng(radius_seed))
        self._demand = sample_attribute(raw['demand'], count, np.random.default_rng(demand_seed))
        vertices = np.asarray(raw['spawn_vertices'], dtype=float)
        if vertices.ndim != 2 or vertices.shape[1] != 2 or len(vertices) < 3 or not np.isfinite(vertices).all():
            raise ValueError('Spawn vertices must be finite (N, 2), N >= 3')
        polygon = Polygon(vertices)
        if not polygon.is_valid or polygon.area <= 0 or not self.scene.polygon.covers(polygon):
            raise ValueError('Crowd spawn polygon must be valid and inside the scene')
        radius = float(self._radii.max())
        generated = static_positions(raw, polygon, radius, seed)
        self._positions = np.asarray(generated, dtype=float)
        if self._positions.shape != (count, 2) or not np.isfinite(self._positions).all():
            raise ValueError('JuPedSim could not generate the requested crowd')
        self._ids = np.arange(count, dtype=int)
        self._apply_demand_region(raw)
        from shapely.geometry import Point
        if not all(self.scene.polygon.covers(Point(p).buffer(float(r)))
                   for p, r in zip(self._positions, self._radii)):
            raise ValueError('INITIALIZATION_INVALID: pedestrian disk outside scene')
        distances = np.linalg.norm(self._positions[:, None]-self._positions[None, :], axis=2)
        np.fill_diagonal(distances, np.inf)
        if np.any(distances < self._radii[:, None]+self._radii[None, :]-1e-8):
            raise ValueError('INITIALIZATION_INVALID: overlapping pedestrian disks')
        self._ids.setflags(write=False)
        for data in [self._positions, self._radii, self._demand, self._hotspot_mask]:
            data.setflags(write=False)

    def _apply_demand_region(self, raw):
        """Raise planning weight inside a declared disk. This is not a risk model.

        ``demand_i = clip(base_i * multiplier, min, max)`` for people whose
        centre lies within ``radius`` of ``center``; everyone else keeps the
        base draw. The field is computed once here and frozen afterwards.
        """
        region = raw.get('demand_region')
        self._hotspot_mask = np.zeros(len(self._positions), dtype=bool)
        self._demand_clipped = 0
        if region is None:
            return
        center = np.asarray(region['center'], dtype=float)
        distance = np.linalg.norm(self._positions - center, axis=1)
        inside = distance <= float(region['radius'])
        adjusted = np.array(self._demand, copy=True)
        adjusted[inside] *= float(region['multiplier'])
        low, high = float(raw['demand']['min']), float(raw['demand']['max'])
        clipped = np.clip(adjusted, low, high)
        self._demand_clipped = int(np.sum(clipped != adjusted))
        self._demand = clipped
        self._hotspot_mask = inside

    @property
    def hotspot_mask(self):
        """People inside the declared demand region (all False without one)."""
        return self._hotspot_mask.copy()

    @property
    def demand_clipped(self):
        """How many hotspot weights were cut by crowd.demand.max."""
        return self._demand_clipped

    def perceive(self, time, guides=None, guide_velocities=None):
        people = PedestrianState(
            self._ids.copy(), self._positions.copy(), self._radii.copy(),
            self._demand.copy(), np.zeros_like(self._positions),
        )
        guide_state = None
        if guides is not None:
            guides = np.asarray(guides, dtype=float)
            if guide_velocities is None:
                guide_velocities = np.zeros_like(guides)
            else:
                guide_velocities = np.asarray(guide_velocities, dtype=float)
            guide_state = GuideState(guides.copy(), guide_velocities.copy())
        return Perception(float(time), people, guide_state)

    def observe(self):
        return self.perceive(0.0).observation()

    def advance(self, dt):
        """Step 1 is frozen. Step 2 will add human dynamics here."""
        if not np.isfinite(dt) or dt <= 0:
            raise ValueError('dt must be finite and positive')

    def initialize_guides(self, config):
        if 'initial_positions' in config:
            positions = np.asarray(config['initial_positions'], dtype=float)
            if positions.shape != (config['count'], 2) or not np.isfinite(positions).all():
                raise ValueError('INITIALIZATION_INVALID: guide snapshot must be finite (count, 2)')
            # The runner applies the unchanged human/guide/wall safety checks.
            return np.array(positions, copy=True)
        inset = config['initial_inset']
        if 2*inset >= min(self.scene.width, self.scene.height):
            raise ValueError('Guide initialization inset is too large')
        line = self.scene.polygon.buffer(-inset, join_style='mitre').exterior
        count = config['count']
        return np.array([line.interpolate((i/count+.012)*line.length % line.length).coords[0] for i in range(count)])
