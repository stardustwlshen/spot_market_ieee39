"""Independent midnight research fixture from existing DA history, NOT new RT executed history."""
from pathlib import Path
from datetime import datetime,timedelta
from copy import deepcopy
import sys,json,hashlib,math
B=Path(__file__).resolve().parent;ROOT=B.parent;sys.path.insert(0,str(ROOT/'guangdong_rt_recommit_program_v1_20261112/vendor/guangdong_after_reset_r23_compressed_20261112/repo/src'))
from smm.gd_october_job_codec import loads
from day_contract import digest
read=lambda p:json.loads(Path(p).read_bytes());sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
 seed=ROOT/'guangdong_rt_recommit_history_import_v1_20261112/continuation_run_v1/seed';case=loads((seed/'case.json').read_bytes());r=read(seed/'request.json');w=read(seed/'WARMSTART_SOURCE.json');assert sha(w['names'])==w['names_sha256']and sha(w['response'])==w['response_sha256'];v=dict(zip(read(w['names'])['variables'],read(w['response'])['primal']));origin=datetime.fromisoformat(r['origin']);decl=dict(schema='gd.short.day_declarations.research.v1',mode='hypothetical_published_research',description='Independent comparison: assume existing original48h DA values wholly published D-1 20:30; hash proves value provenance only, not actual publication. Repeat known50percent day-end values forD+1; not official default.',days={})
 for day in range(2):
  date=(origin+timedelta(days=day)).date().isoformat();a=dict(thermal_online={g:r['DA_online'][g][day*288:(day+1)*288]for g in case['energy_inputs']['thermal']},storage={u:dict(day_end_at=(origin+timedelta(days=day+1)).isoformat(),energy_mwh=z['terminal_energy_mwh'],limit_cycles=z['cycle_budget'][date]['limit_cycles'],transform='repeat original known terminal number; no future optimized SOC used',original_terminal_at=z['terminal_at'],original_terminal_energy_mwh=z['terminal_energy_mwh'])for u,z in case['batteries'].items()})
  decl['days'][date]=dict(basis='explicit_new_research_assumption',published_at=case['contract']['publication_at'],available_at=case['contract']['publication_at'],source=dict(case_sha256=sha(seed/'case.json'),accepted_original_DA_response_sha256=w['response_sha256'],actual_publication_verified=False),artifact=a,artifact_sha256=digest(a))
 path=B/'RESEARCH_DAY_DECLARATIONS.json';path.write_text(json.dumps(decl,indent=2));out=B/'fixtures';out.mkdir(exist_ok=False)
 for start in(285,288):
  thermal={};ages={};storage={};begin=origin+timedelta(minutes=5*start);cut=begin-timedelta(minutes=15)
  for g,z in case['energy_inputs']['thermal'].items():
   assert all(v[f'stable[{g},{t}]']>.5 for t in range(start)),'fixture mapper refuses active reset';age=z['initial_state_age_h']+start/12;ages[g]=age;thermal[g]=dict(phase='stable',source='independent originalDA trajectory replay fixture; NOT new RT rolling execution',available_at=cut.isoformat(),end_power_mw=v[f'power[{g},{start-1}]'],residual_up_ticks=max(0,math.ceil(z['min_up_h']*12-age*12-1e-8)),residual_down_ticks=0,min_up_basis='startup_begin',min_down_basis='shutdown_end',event_id=None,event_elapsed_ticks=0,remaining_curve=[])
  for u,z in case['batteries'].items():
   E=z['initial_energy_mwh'];eta=math.sqrt(z['roundtrip_efficiency']);mode=z['initial_mode'];age=z['initial_mode_age_h'];ledger=deepcopy(z['cycle_budget'])
   for t in range(start):
    ch=v[f'battery.charge[{u},{t}]'];dis=v[f'battery.discharge[{u},{t}]'];new='charge'if v[f'battery.charge_mode[{u},{t}]']>.5 else'discharge'if v[f'battery.discharge_mode[{u},{t}]']>.5 else'idle';age=age+1/12 if new==mode else 1/12;mode=new;E+=(eta*ch-dis/eta)/12;assert abs(E-v[f'battery.energy[{u},{t}]'])<=1e-6;date=(origin+timedelta(minutes=5*t)).date().isoformat();ledger[date]['used_internal_mwh']+=(eta*ch+dis/eta)/12
   hour=begin.replace(minute=0,second=0,microsecond=0).isoformat();prev=begin-timedelta(minutes=5);keys=[k for k in v if k.startswith('battery.hour_direction['+u+',')and hour in k];assert len(keys)==1;direction=(1 if v[keys[0]]>.5 else -1)if prev.hour==begin.hour else 0
   storage[u]=dict(initial_energy_mwh=E,initial_power_mw=dis-ch,initial_mode=mode,initial_mode_age_h=age,cycle_budget=ledger,initial_hour_direction=direction)
  c=dict(schema='gd.short.carry.research.v2',mode='independent_original_DA_replay_fixture_not_new_RT_history',seed_case_sha256=sha(seed/'case.json'),seed_request_sha256=sha(seed/'request.json'),origin=origin.isoformat(),cutoff=cut.isoformat(),cutoff_tick=start-3,delivery_tick=start,boundary_at=begin.isoformat(),executed_end_exclusive=start-3,issued_end_exclusive=start,thermal=thermal,thermal_age_h=ages,storage=storage,notice_values=r['state']['notice_values'],notice_requirements=[],day_declaration_path=str(path),day_declaration_sha256=sha(path),source_pins={str(p):sha(p)for p in [Path(__file__),path,seed/'case.json',seed/'request.json',Path(w['names']),Path(w['response'])]},formal=False,safety_release=False)
  (out/f'W{start//3-1}_CARRY.json').write_text(json.dumps(c,indent=2))
 print('two independent midnight fixtures; no optimizer; existing original history only')
if __name__=='__main__':main()
