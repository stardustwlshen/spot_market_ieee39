from pathlib import Path
import sys,json,copy
from decimal import Decimal as D
from datetime import datetime
BASE=Path(__file__).resolve().parents[2];PATCH=BASE
sys.path[:0]=[str(PATCH/'app'),str(BASE/'app'),str(BASE/'app/gd_live_market_runtime'),str(BASE/'engines/guangdong_short_unified_v6/guangdong_rt_price_program_v22_20261112/gd_price/vendor/guangdong_r75_daily_simulated_settlement_20261112')]
from gd_balance_diagnostics import classify,attach_accounts,unavailable
from gd_unit_economics import summarize
from arithmetic import settle_hour,PRICE_KIND
start=datetime.fromisoformat('2026-01-01T00:00:00+08:00')
def row(qda,qrt,pda,prt,resource='G',direction='discharge',tech='coal'):
 prices={stage:dict(kind=PRICE_KIND,channel='simulation',node='1',start=start.isoformat(),stage=stage,value=value,source_sha256='0'*64,aggregation='quarter_clip_then_arithmetic_mean_four',official_publication=False,formal_bill=None)for stage,value in [('DA',pda),('RT',prt)]}
 r=settle_hour(resource=resource,node='1',technology=tech,direction=direction,start=start,qda=qda,qexecuted=qrt,prices=prices,assumptions=dict(channel='simulation',contracts=[],known_empty_contracts=True,participation='participates',actual_meter=None))
 return {k:v.isoformat()if isinstance(v,datetime)else v for k,v in r.items()}
def case(supply,load,short,surplus,pda=100,prt=200,qda=100):
 rows=[row(qda,supply,pda,prt)];cells=[dict(tick=t,supply_MW=supply,load_MW=load,fixed_tie_MW=0,residual_MW=supply-load)for t in range(4)];x={f'balance_{k}[1,{t}]':v for t in range(4)for k,v in [('shortfall',short),('surplus',surplus)]}
 result=dict(rows=rows,physical_cells=cells,input_binding={'RT_physical_interval_minutes':15},run_id='test',schema='gd.live_host.PRICE.hour.v3',source_pins={})
 return attach_accounts(result,classify(cells,x),0)
results=[]
for supply,load,short,surplus,pda,prt,expected in [(90,100,10,0,100,200,8000),(110,100,0,10,100,-50,9500),(100,100,0,0,100,200,10000),(90,100,10,0,-50,-20,-4800)]:
 h=case(supply,load,short,surplus,pda,prt);assert h['total_energy_component_CNY']==expected and not unavailable(h)
 assert h['system_balance']['resource_net_executed_MWh']==supply and h['system_balance']['closure_including_slack_MWh']==0
 inp=dict(run_id='test',start=h['rows'][0]['start'],end=h['rows'][0]['end'],source='explicit test assumptions',costs={'G':dict(source='cost independent of bid',generation_CNY_MWh=30,executed_startup_cost_CNY=50)},services={'G':{s:dict(amount_CNY=0,source='explicit nonparticipant')for s in ['agc','reserve']}})
 econ=summarize(h,inp)['rows'][0];assert econ['simulated_spot_profit_CNY']==expected-supply*30-50
 del inp['costs']['G']['generation_CNY_MWh'];assert summarize(h,inp)['rows'][0]['simulated_spot_profit_CNY'] is None
 results.append({'supply':supply,'shortfall':short,'surplus':surplus,'energy_income':expected,'profit':str(econ['simulated_spot_profit_CNY'])})
# Storage purchase: positive price expenditure / negative price credit, no double counting.
for p,expected in [(100,-1000),(-50,500)]:
 r=row(-8,-10,p,p,'B','charge','independent_storage');assert r['energy_amount_CNY']==expected
 h=dict(run_id='test',rows=[r]);inp=dict(run_id='test',start=r['start'],end=r['end'],source='fixture',costs={'B':dict(source='explicit cost',generation_CNY_MWh=2,executed_startup_cost_CNY=0)},services={'B':{s:dict(amount_CNY=0,source='nonparticipant')for s in ['agc','reserve']}})
 e=summarize(h,inp)['rows'][0];assert e['generation_cost_CNY']==0 and e['simulated_spot_profit_CNY']==expected
# Net storage and pumped-storage charge stays signed, never allocated virtual supply.
for technology in ('independent_storage','pumped_storage'):
 mixed=case(90,100,10,0)
 charge=row(-10,-10,100,100,'S','charge','independent_storage');charge['technology']=technology
 mixed['rows']=[row(100,100,100,100),charge]
 attach_accounts(mixed,mixed['balance_diagnostics'],0)
 assert mixed['system_balance']['resource_net_executed_MWh']==90
 assert mixed['total_energy_component_CNY']==9000

# Real reader accepts new anomaly account, preserves aggregate arithmetic.
from gd_settlement_bridge import from_export
import hashlib,tempfile
with tempfile.TemporaryDirectory()as tmp:
 for i,h in enumerate([case(90,100,10,0),case(100,100,0,0)]):
  f=Path(tmp)/f'HOUR{i}.json';f.write_text(json.dumps(h,default=str));s=from_export(f,'test',hashlib.sha256(f.read_bytes()).hexdigest());assert s.hour_summary()['total']==h['total_energy_component_CNY']
# No unexplained imbalance silently accepted; no virtual allocation or bogus money.
h=case(90,100,10,0);h['rows'][0]['simulated_executed_MWh']=100
try:attach_accounts(h,h['balance_diagnostics'],0)
except ValueError as e:assert 'executed energy' in str(e)
else:raise AssertionError('virtual energy allocated')
output=dict(passed=True,tests=results,storage_signs='passed',missing_cost='profit remains None',real_settlement_reader='two consecutive hours accepted',scope='real typed arithmetic and settlement modules; not full market solver run')
print(json.dumps(output))
