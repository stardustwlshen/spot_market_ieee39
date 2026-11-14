"""Read-only arithmetic/provenance review; no model, solver or KKT invocation."""
from pathlib import Path
from decimal import Decimal as D
from datetime import datetime,timedelta
import json,hashlib
B=Path(__file__).resolve().parent;R=B/'pure_fresh_run_v1';pins={}
def read(p):
 p=Path(p);raw=p.read_bytes();pins[str(p.relative_to(R))]=hashlib.sha256(raw).hexdigest();return json.loads(raw)
def ref(z):
 p=R/z['path'];x=read(p);assert pins[z['path']]==z['sha256'];return x
def close(a,b,t='1e-18'):assert abs(D(a)-D(b))<=D(t),(a,b)
res=read(R/'settlement/RESULT.json');trace=read(R/'settlement/PRICE_TRACE.json');art=read(R/'ARTIFACTS.json');man=read(R/'rt_price/PRICE_MANIFEST.json');src=read(R/'rt/SOURCE_CONTRACT.json');run=read(R/'rt/RUN_RESULT.json');drain=read(R/'rt/D96_DRAIN.json')
assert art['status']=='accepted' and art['old_native_results_used'] is False
fr=res['fresh_execution_receipt'];assert fr['DA_run_id']==art['run_id'] and fr['run_id']==man['run_id']==art['run_id']+'-RT'
assert fr['DA_artifacts_sha256']==pins['ARTIFACTS.json'];assert fr['source_contract_sha256']==pins['rt/SOURCE_CONTRACT.json']==man['source_contract_sha256']
physical=ref(art['stages']['da_sced']['physical_solution']);pp=art['stages']['da_sced']['physical_solution'];assert src['source_bindings'][str((R/pp['path']).resolve())]==pp['sha256']
rawda=ref(art['stages']['da_price']['raw_prices'])['raw_unclipped_LMP'];assert fr['DA_PRICE_response_sha256']==art['stages']['da_price']['response']['sha256']
assert run['report']==drain['report'] and run['report']['executed_RT_cells']==288 and run['report']['accepted_RT_windows']==96
records={};cells=[];inputs=[]
assert len(man['records'])==96 and {x['window']for x in man['records']}==set(range(96))
for item in man['records']:
 w=item['window'];rec=ref(item['receipt']);response=ref(item['response']);assert rec['run_id']==fr['run_id'] and rec['source_contract_sha256']==fr['source_contract_sha256'];assert rec['response_sha256']==item['response']['sha256'] and rec['KKT']['passed'] is True and rec['RT_dual_used'] is False;assert response['status']=='optimal';records[w]=rec
for w in range(96):
 a=read(R/f'rt/W{w}/accepted/ACCEPTANCE.json');i=read(R/f'rt/W{w}/prepared/input.json');assert pins[f'rt/W{w}/prepared/input.json']==a['local_RT_input_sha256'];assert i['projection']['run_id']==fr['run_id'] and i['contract_sha256']==fr['source_contract_sha256'];inputs.append(i)
 cs=read(R/f'rt/W{w}/accepted/issued_commands.json');assert len(cs)==3
 for k,c in enumerate(cs):assert c['global_cell_index']==3*w+k and c['accepted_event']==a['event_id'];cells.append(c['cell'])
day=datetime.fromisoformat(src['day_start']);prices={};clipped=0
assert len(trace)==39*96*2
for z in trace:
 w=int((datetime.fromisoformat(z['start'])-day).total_seconds()/900);key=(z['stage'],z['node'],w);assert key not in prices
 raw=rawda[z['node']][w] if z['stage']=='DA' else records[w]['raw_prices'][z['node']]
 assert D(str(raw))==D(z['raw_price']);bound=max(D('-50'),min(D('1800'),D(str(raw))));assert bound==D(z['bounded_candidate_price']);clipped+=bound!=D(str(raw))
 expected=fr['DA_PRICE_response_sha256'] if z['stage']=='DA' else records[w]['response_sha256'];assert z['evidence_sha256']==expected and z['run_id']==fr['run_id']+':'+z['stage'];assert z['official_market_price'] is None and z['settlement_price'] is None;prices[key]=bound
hour=lambda stage,node,h:sum(prices[stage,node,4*h+k]for k in range(4))/4
def quantity(seq,r,d):
 if r.startswith('VRE:'):return sum(D(str(c['renewable'][r[4:]]['dispatched_mw'])) for c in seq)/12
 if r.startswith('ES'):return sum(D(str(c['batteries'][r]['discharge_mw' if d=='discharge' else 'charge_mw'])) for c in seq)/12*(1 if d=='discharge' else -1)
 return sum(D(str(c['thermal'][r]['mean_mw'])) for c in seq)/12
