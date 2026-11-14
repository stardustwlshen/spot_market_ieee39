"""Three-state event component. Offline clock resets at disconnection, not stop-begin."""
from dataclasses import dataclass,asdict
import math
import pyomo.environ as p
CLASSES=('hot','warm','cold')
def need(x,s):
 if not x:raise ValueError(s)
@dataclass(frozen=True)
class Curve:
 mean_mw:tuple
 begin_mw:tuple
 end_mw:tuple
 fee_yuan:float
 notice_lead_ticks:int
 source:str
@dataclass(frozen=True)
class StartupDTO:
 basis:str
 source:str
 dt_minutes:int
 time_basis:str
 threshold_source:str
 hot_upper_ticks:int|None
 warm_upper_ticks:int|None
 initial_phase:str
 initial_offline_ticks:int|None
 initial_state_source:str
 price_limit_verified:bool|None
 maximum_event_fee_yuan:float|None
 price_limit_source:str
 curves:dict
 shutdown:Curve
 classification_boundary_policy:str|None=None
 verified_start_fee_bounds:dict|None=None

def validate(d):
 need(d.basis in ('research','formal_inputs')and d.source,'explicit basis/source')
 need(d.dt_minutes==5,'explicit adapted 5-minute curves required')
 need(d.time_basis=='disconnect_to_start_curve_begin'and d.threshold_source,'explicit disconnect-to-grid-connection mapping required')
 need(d.classification_boundary_policy=='hot_lt_H_warm_lt_W','explicit supported threshold endpoint policy required')
 need(type(d.hot_upper_ticks)is int and type(d.warm_upper_ticks)is int and 0<d.hot_upper_ticks<d.warm_upper_ticks,'missing/invalid supplied thresholds; no10h/72h default')
 need(d.initial_phase in ('off','stable')and d.initial_state_source,'initial offline/stable state required; unfinished curve unsupported')
 need(type(d.initial_offline_ticks)is int and d.initial_offline_ticks>=0,'explicit initial offline age required')
 need(d.initial_phase=='off'or d.initial_offline_ticks==0,'stable initial offline clock must be zero')
 need(set(d.curves)==set(CLASSES),'all three curves required')
 if d.basis=='formal_inputs':
  need(d.price_limit_verified is True and d.price_limit_source and isinstance(d.verified_start_fee_bounds,dict)and set(d.verified_start_fee_bounds)==set(CLASSES),'formal per-state quote lower/upper limits not verified')
  for k,(lo,hi)in d.verified_start_fee_bounds.items():need(all(type(v)in(int,float)and math.isfinite(v)for v in(lo,hi))and 0<=lo<=d.curves[k].fee_yuan<=hi,'fee outside supplied state bounds')
 if d.maximum_event_fee_yuan is not None:need(math.isfinite(d.maximum_event_fee_yuan)and d.maximum_event_fee_yuan>=0,'finite fee limit')
 final=[]
 for kind,c in list(d.curves.items())+[('shutdown',d.shutdown)]:
  need(c.source and len(c.mean_mw)>0 and len(c.mean_mw)==len(c.begin_mw)==len(c.end_mw),'complete curve/source')
  need(type(c.notice_lead_ticks)is int and c.notice_lead_ticks>=0,'integer notification lead required')
  need(math.isfinite(c.fee_yuan)and c.fee_yuan>=0,'finite per-event fee')
  if d.maximum_event_fee_yuan is not None:need(c.fee_yuan<=d.maximum_event_fee_yuan,'event fee exceeds supplied limit')
  for i,(a,b,z)in enumerate(zip(c.begin_mw,c.mean_mw,c.end_mw)):
   need(all(type(v)in(int,float)and math.isfinite(v)and v>=0 for v in (a,b,z)),'finite nonnegative curve')
   need(min(a,z)<=b<=max(a,z),'mean outside endpoint envelope')
   need(i==0 or abs(a-c.end_mw[i-1])<1e-9,'curve discontinuity')
   need(z<=a if kind=='shutdown'else z>=a,'curve monotonicity')
  if kind=='shutdown':need(c.end_mw[-1]==0,'shutdown must end disconnected')
  else:need(c.begin_mw[0]==0,'startup begins disconnected');final.append(c.end_mw[-1])
 need(len(set(final+[d.shutdown.begin_mw[0]]))==1,'all curves must meet same stable minimum')
 return d

def classify(d,offline_ticks):
 validate(d);need(type(offline_ticks)is int and offline_ticks>=0,'elapsed offline integer clock')
 return 'hot'if offline_ticks<d.hot_upper_ticks else'warm'if offline_ticks<d.warm_upper_ticks else'cold'
def can_fold(d):
 validate(d);return len({(c.mean_mw,c.begin_mw,c.end_mw,c.fee_yuan,c.notice_lead_ticks)for c in d.curves.values()})==1

