"""Read a chosen accepted DA, derive explicit hypothetical initial issued boundary. No native."""
from pathlib import Path
from datetime import datetime,timedelta
from copy import deepcopy
import sys,json,hashlib,math,importlib.util,argparse
B=Path(__file__).resolve().parent;ROOT=B.parent
read=lambda p:json.loads(Path(p).read_bytes());sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def module(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def prepare(da,out):
 da=Path(da).resolve();out=Path(out).resolve();assert not out.exists()
 vdir=ROOT/'guangdong_rt_recommit_program_v1_20261112';validator=ROOT/'guangdong_rt_recommit_program_v32_20261112/interface_validators.py';guard=module('_short_seed_guard',validator);pins=guard.selected_da(da);legacy=module('_short_seed_legacy',vdir/'program.py');legacy.seed(da,out,vdir/'vendor')
 from smm.gd_october_job_codec import loads
 seed=out/'seed';case=loads((seed/'case.json').read_bytes());r=read(seed/'request.json');w=read(seed/'WARMSTART_SOURCE.json');assert sha(w['names'])==w['names_sha256']and sha(w['response'])==w['response_sha256'];v=dict(zip(read(w['names'])['variables'],read(w['response'])['primal']));origin=datetime.fromisoformat(r['origin']);delivery=origin+timedelta(minutes=15);thermal={};ages={};storage={}
 for g,z in case['energy_inputs']['thermal'].items():
  assert z['initial_state_kind']=='stable'and all(v[f'stable[{g},{t}]']>.5 for t in range(3)),'initial adapter currently requires proven stable three-cell issued prefix; no phase reset'
  age=z['initial_state_age_h']+.25;ages[g]=age
  thermal[g]=dict(phase='stable',source='chosen accepted DA first3 issued hypothetical no-deviation replay; not measured',available_at=r['cutoff'],end_power_mw=v[f'power[{g},2]'],residual_up_ticks=max(0,math.ceil(z['min_up_h']*12-age*12-1e-8)),residual_down_ticks=0,min_up_basis='startup_begin',min_down_basis='shutdown_end',event_id=None,event_elapsed_ticks=0,remaining_curve=[])
 for u,z in case['batteries'].items():
  eta=math.sqrt(z['roundtrip_efficiency']);E=z['initial_energy_mwh'];mode=z['initial_mode'];age=z['initial_mode_age_h'];ledger=deepcopy(z['cycle_budget'])
  for t in range(3):
   ch=v[f'battery.charge[{u},{t}]'];dis=v[f'battery.discharge[{u},{t}]'];new='charge'if v[f'battery.charge_mode[{u},{t}]']>.5 else'discharge'if v[f'battery.discharge_mode[{u},{t}]']>.5 else'idle';age=age+1/12 if new==mode else 1/12;mode=new;E+=(eta*ch-dis/eta)/12;assert abs(E-v[f'battery.energy[{u},{t}]'])<=1e-7;ledger[(origin+timedelta(minutes=5*t)).date().isoformat()]['used_internal_mwh']+=(eta*ch+dis/eta)/12
  hour=delivery.replace(minute=0,second=0,microsecond=0).isoformat();keys=[k for k in v if k.startswith('battery.hour_direction['+u+',')and hour in k];assert len(keys)==1
  storage[u]=dict(initial_energy_mwh=E,initial_power_mw=dis-ch,initial_mode=mode,initial_mode_age_h=age,cycle_budget=ledger,initial_hour_direction=1 if v[keys[0]]>.5 else -1)
 c=dict(schema='gd.short.carry.research.v2',mode='counterfactual_no_deviation',selected_DA=str(da),selected_DA_run_id=read(da/'RUN.json')['run_id'],seed_case_sha256=sha(seed/'case.json'),seed_request_sha256=sha(seed/'request.json'),origin=r['origin'],cutoff=r['cutoff'],cutoff_tick=0,delivery_tick=3,boundary_at=delivery.isoformat(),executed_end_exclusive=0,issued_end_exclusive=3,thermal=thermal,thermal_age_h=ages,storage=storage,notice_values=r['state']['notice_values'],notice_requirements=[],predicted_issued_prefix=True,source_pins={**pins,**{str(p):sha(p)for p in [Path(__file__),validator,*seed.glob('*.json'),Path(w['names']),Path(w['response'])]}},formal=False,safety_release=False)
 with(out/'INITIAL_CARRY.json').open('x')as f:json.dump(c,f,indent=2)
 return dict(status='selected_DA_short_seed_ready',selected_DA_run_id=c['selected_DA_run_id'],native_calls=0,formal=False,seed=str(seed),carry=str(out/'INITIAL_CARRY.json'))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--da',required=True);p.add_argument('--output',required=True);a=p.parse_args();print(json.dumps(prepare(a.da,a.output)))
