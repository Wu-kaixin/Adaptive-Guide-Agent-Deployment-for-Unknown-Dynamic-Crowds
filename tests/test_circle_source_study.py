from pathlib import Path
from types import SimpleNamespace

import numpy as np
from shapely.geometry import Point

from udc.analysis import circle_source_audit as module
from udc.experiments.static_multigroup import digest,write_json
from udc.multigroup.offset_interval import Interval


def test_transfer_refutation_preserves_geometry_state_and_stops_affected_batch(tmp_path,monkeypatch):
    parent=tmp_path/'parent';world=parent/'baseline';world.mkdir(parents=True)
    np.savez_compressed(world/'truth.npz',positions=np.array([[1.,1.]]))
    write_json(world/'receipt.json',{'truth.npz':digest(world/'truth.npz')});write_json(parent/'candidates.json',[])
    study=tmp_path/'study';study.mkdir()
    center=(Interval.point(1.),Interval.point(1.));output=(Interval(.75,1.25),Interval(.75,1.25))
    monkeypatch.setattr(module,'source_inputs',lambda *_:({'design':{'quad_segs':16}},parent,
        [SimpleNamespace(person_ids=(0,))],[center],[.25],{(.25).hex():.25},[output]))
    # Deliberately substitute a different center to exercise a scientific
    # transfer refutation, distinct from malformed inputs or a program error.
    monkeypatch.setattr(module.geometry,'outer_disk',lambda *a,**k:Point(5.,5.).buffer(.25,quad_segs=16))
    result=module.worker((str(study),0));case=study/'cases/000'
    assert result['hypothesis_refuted'] is True and result['worlds_checked']==0
    assert (study/'STOP.json').is_file()
    assert all((case/name).is_file() for name in ['transfer_counterexample.json','partial_world.json','native_circles.npz','receipt.json'])