def attach(model,name,d,n,*,pre_horizon_notices=(),notice_eligible_ticks=None):
 """Returns independent Block. Bind its start/stop/stable/off to SCUC, don't duplicate phases.
 Notice availability is an explicit resource/decision schedule, not solver-created history.
 """
 validate(d);need(type(n)is int and n>0,'positive horizon');eligible=set(range(n))if notice_eligible_ticks is None else set(notice_eligible_ticks)
 need(all(type(t)is int and 0<=t<n for t in eligible),'notice eligible timeline')
 pre=set(pre_horizon_notices);need(all(k in CLASSES and type(t)is int and t<0 for k,t in pre),'pre-horizon notice identities')
 b=p.Block(concrete=True);model.add_component(name,b);b.T=p.RangeSet(0,n-1);b.B=p.RangeSet(0,n);b.K=p.Set(initialize=CLASSES,ordered=True)
 b.start=p.Var(b.T,domain=p.Binary);b.stop=p.Var(b.T,domain=p.Binary);b.kind=p.Var(b.T,b.K,domain=p.Binary);b.stable=p.Var(b.B,domain=p.Binary);b.off=p.Var(b.B,domain=p.Binary);b.offline_ticks=p.Var(b.B,domain=p.NonNegativeIntegers)
 b.notice=p.Var(b.T,b.K,domain=p.Binary);b.c=p.ConstraintList();ns={k:len(c.mean_mw)for k,c in d.curves.items()};nd=len(d.shutdown.mean_mw);M=d.initial_offline_ticks+n+max(d.warm_upper_ticks,1)+1
 def z(t,k):return b.kind[t,k]if 0<=t<n else 0
 def stop(t):return b.stop[t]if 0<=t<n else 0
 b.disconnect=p.Expression(b.B,rule=lambda _,t:stop(t-nd))
 b.start_active=p.Expression(b.B,rule=lambda _,t:sum(z(s,k)for k in CLASSES for s in range(max(0,t-ns[k]+1),min(t+1,n))))
 b.stop_active=p.Expression(b.B,rule=lambda _,t:sum(stop(s)for s in range(max(0,t-nd+1),min(t+1,n))))
 for t in range(n+1):
  prev_off=int(d.initial_phase=='off')if t==0 else b.off[t-1];prev_stable=int(d.initial_phase=='stable')if t==0 else b.stable[t-1]
  st=b.start[t]if t<n else 0;sp=stop(t);completed=sum(z(t-ns[k],k)for k in CLASSES)
  b.c.add(b.off[t]==prev_off+b.disconnect[t]-st);b.c.add(b.stable[t]==prev_stable+completed-sp)
  b.c.add(b.off[t]+b.stable[t]+b.start_active[t]+b.stop_active[t]==1)
  if t==0:b.c.add(b.offline_ticks[t]==d.initial_offline_ticks)
  else:
   # Actual disconnect at t resets clock even though stop began nd ticks earlier.
   a=b.offline_ticks[t];prev=b.offline_ticks[t-1];q=b.disconnect[t]
   b.c.add(a<=M*prev_off);b.c.add(a<=M*(1-q))
   b.c.add(a-(prev+1)<=M*(1-prev_off));b.c.add(a-(prev+1)>=-M*(1-prev_off))
  if t==n:continue
  b.c.add(st<=prev_off+b.disconnect[t]);b.c.add(sp<=prev_stable+completed);b.c.add(st+sp<=1)
  b.c.add(sum(b.kind[t,k]for k in CLASSES)==st)
  for k in CLASSES:
   v=b.kind[t,k];lead=d.curves[k].notice_lead_ticks;nt=t-lead
   if t+ns[k]>n:v.fix(0)
   b.c.add(v<=(b.notice[nt,k]if nt>=0 else int((k,nt)in pre)))
   lo=0 if k=='hot'else d.hot_upper_ticks if k=='warm'else d.warm_upper_ticks
   hi=d.hot_upper_ticks-1 if k=='hot'else d.warm_upper_ticks-1 if k=='warm'else M
   b.c.add(b.offline_ticks[t]>=lo-M*(1-v));b.c.add(b.offline_ticks[t]<=hi+M*(1-v))
   target=t+lead;b.c.add(b.notice[t,k]==(z(target,k)if t in eligible and target<n else 0))
  if t+nd>n:b.stop[t].fix(0)
 b.transition_mean=p.Expression(b.T,rule=lambda _,t:sum(d.curves[k].mean_mw[t-s]*z(s,k)for k in CLASSES for s in range(max(0,t-ns[k]+1),t+1))+sum(d.shutdown.mean_mw[t-s]*stop(s)for s in range(max(0,t-nd+1),t+1)))
 b.event_fee=p.Expression(expr=sum(d.curves[k].fee_yuan*b.kind[t,k]for t in b.T for k in CLASSES)+sum(d.shutdown.fee_yuan*b.stop[t]for t in b.T))
 b._dto=d;b._scope={'official_parameters_authenticated':False,'clock':'elapsed grid ticks since actual disconnection','event_fee_multiplied_by_dt':False,'physical_curve_energy_separate':True}
 return b
