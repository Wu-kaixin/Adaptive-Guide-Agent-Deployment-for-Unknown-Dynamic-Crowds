"""The deadline diagnostic changes only elapsed-time termination, never policy."""
import ast
import inspect
import pytest
from udc.experiments import runner
from udc.experiments.deadline_diagnostics import transform_deadline, canonical_events, deadline_entry


def toy_source():
    return '''
def experiment(config, *, execute=True, stop=None):
    sim=config['simulation']
    queue=[]; samples=[]; recoveries=0
    for step in range(0 if execute else sim['max_steps'], sim['max_steps']):
        queue.append(step)
        samples.append(step)
        if step in (10,20,30): recoveries+=1
        if stop is not None and step==stop: break
    return queue,samples,recoveries,config
'''


def test_transform_only_one_AST_bound_and_preserves_actual_runner_other_branches():
    tree,proof=transform_deadline(inspect.getsource(runner.experiment))
    loop=next(n for n in ast.walk(tree) if isinstance(n,ast.For) and isinstance(n.target,ast.Name) and n.target.id=='step')
    assert loop.iter.args[1].value==600 and proof['changed_nodes']==1
    loop.iter.args[1]=ast.parse("sim['max_steps']",mode='eval').body
    assert ast.dump(tree)==ast.dump(ast.parse(inspect.getsource(runner.experiment)))


def test_continuation_preserves_400_prefix_queue_window_counters_and_original_config():
    source=toy_source(); baseline={}; exec(source,baseline)
    tree,_=transform_deadline(source); diagnostic={}; exec(compile(tree,'<test>', 'exec'),diagnostic)
    cfg={'simulation':{'max_steps':400}}
    a=baseline['experiment'](cfg); b=diagnostic['experiment'](cfg)
    assert a[0]==b[0][:400] and a[1]==b[1][:400]
    assert b[0]==b[1]==list(range(600))
    assert a[2]==b[2]==3 and b[3] is cfg and cfg['simulation']['max_steps']==400


def test_original_non_time_termination_is_not_suppressed_by_continuation():
    source=toy_source(); tree,_=transform_deadline(source); diagnostic={}; exec(compile(tree,'<test>', 'exec'),diagnostic)
    cfg={'simulation':{'max_steps':400}}
    # Original safety/stall/budget exits are unchanged breaks, including after 40s.
    for stop in (199,419,599):
        assert len(diagnostic['experiment'](cfg,stop=stop)[0])==stop+1


def test_transform_rejects_ambiguous_or_changed_loop_and_unbounded_extension():
    for limit in (400,601,600.,True):
        with pytest.raises(ValueError): transform_deadline(toy_source(),limit)
    with pytest.raises(ValueError): transform_deadline(toy_source().replace("sim['max_steps']):","401):"))
    with pytest.raises(ValueError): transform_deadline(toy_source().replace('    return queue',
        '    for step in range(1): pass\n    return queue'))


def test_timing_only_event_comparison_does_not_hide_queue_budget_or_identity_changes():
    a={'wall_seconds':1.,'after':{'assignment':'abc','budget':3,'samples':[2.,1.]}}
    b={'wall_seconds':9.,'after':{'assignment':'abc','budget':3,'samples':[2.,1.]}}
    assert canonical_events(a)==canonical_events(b)
    b['after']['budget']=2
    assert canonical_events(a)!=canonical_events(b)
    b['after']['budget']=3; b['after']['samples']=[1.]
    assert canonical_events(a)!=canonical_events(b)


def test_isolated_entry_rejects_checkpoint_resume_or_planning_only_without_running():
    entry,_=deadline_entry()
    for kwargs in ({'execute':False},{'resume':True},{'restart_interrupted':True}):
        with pytest.raises(ValueError,match='t=0'): entry('unused-config','unused-output',**kwargs)
