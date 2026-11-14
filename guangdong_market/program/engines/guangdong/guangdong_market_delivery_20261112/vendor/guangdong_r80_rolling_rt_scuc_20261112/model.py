"""Independent declared research system; original IEEE39 untouched."""
from pathlib import Path
import sys,math,dataclasses
from datetime import datetime,timedelta
import pyomo.environ as p
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'guangdong_r68_three_state_builder_20261112'))
from events5 import Curve,StartupDTO,attach,CLASSES,need
N=54;ORIGIN=datetime.fromisoformat('2026-11-11T19:45:00+08:00');DT=1/12

def curve(n,fee,lead,down=False):
 vals=[100*i/n for i in range(n+1)];vals=vals[::-1]if down else vals
 return Curve(tuple((a+b)/2 for a,b in zip(vals,vals[1:])),tuple(vals[:-1]),tuple(vals[1:]),fee,lead,'R80 independent synthetic per5min linear curve, not real plant data')
def dto():
 return StartupDTO(basis='research',source='R80 explicit new fast gas synthetic resource',dt_minutes=5,time_basis='disconnect_to_start_curve_begin',threshold_source='synthetic thresholds only: hot<120min, warm<360min',hot_upper_ticks=24,warm_upper_ticks=72,initial_phase='off',initial_offline_ticks=12,initial_state_source='synthetic actual off for60min at19:45',price_limit_verified=False,maximum_event_fee_yuan=None,price_limit_source='formal class quote bounds absent',curves={k:curve(n,fee,lead)for k,n,fee,lead in [('hot',6,10,3),('warm',9,20,6),('cold',12,30,9)]},shutdown=curve(3,2,0,True),classification_boundary_policy='hot_lt_H_warm_lt_W')
def fixture():
 return dict(schema='gd.research.r80.explicit_fixture.v1',origin=ORIGIN.isoformat(),asof=ORIGIN.isoformat(),available_at=ORIGIN.isoformat(),forecast_source='synthetic frozen demand declared at19:45; no future actuals',load=[1000 if t<9 else(1700 if 12<=t<15 else 1650)for t in range(N)],near=dict(cells=24,resolution_minutes=5),far=dict(cells=24,resolution_minutes=5,basis='explicit research extension, not asserted unique official far-domain definition'),terminal_tail=dict(cells=3,basis='nextday boundary feasibility only'),reserve_positive_MW=400,reserve_accident_MW=400,reserve_overlap_basis='same physical headroom, separate non-simultaneous activation research requirements; never claim800 simultaneously deliverable',base_unit_physical_min_MW=100,base_unit_physical_max_MW=1000,AGC_down_MW=10,AGC=dict(G1=10,G2=10,FAST=0,ES=0),AGC_basis='explicit predeclared research awards; formal AGC auction remains separate, not implemented here',DA_on=['G1','G2'],DA_off=['FAST'],DA_storage_mode='charge',storage=dict(capacity_MWh=100,initial_MWh=50,D96_MWh=50,power_MW=50,eta=.95,dated_internal_budget={'2026-11-11':200,'2026-11-12':200},same_hour_direction=True),qualification=dict(G1=['energy','positive','accident','AGC'],G2=['energy','positive','accident','AGC'],FAST=['energy'],ES=['energy']),notification_policy='hot15/warm30/cold45 minutes synthetic, not DA17:30',min_up_cells=12,min_down_cells=6,max_starts=2,max_stops=2,initial_remaining_min_down_cells=0,disconnection='at shutdown curve end',ignition='synthetic at notice; no preconnection energy injection',network='two buses, lossless DC line1000MW; no AC acceptance',formal_ready=False,formal_missing=['actual three-class resource declarations','formal AGC clearing','AC security validation'])
def validate(f,window):
 need(window in(0,1),'only two declared windows');cutoff=ORIGIN+timedelta(minutes=15*window)
 need(datetime.fromisoformat(f['available_at'])<=cutoff,'future information')
 need(f['reserve_positive_MW']==f['reserve_accident_MW']==400,'original reserve requirements preserved')
 need(f['DA_on']==['G1','G2']and f.get('authorize_DA_stop',False)is False,'DA-on shutdown requires explicit safety authorization contract, unsupported here')
 need(not f.get('cancel_issued_notice',False),'issued notice cannot be silently revoked')
 need(f['storage']['same_hour_direction'],'hour-direction contract required')
 expected=fixture();need(set(f)==set(expected),'unsupported fixture fields')
 need(all(f[k]==v for k,v in expected.items()if k!='available_at'),'unsupported parameter change; this minimal fixture does not silently ignore inputs')
 return cutoff

