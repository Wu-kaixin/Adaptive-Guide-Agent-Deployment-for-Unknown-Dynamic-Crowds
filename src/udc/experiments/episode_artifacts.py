"""Small recoverable chunks for multi-step episodes; no repeated truth files."""
from pathlib import Path

import numpy as np

from .static_multigroup import write_json, digest


class EpisodeWriter:
    def __init__(self, folder, task, model, guides, chunk_size=5):
        self.folder, self.task, self.model = Path(folder), task, model
        self.guides, self.chunk_size, self.rows, self.chunk, self.total = guides, chunk_size, [], 0, 0
        self.folder.mkdir(parents=True, exist_ok=False)
        write_json(self.folder/'config.json',{'task':task,'model':model,'chunk_size':chunk_size})

    def append(self, metrics, execution, targets, ids, state):
        self.rows.append((metrics,execution,np.asarray(targets),np.asarray(ids),state))
        if len(self.rows)>=self.chunk_size:self.flush()

    def flush(self):
        if not self.rows:return
        folder=self.folder/f'chunk_{self.chunk:04d}';folder.mkdir()
        positions,velocities,assignments,targets,ids,counts=[],[],[],[],[],[]
        po,vo=[0],[0]
        for metrics,execution,q,g,state in self.rows:
            positions.append(execution.positions);velocities.append(execution.velocities);assignments.append(execution.assignment)
            padded=np.zeros((self.guides,2));padded[:len(q)]=q
            labels=np.full(self.guides,-1,int);labels[:len(g)]=g
            targets.append(padded);ids.append(labels);counts.append(len(q))
            po.append(po[-1]+len(execution.positions));vo.append(vo[-1]+len(execution.velocities))
        np.savez_compressed(folder/'trajectory.npz',positions=np.concatenate(positions),velocities=np.concatenate(velocities),
            position_offsets=po,velocity_offsets=vo,assignment=np.asarray(assignments),targets=np.asarray(targets),target_groups=np.asarray(ids),target_counts=counts)
        write_json(folder/'metrics.json',[r[0] for r in self.rows]);write_json(folder/'state.json',[r[4] for r in self.rows])
        write_json(folder/'receipt.json',{name:digest(folder/name) for name in ('trajectory.npz','metrics.json','state.json')})
        self.total+=len(self.rows);self.rows.clear();self.chunk+=1

    def close(self, summary=None):
        self.flush()
        write_json(self.folder/'completion.json',{'recorded_steps':self.total,'chunks':self.chunk,
            'chunk_receipt_sha256':{f.relative_to(self.folder).as_posix():digest(f) for f in sorted(self.folder.glob('chunk_*/receipt.json'))},
            'config_sha256':digest(self.folder/'config.json'),'summary':summary})
