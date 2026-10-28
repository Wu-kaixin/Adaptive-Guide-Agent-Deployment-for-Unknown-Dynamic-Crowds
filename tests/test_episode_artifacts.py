import json
import numpy as np

from udc.experiments.episode_artifacts import EpisodeWriter
from udc.multigroup.execution import Execution


def test_chunked_episode_preserves_variable_trajectories_and_assignments(tmp_path):
    writer=EpisodeWriter(tmp_path/'episode',{'id':'fixed'}, {},2,2)
    expected=[]
    for i in range(3):
        p=np.array([[[i,0],[9,9]],[[i+1,0],[9,9]]],float);v=np.diff(p,axis=0)
        e=Execution(p,v,np.array([0,-1]),'ARRIVED',(),{})
        writer.append({'step':i,'status':'PARTIAL_SERVICE'},e,np.array([[i+1,0]]),np.array([0]),{'step':i})
        expected.append(p)
    writer.close({'sequence_completed':True})
    receipt=json.loads((tmp_path/'episode/completion.json').read_text())
    assert receipt['recorded_steps']==3 and receipt['chunks']==2
    with np.load(tmp_path/'episode/chunk_0000/trajectory.npz',allow_pickle=False) as data:
        assert np.array_equal(data['positions'],np.concatenate(expected[:2]))
        assert list(data['position_offsets'])==[0,2,4]
        assert np.array_equal(data['assignment'],[[0,-1],[0,-1]])