assert len(res['rows'])==480;seen=set();components={k:D(0)for k in ('DA','LT','RT_deviation')};total=D(0)
for row in res['rows']:
 h=int((datetime.fromisoformat(row['start'])-day).total_seconds()/3600);r=row['resource'];d=row['direction'];key=(h,r,d);assert key not in seen;seen.add(key)
 actual=quantity(cells[12*h:12*h+12],r,d);planned=quantity(src['physical_cells'][12*h:12*h+12],r,d)
 close(actual,row['raw_executed_signed_MWh']);close(planned,row['raw_DA_signed_MWh']);qa=D(row['simulated_executed_MWh']);qd=D(row['QDA_MWh'])
 for raw,norm in ((actual,qa),(planned,qd)):
  if raw!=norm:assert abs(raw)<=D('1e-9') and norm==0 and ((d=='charge' and raw>0)or(d=='discharge' and raw<0))
 pd=hour('DA',row['node'],h);pr=hour('RT',row['node'],h);assert pd==D(row['DA_hour_price']) and pr==D(row['RT_hour_price']);close(qa-qd,row['deviation_MWh'])
 want={'DA':qd*pd,'LT':D(0),'RT_deviation':(qa-qd)*pr}
 for k,v in want.items():close(v,row['components_CNY'][k]);components[k]+=D(row['components_CNY'][k])
 close(sum(want.values()),row['energy_amount_CNY']);total+=D(row['energy_amount_CNY']);assert row['formal_bill'] is None and row['payable'] is False
for k,v in components.items():close(v,res['components_CNY'][k],'1e-15')
for z in res['accounting_zero_normalizations']:
 assert abs(D(z['raw_signed_MWh']))<=D('1e-9');assert D(z['accounting_signed_MWh'])==0;assert D(z['delta_MWh'])==-D(z['raw_signed_MWh'])
assert res['formal_bill'] is None and res['payable'] is False
energy={k:D(0)for k in ('load_MWh','fixed_injection_MWh','modeled_resource_signed_net_MWh')};maxbal=D(0)
for j,c in enumerate(cells):
 i=inputs[j//3];k=j%3
 load=sum(D(str(v[k]))for v in i['load_mw'].values());fixed=sum(D(str(v[k]))for v in i['fixed_injection_mw'].values())
 power=sum(D(str(z['mean_mw']))for z in c['thermal'].values())+sum(D(str(z['discharge_mw']))-D(str(z['charge_mw']))for z in c['batteries'].values())+sum(D(str(z['dispatched_mw']))for z in c['renewable'].values())
 maxbal=max(maxbal,abs(power+fixed-load));energy['load_MWh']+=load/12;energy['fixed_injection_MWh']+=fixed/12;energy['modeled_resource_signed_net_MWh']+=power/12
for k,v in energy.items():close(v,res['physical_energy_conservation'][k],'1e-18')
assert maxbal<D('1e-7');energy['independent_max_cell_balance_residual_MW']=str(maxbal)
report=dict(schema='gd.pipeline.independent.final_arithmetic.v1',status='passed',run_id=art['run_id'],actual_RT_windows=96,actual_PRICE_windows=96,executed_cells=288,settlement_rows=480,checked_quarter_prices=len(trace),clipped_quarter_prices=clipped,aggregation='clip each quarter then arithmetic mean of four',components_CNY={k:str(v)for k,v in components.items()},total_CNY=str(total),roundoff_normalizations=res['accounting_zero_normalizations'],physical_energy_conservation=res['physical_energy_conservation'],independent_energy_totals={k:str(v)for k,v in energy.items()},source_pins=pins,native_calls=0,KKT_repeated=False,formal_bill=None,payable=False,limitations=['Explicit synthetic participation and known-empty contracts; no actual meter/financial entitlement','Fixed-DA UC 309-cell-tail RT; R80 recommitment not integrated','AC research tests unsafe, not a security pass','Formal AGC qualification/mileage/payment and current price-limit verification remain missing'])
(B/'INDEPENDENT_FINAL_ARITHMETIC_REVIEW.json').write_text(json.dumps(report,indent=2));print(json.dumps({k:v for k,v in report.items()if k!='source_pins'},indent=2))