def build(window=0,f=None):
 f=fixture()if f is None else f;validate(f,window);m=p.ConcreteModel();m.T=p.RangeSet(0,N-1);m.B=p.RangeSet(0,N);m.G=p.Set(initialize=['G1','G2']);b=attach(m,'fast',dto(),N,notice_eligible_ticks=range(N))
 m.fastP=p.Var(m.T,bounds=(0,100));m.fastcurve=p.Constraint(m.T,rule=lambda _,t:m.fastP[t]==100*b.stable[t]+b.transition_mean[t]);m.unit=p.ConstraintList()
 for t in m.T:
  if t%3:b.start[t].fix(0);b.stop[t].fix(0)
  m.unit.add(b.stop[t]+sum(b.start[j]for j in range(max(0,t-11),t+1))<=1)
  m.unit.add(b.start[t]+sum(b.stop[j]for j in range(max(0,t-8),max(0,t-2)))<=1)
 m.unit.add(sum(b.start[t]for t in m.T)<=2);m.unit.add(sum(b.stop[t]for t in m.T)<=2)
 m.P=p.Var(m.G,m.T,bounds=(110,990));m.Rpos=p.Var(m.G,m.T,bounds=(0,400));m.Racc=p.Var(m.G,m.T,bounds=(0,400));m.physical=p.ConstraintList()
 for t in m.T:
  for g in m.G:
   m.physical.add(m.P[g,t]+m.Rpos[g,t]<=990);m.physical.add(m.P[g,t]+m.Racc[g,t]<=990)
   prev=500 if t==0 else m.P[g,t-1];m.physical.add(m.P[g,t]-prev<=400);m.physical.add(prev-m.P[g,t]<=400)
  m.physical.add(sum(m.Rpos[g,t]for g in m.G)>=400);m.physical.add(sum(m.Racc[g,t]for g in m.G)>=400)
 m.dis=p.Var(m.T,bounds=(0,50));m.ch=p.Var(m.T,bounds=(0,50));m.alpha=p.Var(m.T,domain=p.Binary);m.beta=p.Var(m.T,domain=p.Binary);m.E=p.Var(m.B,bounds=(10,90));m.U=p.Var(m.B,bounds=(0,400));m.hour=p.Set(initialize=sorted({(ORIGIN+timedelta(minutes=5*t)).strftime('%Y-%m-%dT%H')for t in m.T}));m.direction=p.Var(m.hour,domain=p.Binary);m.storage=p.ConstraintList();m.E[0].fix(50);m.U[0].fix(0)
 for t in m.T:
  h=(ORIGIN+timedelta(minutes=5*t)).strftime('%Y-%m-%dT%H');m.storage.add(m.alpha[t]+m.beta[t]<=1);m.storage.add(m.alpha[t]<=m.direction[h]);m.storage.add(m.beta[t]<=1-m.direction[h]);m.storage.add(m.dis[t]<=50*m.alpha[t]);m.storage.add(m.ch[t]<=50*m.beta[t]);m.storage.add(m.E[t+1]==m.E[t]+DT*(.95*m.ch[t]-m.dis[t]/.95));m.storage.add(m.U[t+1]==m.U[t]+DT*(.95*m.ch[t]+m.dis[t]/.95))
 m.storage.add(m.E[51]==50);m.storage.add(m.U[51]<=200);m.storage.add(m.U[54]-m.U[51]<=200)
 m.flow=p.Var(m.T,bounds=(-1000,1000));m.balance1=p.Constraint(m.T,rule=lambda _,t:m.P['G1',t]-f['load'][t]/2==m.flow[t]);m.balance2=p.Constraint(m.T,rule=lambda _,t:m.P['G2',t]+m.fastP[t]+m.dis[t]-m.ch[t]+m.flow[t]==f['load'][t]/2)
 # Prep cells are actual initial neutral history, not fictitious optimized execution.
 for t in range(3):
  m.P['G1',t].fix(500);m.P['G2',t].fix(500);m.dis[t].fix(0);m.ch[t].fix(0);b.start[t].fix(0);b.stop[t].fix(0)
 offset=3+3*window
 # Current energy cost only; event fee once across plan, no dt multiplier.
 new_event_cost=sum(dto().curves[k].fee_yuan*b.kind[t,k] for t in range(offset,N) for k in CLASSES)+sum(dto().shutdown.fee_yuan*b.stop[t]for t in range(offset,N))
 m.total=p.Objective(expr=new_event_cost+sum(DT*(50*sum(m.P[g,t]for g in m.G)+5*m.fastP[t]+m.ch[t]+m.dis[t])for t in range(offset,offset+24)))
 m._f=f;m._window=window;return m
