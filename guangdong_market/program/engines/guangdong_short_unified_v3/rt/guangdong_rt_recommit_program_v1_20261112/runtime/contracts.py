"""Explicit research rolling request/state contract, no solver or qualification inference."""
from datetime import datetime,timedelta
import hashlib,json,math
SCHEMA='gd.research.generic_rt_recommit.v1'
def need(ok,msg):
 if not ok:raise ValueError(msg)
def stamp(v):
 d=datetime.fromisoformat(v);need(d.utcoffset()==timedelta(hours=8),'aware Shanghai timestamp required');return d
def digest(z):return hashlib.sha256(json.dumps(z,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def validate(r):
 need(r['schema']==SCHEMA and r['mode']=='uniform_event_research','formal/three-state mode unsupported')
 need(r['case_sha256']and r['DA_acceptance_sha256']and r['run_id'],'explicit source identities')
 day=stamp(r['origin']);cut=stamp(r['cutoff']);n=r['horizon_cells'];k=r['cutoff_tick'];start=r['delivery_tick']
 need(n==576 and type(k)is int and k>=0 and k%3==0 and start==k+3,'absolute48h axis, quarter cutoff,15min lead')
 need(cut==day+timedelta(minutes=5*k)and start+48<=n,'near/far and nextday coverage')
 need(r['near_cells']==24 and r['far_cells']==24 and r['tail_policy']=='retain_explicit_full48h_feasibility','explicit near/far/tail policy')
 need(r['objective_policy']=='near2h_energy_all_future_uniform_events_once','supported explicit objective')
 need(r['event_model']=='original_uniform_fee_curves_not_official_three_state','uniform event declaration')
 need(r['tracking_policy']=='explicit_no_deviation_research_receipts','tracking deviations not implemented; do not silently overwrite actual state')
 for key in ('forecasts','technical','reserve','AGC','DA'):
  z=r['sources'][key];need(z['sha256']and z['source']and stamp(z['available_at'])<=cut,'missing/future '+key)
  need(z['uses_future_actual']is False,'future actual forbidden')
 need(stamp(r['sources']['forecasts']['coverage_end'])>=day+timedelta(minutes=5*n),'full explicit forecast coverage required')
 state=r['state'];need(state['run_id']==r['run_id']and state['case_sha256']==r['case_sha256'],'state identity')
 need(state['executed_end_tick']==k and state['issued_end_tick']==start,'executed/issued frontier mismatch')
 need(stamp(state['receipt_at'])==cut and state['receipt_sha256']and state['basis']=='simulated_execution_not_metered','explicit current receipt, no inferred execution')
 need(state['initial_phase_source']and state['fixed_values']is not None and state['notice_values']is not None,'complete state/notice provenance')
 need(state['completed_window']==r['window']-1,'sequential window required')
 need(r['reserve_positive_MW']==r['reserve_accident_MW']==400,'original400MW services unchanged')
 need(r['DA_shutdown_policy']=='no_DA_awarded_shutdown_without_explicit_safety_authorization','DA stop protection')
 need(not r['authorized_DA_shutdowns'],'authorized shutdown revisions not implemented in this first version')
 for name,v in state['fixed_values'].items():need(type(v)in(int,float)and math.isfinite(v),'nonfinite fixed prefix')
 return r

def fixed_sced(q,primal):
 from copy import deepcopy
 need(len(primal)==len(q['variables']),'SCUC vector size');z=deepcopy(q)
 for col,v in zip(z['variables'],primal):
  need(math.isfinite(v),'nonfinite SCUC value')
  if col['kind']!='C':need(abs(v-round(v))<=1e-6,'integer tolerance');col.update(kind='C',lb=float(round(v)),ub=float(round(v)),start=None)
 z['dual']=True
 return z
