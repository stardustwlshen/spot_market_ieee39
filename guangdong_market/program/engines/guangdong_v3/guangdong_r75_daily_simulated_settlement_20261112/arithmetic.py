"""Explicit simulated-price boundary; reuses R36/R47 arithmetic, never a bill."""
from pathlib import Path
import sys,hashlib,json,dataclasses
from datetime import datetime,timedelta
from decimal import Decimal as D
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'guangdong_r36_energy_settlement_20261112'))
sys.path.insert(0,str(ROOT/'guangdong_r47_price_settlement_contract_20261112'))
import r36_energy as R
from r47_settlement_inputs import synthetic_participating_DA
L=R.legacy
need=L.require
PRICE_KIND='source_verified_bounded_research_price'
def encode(x):
 if isinstance(x,D):return str(x)
 if isinstance(x,datetime):return x.isoformat()
 if dataclasses.is_dataclass(x):return dataclasses.asdict(x)
 raise TypeError(type(x).__name__)
def digest(x):return hashlib.sha256(json.dumps(x,default=encode,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def research_price(x,node,start,stage):
 need(x['kind']==PRICE_KIND and x['channel']=='simulation','research price only in simulation')
 need(x['node']==node and x['start']==start.isoformat() and x['stage']==stage,'price node/time/stage mismatch')
 need(x['official_publication'] is False and x['formal_bill'] is None,'no publication promotion')
 need(len(x['source_sha256'])==64 and x['aggregation']=='quarter_clip_then_arithmetic_mean_four','source/aggregation required')
 v=D(str(x['value']));need(v.is_finite() and -50<=v<=1800,'bounded finite hourly value');return v

def settle_hour(*,resource,node,technology,direction,start,qda,qexecuted,prices,assumptions):
 need(assumptions['channel']=='simulation' and assumptions['contracts']==[] and assumptions['known_empty_contracts'] is True,'explicit known-empty research inventory required')
 need(assumptions['participation']=='participates' and assumptions['actual_meter'] is None,'explicit participation assumption, no actual meter')
 qda,qexecuted=D(str(qda)),D(str(qexecuted));need(qda.is_finite() and qexecuted.is_finite(),'finite energy')
 pd,pr=(research_price(prices[s],node,start,s)for s in ('DA','RT'));end=start+timedelta(hours=1)
 ah=digest(assumptions);ev=L.Evidence('simulation','R71 explicit simulation scenario:'+ah,ah,'1',start-timedelta(days=1),start-timedelta(days=1));asof=end+timedelta(days=1)
 def v(n,u='MWh'):return L.Value(D(str(n)),u,ev)
 if technology=='vre':
  need(direction=='discharge','VRE generation ledger')
  params={k:dict(value=z,unit=u,node=node,start=start,end=end,source='R71 source-verified simulation:'+ah)for k,z,u in [('QDA',qda,'MWh'),('Qmeter',qexecuted,'MWh'),('PDA',pd,'CNY/MWh'),('PRT',pr,'CNY/MWh')]}
  r=synthetic_participating_DA(participation='participates',node=node,start=start,end=end,quantity_kind='synthetic_metered_fixture',params=params,contracts=[],contracts_source=ah)
  parts={'LT':r['LT'],'DA':r['DA'],'RT_deviation':r['RT']};amount=r['amount'];engine='R47.synthetic_participating_DA; simulated executed quantity explicitly mapped to research fixture only'
 else:
  key=L.Key(resource,resource+':'+direction,node,direction,start,end);zero=L.Contract('__R71_KNOWN_EMPTY_LT__',node,v(0),v(0,'CNY/MWh'),v(0,'CNY/MWh'))
  # The retained Award's "final" denotes final arithmetic input in this
  # simulation channel only; no external publication exists or is asserted.
  awards={s:L.Award(s,'R71_SIMULATION_'+s,'independent_'+s,'1','NOT_AN_OFFICIAL_PUBLICATION',key,v(p,'CNY/MWh'),v(qda)if s=='DA'else None,'final')for s,p in [('DA',pd),('RT',pr)]}
  storage=technology=='independent_storage'
  args=dict(technology=technology,participation='participates',participation_source='R71 explicit research assumption:'+ah,agency_mode='not_applicable'if storage else'non_decoupled_provincial',agency_contracts=None if storage else(L.Contract('GD_LOCAL:R71_ASSUMED_EMPTY_AGENCY',node,v(0),v(0,'CNY/MWh'),v(0,'CNY/MWh')),),key=key,settlement_class='independent_storage'if storage else'generator',classification=L.Attestation(True,ev),applicability=L.Attestation(True,ev),quarter_eligibility=None,channel='simulation',asof=asof,meter=v(qexecuted),contracts=(zero,),da=awards['DA'],rt=awards['RT'])
  if not storage:args.update(base=v(0),agency_export=v(0))
  r=R.settle_energy(**args);c=r['components'];parts={'LT':c['LT_principal']+c['LT_congestion']+c['agency_principal']+c['agency_congestion'],'DA':c['DA_deviation'],'RT_deviation':c['RT_deviation']};amount=r['energy_component_subtotal'];engine='R36.settle_energy retained typed formulas'
 need(sum(parts.values(),D(0))==amount,'component conservation')
 return dict(resource=resource,node=node,technology=technology,direction=direction,start=start,end=end,QDA_MWh=qda,simulated_executed_MWh=qexecuted,deviation_MWh=qexecuted-qda,DA_hour_price=pd,RT_hour_price=pr,components_CNY=parts,energy_amount_CNY=amount,engine=engine,channel='simulation',actual_meter=None,formal_bill=None,payable=False)
