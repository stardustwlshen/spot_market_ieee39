from pathlib import Path
import json,subprocess,sys,shutil
ROOT=Path(__file__).resolve().parent
def test_relocated_package(tmp_path):
 package=tmp_path/'package';shutil.copytree(ROOT,package,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
 other=tmp_path/'other';other.mkdir()
 def call(*args):return subprocess.run([sys.executable,'-B',str(package/'market.py'),*args],cwd=other,capture_output=True,text=True)
 assert call('--help').returncode==0
 assert json.loads(call('readiness').stdout)['status']=='portable_pure_interfaces_ready'
 for name in ('thermal','storage','mixed','safety'):
  r=call('validate','--module',name,'--input',str(package/'examples'/f'{name}.json'));assert r.returncode==0,r.stdout+r.stderr
  r=call('run','--module',name,'--input',str(package/'examples'/f'{name}.json'));assert r.returncode==0,r.stdout+r.stderr
 assert call('readiness','--mode','formal').returncode==2
 assert call('backend-readiness').returncode==2
 bad=other/'bad.json';bad.write_text('{"schema":"gd.agc.r73.v1","mode":"research"}')
 assert call('validate','--module','thermal','--input',str(bad)).returncode==2
def test_relocated_small_build_and_native_dryrun(tmp_path):
 package=tmp_path/'package';shutil.copytree(ROOT,package,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
 other=tmp_path/'elsewhere';other.mkdir();prepared=tmp_path/'small';native=tmp_path/'not_executed'
 def call(*args):return subprocess.run([sys.executable,'-B',str(package/'market.py'),*args],cwd=other,capture_output=True,text=True)
 r=call('prepare-small','--output',str(prepared));assert r.returncode==0,r.stdout+r.stderr
 z=json.loads(r.stdout);assert z['variables']==1623 and z['rows']==3073 and z['native_calls']==0
 r=call('solve-prepared','--prepared',str(prepared),'--output',str(native),'--time-limit','30');assert r.returncode==0,r.stdout+r.stderr
 assert json.loads(r.stdout)['status']=='dryrun_only'and not native.exists()and not(prepared/'ATTEMPT.json').exists()
def test_explicit_codec_horizon_and_agc_decode_without_build(tmp_path,monkeypatch):
 import importlib.util,pytest
 spec=importlib.util.spec_from_file_location('delivery_decode_core',ROOT/'core.py');c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
 from smm.gd_october_job_codec import dumps
 from smm.gd_horizon import EnergyHorizon,EnergyPeriod
 import market_builder
 case={'energy_inputs':{'horizon':dict(periods=[dict(index=0,duration_h=1/12,ramp_elapsed_h=1/12,role='delivery',settle=True)],mode='component',time_mapping_source='synthetic decode test')}}
 (tmp_path/'case.json').write_bytes(dumps(case))
 (tmp_path/'grid.json').write_text(json.dumps([{'kind':'delivery'}]*96+[{'kind':'peak'},{'kind':'valley'}]))
 (tmp_path/'policy.json').write_text(json.dumps({'curtailment_penalty':{'yuan_per_mwh':0}}))
 (tmp_path/'agc.json').write_text(json.dumps({'G':{'up_mw':{'0':1},'down_mw':{'0':1}}}))
 bundle={'schema':'gd.delivery.scuc_inputs.v1','case_file':'case.json','grid_file':'grid.json','policy_file':'policy.json','agc_file':'agc.json'};(tmp_path/'bundle.json').write_text(json.dumps(bundle))
 class StopBeforeBuild(Exception):pass
 def inspect(case,grid,policy,agc):
  h=case['energy_inputs']['horizon'];assert isinstance(h,EnergyHorizon)and isinstance(h.periods[0],EnergyPeriod)and h.ids==(0,)
  assert agc['G']['up_mw']=={0:1};raise StopBeforeBuild
 monkeypatch.setattr(market_builder,'build_market',inspect)
 with pytest.raises(StopBeforeBuild):c.prepare(tmp_path/'neverbuilt',tmp_path/'bundle.json')
 assert not(tmp_path/'neverbuilt').exists()
