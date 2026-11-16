"""Portable request-local case snapshot tests: synthetic data, no saved runs."""
from pathlib import Path
import hashlib,sys,os
import pytest
# Works both under repository tests/ and isolated candidate/.
P=Path(__file__).resolve().parents[1]
if not (P/'app').exists():P=P/'snapshot_program/program'
sys.path[:0]=[str(P/'app'),str(P/'app/gd_live_market_runtime')]
import gd_case_snapshot as snap
from gd_provincial15_model.compiler import dumps,loads
from gd_provincial15_model.adapter import convert_full_case
import gd_forecast_inputs as forecast

def raw_case():
 n=6
 return {'energy_inputs':{'horizon':{'periods':[{'index':i}for i in range(n)]},'load_mw':{('1',i):10.+i for i in range(n)},'renewable_mw':{('1',i):2. for i in range(n)},'thermal':{'G':{'initial_power_mw':10.,'max_mw':{i:20. for i in range(n)},'segments':[{'price_yuan_per_mwh':{i:100. for i in range(n)}}]}}},'curves':{},'contract':{},'battery_contract':{'storage':{'battery_parameters_source_id':'synthetic','pumped_storage':{}},'sources':{'synthetic':{}}},'batteries':{'B':{'initial_energy_mwh':5.}}}

@pytest.fixture
def casefile(tmp_path):
 p=tmp_path/'case.json';p.write_bytes(dumps(raw_case()));return p

def create(p):
 h=hashlib.sha256(p.read_bytes()).hexdigest();return snap.CaseSnapshot(p,h,{str(p.resolve()):h})

def test_clone_matches_fresh_and_isolated(casefile):
 c=create(casefile);a=c.clone(casefile);b=c.clone(casefile)
 assert a==convert_full_case(loads(casefile.read_bytes()))
 a['batteries']['B']['initial_energy_mwh']=999
 assert c.clone(casefile)==b
 assert c.baseline(casefile)==forecast.baseline_digest(b)
 assert str(Path(snap.__file__).resolve()) in c.source_pins(casefile)

@pytest.mark.parametrize('field',['bid','capacity','initial_power','soc'])
def test_A_B_A_current_values(casefile,field):
 raw=casefile.read_bytes();a=create(casefile).clone(casefile);x=loads(raw);g=x['energy_inputs']['thermal']['G']
 if field=='bid':g['segments'][0]['price_yuan_per_mwh'][0]+=10
 elif field=='capacity':
  for t in range(3):g['max_mw'][t]+=10
 elif field=='initial_power':g['initial_power_mw']+=1
 else:x['batteries']['B']['initial_energy_mwh']+=1
 casefile.write_bytes(dumps(x));b=create(casefile).clone(casefile)
 assert b==convert_full_case(loads(casefile.read_bytes())) and b!=a
 casefile.write_bytes(raw);assert create(casefile).clone(casefile)==a

def test_same_mtime_drift_and_binding_rejected(casefile):
 c=create(casefile);st=casefile.stat();casefile.write_bytes(casefile.read_bytes()+b' ');os.utime(casefile,ns=(st.st_atime_ns,st.st_mtime_ns))
 with pytest.raises(ValueError,match='bytes changed'):c.clone(casefile)
 with pytest.raises(ValueError,match='match request'):snap.CaseSnapshot(casefile,'0'*64,{})
 with pytest.raises(ValueError,match='exact internal'):snap.require_snapshot(object())

def test_bundle_is_not_cached(casefile):
 import json
 c=create(casefile);p=casefile.parent/forecast.FILE
 z={'baseline_case_digest':c.baseline(casefile),'resources':{},'version':1};z['bundle_digest']=forecast.digest(z);p.write_text(json.dumps(z))
 assert forecast.read_bundle(casefile.parent,_case_snapshot=c)['version']==1
 z.pop('bundle_digest');z['version']=2;z['bundle_digest']=forecast.digest(z);p.write_text(json.dumps(z))
 assert forecast.read_bundle(casefile.parent,_case_snapshot=c)['version']==2
 z['version']=3;p.write_text(json.dumps(z))
 with pytest.raises(ValueError,match='bundle digest mismatch'):forecast.read_bundle(casefile.parent,_case_snapshot=c)


def test_existing_code_pin_cannot_be_resigned(casefile):
 h=hashlib.sha256(casefile.read_bytes()).hexdigest()
 code=str(Path(snap.__file__).resolve())
 with pytest.raises(ValueError,match='conflicts with accepted source pin'):
  snap.CaseSnapshot(casefile,h,{str(casefile.resolve()):h,code:'0'*64})


def test_code_drift_during_conversion_rejected(casefile,tmp_path,monkeypatch):
 from gd_provincial15_model import adapter
 fake=tmp_path/'adapter.py';fake.write_bytes(Path(adapter.__file__).read_bytes())
 monkeypatch.setattr(adapter,'__file__',str(fake))
 original=adapter.convert_full_case
 def changed(case):
  result=original(case)
  fake.write_bytes(fake.read_bytes()+b'\n# drift during conversion\n')
  return result
 monkeypatch.setattr(adapter,'convert_full_case',changed)
 with pytest.raises(ValueError,match='conversion code changed'):create(casefile)
