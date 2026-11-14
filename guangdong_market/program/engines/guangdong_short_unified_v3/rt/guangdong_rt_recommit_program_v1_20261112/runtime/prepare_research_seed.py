"""Explicitly hypothetical issued-prefix seed from accepted DA, NOT a metered receipt."""
from pathlib import Path
from datetime import timedelta
import sys,json,re,hashlib,shutil,argparse
B=Path(__file__).resolve().parent
from runtime_config import dependency_root
ROOT=dependency_root()
sys.path.insert(0,str(ROOT/'guangdong_after_reset_r23_compressed_20261112/repo/src'))
from smm.gd_october_job_codec import loads
from contracts import need,stamp,digest,SCHEMA,validate
read=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def prepare(run,output):
 run=Path(run).resolve();out=Path(output).resolve();need(not out.exists(),'fresh independent seed required')
 art=read(run/'ARTIFACTS.json');need(art['status']=='accepted','accepted DA required')
 def resolve(ref):
  p=(run/ref['path']).resolve();need(p.is_relative_to(run)and sha(p)==ref['sha256'],'accepted artifact changed');return p
 sced=art['stages']['da_sced'];names=read(resolve(sced['full_names']))['variables'];primal=read(resolve(sced['expanded_response']))['primal'];accept=resolve(sced['acceptance']);data=dict(zip(names,primal))
 cp=resolve(art['inputs']['case']);case=loads(cp.read_bytes());origin=stamp(case['contract']['day_start']);available=case['contract']['publication_at'];agcp=resolve(art['inputs']['agc']);policyp=resolve(art['inputs']['policy'])
 fixed={}
 for name,value in data.items():
  if name.startswith('battery.hour_direction['):
   hour=name.rsplit(',',1)[1].rstrip(']').strip("'\"")
   if stamp(hour)<origin+timedelta(minutes=15):fixed[name]=value
  elif '['in name:
   token=name.rsplit(',',1)[-1]if ','in name else name.split('[',1)[1]
   token=token.rstrip(']')
   if token.isdigit()and int(token)<3:fixed[name]=value
 notices={};online={};pre={};notice_events=[]
 for g,curve in case['curves'].items():
  lead=curve['notice_h']*12;need(lead==int(lead),'exact5min notice only');lead=int(lead);notices[g]={};pre[g]={}
  def value(stem,t):
   keys=[f'{stem}[{g},{t}]',f"{stem}['{g}',{t}]"];vals=[data[k]for k in keys if k in data];need(len(vals)==1,'source variable mapping');return vals[0]
  online[g]=[value('online',t)>.5 for t in range(576)]
  for nt in range(-lead,0):
   start=nt+lead;x=value('start',start);need(abs(x-round(x))<=1e-8,'accepted integer');pre[g][str(nt)]=int(round(x))
   if x>.5:
    at=origin+timedelta(minutes=5*nt);need(at>=stamp(available),'notice before DA publication')
    notice_events.append(dict(resource=g,notice_at=at.isoformat(),connection_at=(origin+timedelta(minutes=5*start)).isoformat(),lead_h=curve['notice_h'],basis='new explicit research assumption: accepted DA-start notification issued at minimum lead; not dispatch record'))
 sources={k:dict(source='frozen accepted research DA case explicitly redeclared as known forecast/technical baseline; not future actual',sha256=sha(cp),available_at=available,uses_future_actual=False)for k in ('forecasts','technical','reserve','DA')}
 sources['forecasts']['coverage_end']=(origin+timedelta(hours=48)).isoformat();sources['AGC']=dict(source='unchanged explicit research AGC awards, not formal auction',sha256=sha(agcp),available_at=available,uses_future_actual=False)
 receipt=dict(schema='gd.research.hypothetical_initial_issue.v1',basis='new research assumed issued, not observed operations',executed_cells=0,issued_cells=3,source_DA_SCED_acceptance_sha256=sha(accept),source_expanded_response_sha256=sha(resolve(sced['expanded_response'])),first3_prefix_values=fixed,notice_events=notice_events,initial_thermal={g:{k:z[k]for k in ('initial_online','initial_state_age_h','initial_power_mw','initial_state_kind')}for g,z in case['energy_inputs']['thermal'].items()},initial_storage={g:{k:z[k]for k in ('initial_energy_mwh','initial_mode','initial_mode_age_h','initial_hour_direction','initial_power_mw')}for g,z in case['batteries'].items()})
 r=dict(schema=SCHEMA,mode='uniform_event_research',run_id='independent-rt-recommit-'+digest(receipt)[:16],case_sha256=sha(cp),policy_sha256=sha(policyp),DA_acceptance_sha256=sha(accept),origin=origin.isoformat(),cutoff=origin.isoformat(),cutoff_tick=0,delivery_tick=3,window=0,horizon_cells=576,near_cells=24,far_cells=24,tail_policy='retain_explicit_full48h_feasibility',objective_policy='near2h_energy_all_future_uniform_events_once',event_model='original_uniform_fee_curves_not_official_three_state',tracking_policy='explicit_no_deviation_research_receipts',sources=sources,state=dict(run_id='',case_sha256=sha(cp),executed_end_tick=0,issued_end_tick=3,receipt_at=origin.isoformat(),receipt_sha256=digest(receipt),basis='simulated_execution_not_metered',initial_phase_source='unchanged declared stable/off original case initial state; accepted DA first3 assumed issued, not executed',fixed_values=fixed,notice_values=notices,completed_window=-1),preorigin_notices=pre,DA_online=online,reserve_positive_MW=400,reserve_accident_MW=400,DA_shutdown_policy='no_DA_awarded_shutdown_without_explicit_safety_authorization',authorized_DA_shutdowns=[])
 r['state']['run_id']=r['run_id'];validate(r);out.mkdir(parents=True)
 for name,p in [('case',cp),('agc',agcp),('policy',policyp)]:shutil.copyfile(p,out/(name+'.json'))
 for name,z in [('request',r),('SEED_ASSUMPTIONS',receipt),('SOURCE_PINS',{str(p):sha(p)for p in (run/'ARTIFACTS.json',accept,cp,agcp,policyp,resolve(sced['full_names']),resolve(sced['expanded_response']))})]:(out/(name+'.json')).write_text(json.dumps(z,sort_keys=True,allow_nan=False))
 print(json.dumps(dict(output=str(out),fixed_prefix_variables=len(fixed),preorigin_issued_start_notices=len(notice_events),executed=0,issued=3,native_calls=0)))
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--run',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();prepare(a.run,a.output)
