"""Fresh regression for exact-byte forecast consumption, no saved run dependency."""
import ast, hashlib, json, os, sys
from pathlib import Path
import pytest
P=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(P/'app'),str(P/'app/gd_live_market_runtime')]
import gd_forecast_inputs as f
import forecast_binding as binding

@pytest.fixture
def bundle(tmp_path,monkeypatch):
    z={'resources':{},'baseline_case_digest':'baseline','notice':'test'}
    z['bundle_digest']=f.digest(z)
    p=tmp_path/f.FILE;p.write_text(json.dumps(z));(tmp_path/'case.json').write_bytes(b'case')
    monkeypatch.setattr(f,'_baseline_digest_from_bytes',lambda raw:'baseline')
    pins={str(p.resolve()):hashlib.sha256(p.read_bytes()).hexdigest()}
    return p,pins

@pytest.mark.parametrize('action',['forecast','actual','read'])
def test_bound_consumption_success(bundle,action):
    p,pins=bundle
    if action=='forecast':assert f.apply_forecast({'energy_inputs':{}},p.parent,0,accepted_source_pins=pins)['_forecast_proof']['issue_tick']==0
    elif action=='actual':assert f.apply_actual({'energy_inputs':{}},p.parent,accepted_source_pins=pins)=={'energy_inputs':{}}
    else:assert f.read_bundle(p.parent,accepted_source_pins=pins)['resources']=={}

@pytest.mark.parametrize('change',['delete','replace','same_mtime'])
@pytest.mark.parametrize('action',['forecast','actual','source_pin','read'])
def test_consumers_reject_changed_bound_bytes(bundle,change,action):
    p,pins=bundle;st=p.stat()
    if change=='delete':p.unlink()
    else:
        z=json.loads(p.read_bytes());z.pop('bundle_digest');z['notice']='replacement';z['bundle_digest']=f.digest(z);p.write_text(json.dumps(z))
        if change=='same_mtime':os.utime(p,ns=(st.st_atime_ns,st.st_mtime_ns))
    with pytest.raises(ValueError,match='missing|changed'):
        if action=='forecast':f.apply_forecast({},p.parent,0,accepted_source_pins=pins)
        elif action=='actual':f.apply_actual({},p.parent,accepted_source_pins=pins)
        elif action=='source_pin':binding.source_pin(p.parent,pins)
        else:f.read_bundle(p.parent,accepted_source_pins=pins)

def test_no_bound_forecast_remains_supported(tmp_path):
    assert f.read_bundle(tmp_path,accepted_source_pins={}) is None
    assert binding.source_pin(tmp_path,{})=={}
    assert f.apply_forecast({},tmp_path,0,accepted_source_pins={})=={}
    assert f.apply_actual({},tmp_path,accepted_source_pins={})=={}

def test_present_unbound_file_rejected(bundle):
    p,_=bundle
    with pytest.raises(ValueError,match='absent from accepted'):f.read_bundle(p.parent,accepted_source_pins={})

def test_replace_only_during_read_then_restore_rejected(bundle,monkeypatch):
    p,pins=bundle;raw=p.read_bytes();real=Path.read_bytes
    def transient(path):
        if path==p:return raw+b' '
        return real(path)
    monkeypatch.setattr(Path,'read_bytes',transient)
    with pytest.raises(ValueError,match='changed'):f.read_bundle(p.parent,accepted_source_pins=pins)
    assert real(p)==raw

def test_deleted_between_source_check_and_consumption(bundle,monkeypatch):
    p,pins=bundle;real=binding.source_pin
    def checked_then_removed(seed,pins):
        result=real(seed,pins);p.unlink();return result
    monkeypatch.setattr(binding,'source_pin',checked_then_removed)
    with pytest.raises(ValueError,match='missing'):binding.for_execution({},p.parent,pins=pins)

def test_replaced_between_source_check_and_consumption(bundle,monkeypatch):
    p,pins=bundle;real=binding.source_pin
    def checked_then_changed(seed,pins):
        result=real(seed,pins);p.write_bytes(p.read_bytes()+b' ');return result
    monkeypatch.setattr(binding,'source_pin',checked_then_changed)
    with pytest.raises(ValueError,match='changed'):binding.for_execution({},p.parent,pins=pins)

@pytest.mark.parametrize('relative,callee,source',[
 ('app/gd_provincial15_model/compiler.py','apply_forecast',"c['source_pins']"),
 ('app/research_runtime/single_window/price/single_price.py','sample',"host_binding['source_pins']"),
 ('app/gd_live_market_runtime/branch_parameters.py','apply_forecast','accepted_source_pins')])
def test_actual_call_passes_accepted_binding(relative,callee,source):
    tree=ast.parse((P/relative).read_text())
    calls=[n for n in ast.walk(tree)if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id==callee]
    assert len(calls)==1
    kw=[k.value for k in calls[0].keywords if k.arg=='accepted_source_pins']
    assert len(kw)==1 and ast.unparse(kw[0])==source

def test_real_seed_forecast_and_actual_equivalence(tmp_path):
    from copy import deepcopy
    from gd_provincial15_model.compiler import loads
    from gd_provincial15_model.adapter import convert_full_case
    raw=(P/'data/default_scenario/seed/case.json').read_bytes()
    (tmp_path/'case.json').write_bytes(raw)
    original=loads(raw)
    f.create(original,tmp_path,{'wind':'normal','solar':'normal','load':'normal'})
    p=tmp_path/f.FILE;pins={str(p.resolve()):hashlib.sha256(p.read_bytes()).hexdigest()}
    case=convert_full_case(original)
    for tick in (0,47,95):
        assert f.apply_forecast(deepcopy(case),tmp_path,tick)==f.apply_forecast(deepcopy(case),tmp_path,tick,accepted_source_pins=pins)
    assert f.apply_actual(deepcopy(case),tmp_path)==f.apply_actual(deepcopy(case),tmp_path,accepted_source_pins=pins)

def test_source_proof_never_resigns_forecast(bundle,monkeypatch):
    p,pins=bundle;real=binding._sha
    def sha_then_change(path):
        value=real(path)
        if Path(path)==p:p.write_bytes(p.read_bytes()+b' ')
        return value
    monkeypatch.setattr(binding,'_sha',sha_then_change)
    proof=binding.source_pin(p.parent,pins)
    assert proof[str(p)]==pins[str(p)]
    with pytest.raises(ValueError,match='changed'):f.verified_forecast_pins(p.parent,pins)

@pytest.mark.parametrize('change',['delete','replace','same_mtime'])
def test_preparation_completion_rechecks_accepted_forecast(bundle,change):
    p,pins=bundle;st=p.stat()
    if change=='delete':p.unlink()
    else:
        p.write_bytes(p.read_bytes()+b' ')
        if change=='same_mtime':os.utime(p,ns=(st.st_atime_ns,st.st_mtime_ns))
    with pytest.raises(ValueError,match='missing|changed'):f.verified_forecast_pins(p.parent,pins)

def test_compiler_completion_preserves_pins():
    tree=ast.parse((P/'app/gd_provincial15_model/compiler.py').read_text())
    calls=[n for n in ast.walk(tree)if isinstance(n,ast.Call)and isinstance(n.func,ast.Name)and n.func.id=='verified_forecast_pins']
    assert len(calls)==1 and ast.unparse(calls[0].args[1])=="c['source_pins']"
