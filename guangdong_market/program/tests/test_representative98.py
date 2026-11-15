import importlib.util,pathlib
f=pathlib.Path(__file__).parents[1]/'app/gd_provincial15_model/representative98.py'
s=importlib.util.spec_from_file_location('r98',f);mod=importlib.util.module_from_spec(s);s.loader.exec_module(mod)
def test_extrema_reuse_and_no_input_mutation():
    c={'energy_inputs':{'load_mw':{('n',t):100+abs(t-20) for t in range(96)},'available':{t:t/2 for t in range(96)}}}
    out,meta=mod.extend(c)
    assert [x['source_day_quarter'] for x in meta]==[20,95]
    assert out['energy_inputs']['load_mw']['n',96]==100
    assert out['energy_inputs']['available'][97]==47.5
    assert len(c['energy_inputs']['available'])==96
    assert all(not x['settle'] and x['objective_duration_h']==.25 for x in meta)
def test_flat_profile_deterministic_roles():
    _,m=mod.extend({'energy_inputs':{'load_mw':{('n',t):100 for t in range(96)}}})
    assert [x['source_day_quarter'] for x in m]==[0,0]
    assert {x['kind'] for x in m}=={'peak','valley'}
