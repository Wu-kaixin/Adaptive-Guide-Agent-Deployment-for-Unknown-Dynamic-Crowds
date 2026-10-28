from types import SimpleNamespace

import numpy as np
from shapely.geometry import Point, box

from udc.multigroup.safe_routing import tangent_polygon, find_path, execute


def config():
    return dict(guide_distance_m=.75, wall_distance_m=.4, guide_radius_m=.2, clearance_m=.35,
                routing_sides=[16,32], dt_s=.1, max_speed_m_s=1., deadline_s=100., hold_steps=2)


def domain():
    return SimpleNamespace(free=box(.4,.4,9.6,9.6), conservative_obstacle=Point(20,20).buffer(.1))


def test_endpoint_facing_support_resolves_old_polygon_rejection():
    from udc.multigroup.geometry import outer_disk
    query = np.array([.75807*np.cos(.02), .75807*np.sin(.02)])
    assert outer_disk([0,0],.75,quad_segs=4).contains(Point(query))
    polygon = tangent_polygon([0,0],.75,[query,[3,3]])
    assert not polygon.covers(Point(query))
    angles=np.arange(360)*np.pi/180
    assert all(polygon.covers(Point(p)) for p in .75*np.c_[np.cos(angles),np.sin(angles)])


def test_search_finds_safe_alternative_around_stationary_robot():
    current=np.array([[2.,5.],[5.,5.]])
    path,_=find_path(current[0],[8,5],current,0,domain(),[10,10],config())
    assert path is not None and len(path)>2
    from shapely.geometry import LineString
    assert LineString(path).distance(Point(current[1]))>=.75


def test_reverse_assembly_is_safe_and_preserves_terminal_assignment():
    guides=np.array([[1.,1.],[9.,1.],[1.,9.]])
    targets=np.array([[4.,5.],[5.,5.],[6.,5.]])
    result=execute(guides,targets,domain(),[10,10],config(),policy='adaptive_reverse')
    assert result.status=='ARRIVED'
    assert np.max(np.linalg.norm(result.positions[-1]-targets[result.assignment],axis=1))<1e-12
    from udc.multigroup.evaluation import motion_metrics
    from udc.multigroup.observation import StaticTruth
    truth=StaticTruth(np.array([[20.,20.]]),np.array([.2]),np.ones(1),np.zeros(1,int))
    m=motion_metrics(result,truth,[10,10],dict(config(),numeric_tolerance_m=1e-7,speed_tolerance_m_s=.03))
    assert m['interval_safety_ok']
