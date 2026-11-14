"""Explicit no-deviation short RT continuation; one native occurrence per stage, no retries."""
from pathlib import Path
from copy import deepcopy
from datetime import datetime,timedelta
import sys,json,gzip,hashlib,math,importlib.util
B=Path(__file__).resolve().parent;ROOT=B.parent
sys.path.insert(0,str(ROOT/'guangdong_rt_short_state_review_20261112'))
from thermal_prefix_reader import derive_prefix
from builder import build,loads
s=importlib.util.spec_from_file_location('_short_native',ROOT/'guangdong_rt_short_physical_v1_20261112/run_actual39.py');worker=importlib.util.module_from_spec(s);s.loader.exec_module(worker)
from acceptance import checks
from contracts import fixed_sced
from evidence_io import save,write_json
read=lambda p:json.loads(Path(p).read_bytes());sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def checked_pair(prepared,actual):
 src=Path(prepared);a=Path(actual);summary=read(src/'SUMMARY.json')
 for f,h in summary['source_pins'].items():assert sha(f)==h,('producer drift',f)
 n=read(src/'names.json');q=read(src/'template.json');uq=json.loads(gzip.decompress((a/'SCUC/request.json.gz').read_bytes()));u=json.loads(gzip.decompress((a/'SCUC/response.json.gz').read_bytes()));eq=json.loads(gzip.decompress((a/'SCED/request.json.gz').read_bytes()));e=json.loads(gzip.decompress((a/'SCED/response.json.gz').read_bytes()))
 # Current producer may change warmstart/options only; bounds/rows/objective/names must match.
 for k in ('rows','objective'):assert q[k]==uq[k]
 assert [{k:v for k,v in c.items()if k!='start'}for c in q['variables']]==[{k:v for k,v in c.items()if k!='start'}for c in uq['variables']]
 expect=fixed_sced(uq,u['primal']);assert expect['variables']==eq['variables']and expect['rows']==eq['rows']and expect['objective']==eq['objective']
 for stage in ('SCUC','SCED'):
  d=a/stage;job=read(d/'JOB.json');done=read(d/'COMPLETED.json');assert sha(job['worker'])==job['worker_sha256'];assert worker.load(job['request'])==json.loads(gzip.decompress((d/'request.json.gz').read_bytes()));assert worker.load(done['response'])==json.loads(gzip.decompress((d/'response.json.gz').read_bytes()))
 ua=checks(uq,u,n,True);ea=checks(eq,e,n,False);return n,e

