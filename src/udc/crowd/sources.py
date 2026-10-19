"""Generate or import a snapshot, then keep it strictly frozen for Step 1."""
from pathlib import Path
import sqlite3

import jupedsim as jps
from jupedsim.distributions import AgentNumberError
import numpy as np


def static_positions(config, polygon, radius, seed):
    source = config.get('source', 'jupedsim')
    if source == 'grid':
        grid = config['grid']
        x, y = grid['origin']
        spacing = grid['spacing']
        return [(x+column*spacing, y+row*spacing)
                for row in range(grid['rows']) for column in range(grid['columns'])]
    if source == 'sqlite':
        path = Path(config['trajectory_file'])
        if not path.is_file():
            raise ValueError('INITIALIZATION_INVALID: trajectory_file does not exist')
        try:
            with sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True) as connection:
                rows = connection.execute(
                    'SELECT pos_x, pos_y FROM trajectory_data '
                    'WHERE frame=(SELECT MIN(frame) FROM trajectory_data) ORDER BY id'
                ).fetchall()
        except sqlite3.Error as error:
            raise ValueError(f'INITIALIZATION_INVALID: invalid JuPedSim database: {error}') from error
        if len(rows) != config['count']:
            raise ValueError('INITIALIZATION_INVALID: snapshot count does not match crowd.count')
        return np.asarray(rows, dtype=float)
    try:
        return jps.distribute_by_number(
            polygon=polygon, number_of_agents=config['count'],
            distance_to_agents=max(config['spacing'], 2*radius),
            distance_to_polygon=radius, seed=seed,
        )
    except AgentNumberError as error:
        raise ValueError(f'INITIALIZATION_INVALID: {error}') from error
