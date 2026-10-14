"""JuPedSim static crowd and independent guide initialization."""
import numpy as np
from shapely.geometry import Polygon

from udc.interfaces import Observation
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
        from shapely.geometry import Point
        if not all(self.scene.polygon.covers(Point(p).buffer(float(r)))
                   for p, r in zip(self._positions, self._radii)):
            raise ValueError('INITIALIZATION_INVALID: pedestrian disk outside scene')
        distances = np.linalg.norm(self._positions[:, None]-self._positions[None, :], axis=2)
        np.fill_diagonal(distances, np.inf)
        if np.any(distances < self._radii[:, None]+self._radii[None, :]-1e-8):
            raise ValueError('INITIALIZATION_INVALID: overlapping pedestrian disks')
        profile = raw.get('demand_profile')
        if profile is not None:
            axis = 0 if profile['axis'] == 'x' else 1
            coordinates = self._positions[:, axis]
            threshold = np.quantile(coordinates, profile['quantile'])
            hotspot = coordinates >= threshold
            self._demand = np.where(
                hotspot, float(profile['ratio']), 1.0
            )
            self._demand /= self._demand.mean()
        for data in [self._positions, self._radii, self._demand]:
            data.setflags(write=False)

    def observe(self):
        return Observation(self._positions.copy(), self._radii.copy(), self._demand.copy())

    def advance(self, dt):
        """Step 1 is frozen. Step 2 will add human dynamics here."""
        if not np.isfinite(dt) or dt <= 0:
            raise ValueError('dt must be finite and positive')

    def initialize_guides(self, config):
        inset = config['initial_inset']
        if 2*inset >= min(self.scene.width, self.scene.height):
            raise ValueError('Guide initialization inset is too large')
        line = self.scene.polygon.buffer(-inset, join_style='mitre').exterior
        count = config['count']
        return np.array([line.interpolate((i/count+.012)*line.length % line.length).coords[0] for i in range(count)])