def carry(seed,prepared,actual,out):
 seed=Path(seed).resolve();src=Path(prepared).resolve();a=Path(actual).resolve();out=Path(out).resolve();n,e=checked_pair(src,a);case=loads((src/'derived_case.json').read_bytes());bd=read(src/'BOUNDARY.json');t=derive_prefix(case,bd,n,e['primal'],executed_cells=3);v=dict(zip(n['variables'],e['primal']));origin=datetime.fromisoformat(case['contract']['origin']);begin=datetime.fromisoformat(case['contract']['domain_start']);delivery=begin+timedelta(minutes=15);cut=begin;storage={};ages={}
 for g,z in t['boundary_states'].items():
  old=case['energy_inputs']['thermal'][g];assert bd['thermal'][g]['phase']=='stable'and z['phase']=='stable','active carry age not yet audited; fail closed'
  ages[g]=old['initial_state_age_h']+.25
 for u,b in case['batteries'].items():
  E=b['initial_energy_mwh'];eta=math.sqrt(b['roundtrip_efficiency']);mode=b['initial_mode'];age=b['initial_mode_age_h'];ledger=deepcopy(b['cycle_budget'])
  for i in range(3):
   ch=v[f'battery.charge[{u},{i}]'];dis=v[f'battery.discharge[{u},{i}]'];new='charge'if v[f'battery.charge_mode[{u},{i}]']>.5 else'discharge'if v[f'battery.discharge_mode[{u},{i}]']>.5 else'idle';age=age+1/12 if new==mode else 1/12;mode=new;E+=(eta*ch-dis/eta)/12;assert abs(E-v[f'battery.energy[{u},{i}]'])<=1e-7;date=(begin+timedelta(minutes=5*i)).date().isoformat();ledger[date]['used_internal_mwh']+=(eta*ch+dis/eta)/12
  oldhour=(delivery-timedelta(minutes=5)).replace(minute=0,second=0,microsecond=0);hour=delivery.replace(minute=0,second=0,microsecond=0)
  keys=[k for k in v if k.startswith('battery.hour_direction['+u+',')and oldhour.isoformat() in k];assert len(keys)==1
  storage[u]=dict(initial_energy_mwh=E,initial_power_mw=dis-ch,initial_mode=mode,initial_mode_age_h=age,cycle_budget=ledger,initial_hour_direction=(1 if v[keys[0]]>.5 else -1)if oldhour==hour else 0)
 base=read(seed/'request.json');notice=deepcopy(read(src/'CARRY.json')['notice_values'])if(src/'CARRY.json').exists()else deepcopy(base['state']['notice_values'])
 # Explicit simulated publication: issue only notices whose issuing tick has arrived by this new cutoff.
 required=t['notice_requirements'];cut_tick=round((cut-origin).total_seconds()/300)
 # Until generic nonzero notice receipt is supported, reject rather than invent notification authority.
 assert not any(v[f'start[{g},{i}]']>.5 for g in case['energy_inputs']['thermal'] for i in range(48)),'nonzero startup requires explicit notice receipt importer'
 z=dict(schema='gd.short.carry.research.v2',mode='counterfactual_no_deviation',seed_case_sha256=sha(seed/'case.json'),seed_request_sha256=sha(seed/'request.json'),origin=origin.isoformat(),cutoff=cut.isoformat(),cutoff_tick=cut_tick,delivery_tick=cut_tick+3,boundary_at=delivery.isoformat(),executed_end_exclusive=cut_tick,issued_end_exclusive=cut_tick+3,thermal=t['boundary_states'],thermal_age_h=ages,storage=storage,notice_values=notice,notice_requirements=required,predicted_issued_prefix=True,source_pins={str(p):sha(p)for p in [src/'SUMMARY.json',src/'derived_case.json',src/'BOUNDARY.json',src/'names.json',a/'ACCEPTED_PAIR.json',a/'SCED/response.json.gz',Path(__file__)]},formal=False,safety_release=False)
 write_json(out,z);return z

def solve(prepared,out):
 src=Path(prepared);out=Path(out);s=read(src/'SUMMARY.json')
 for f,h in s['source_pins'].items():assert sha(f)==h
 out.mkdir(exist_ok=False);q=read(src/'template.json');n=read(src/'names.json');save(out/'SCUC_template.json.gz',q)
 uq,u=worker.native(out,'SCUC',q,worker.V/'scuc_worker.py',300.);ua=checks(uq,u,n,True);write_json(out/'SCUC_REVIEW.json',ua)
 lp=fixed_sced(q,u['primal']);lp['options'].update(dual_feasibility_tolerance=1e-10,simplex_scale_strategy=0);save(out/'SCED_template.json.gz',lp)
 eq,e=worker.native(out,'SCED',lp,worker.V/'lp_worker.py',120.);ea=checks(eq,e,n,False)
 z=dict(status='accepted_research_pair',SCUC_objective=u['objective'],SCUC_gap=u['native_mip_gap'],SCED_objective=e['objective'],SCUC_audit=ua,SCED_audit=ea,native_calls=2,formal=False,safety_release=False,full_network_terminal_certificate=False,domain_start=s['domain_start'],domain_end=s['domain_end']);write_json(out/'ACCEPTED_PAIR.json',z);return z
if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--seed',required=True);p.add_argument('--previous-prepared',required=True);p.add_argument('--previous-actual',required=True);p.add_argument('--run',required=True);p.add_argument('--windows',type=int,default=1);p.add_argument('--execute-native',action='store_true');a=p.parse_args();assert a.windows in(1,3)
 run=Path(a.run).resolve();run.mkdir(exist_ok=False);prevp=Path(a.previous_prepared);preva=Path(a.previous_actual)
 for w in range(1,a.windows+1):
  cp=run/f'CARRY_W{w}.json';c=carry(a.seed,prevp,preva,cp);out=run/f'W{w}';out.mkdir();build(a.seed,cp,out/'prepared')
  if not a.execute_native:break
  z=solve(out/'prepared',out/'actual');print(json.dumps(dict(window=w,**z)),flush=True);prevp=out/'prepared';preva=out/'actual'
 if a.execute_native:carry(a.seed,prevp,preva,run/'FINAL_CARRY.json')
