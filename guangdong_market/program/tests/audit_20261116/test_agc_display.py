from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
APP=ROOT/'app'
VENDOR=ROOT/'engines/guangdong_v3/guangdong_after_reset_r23_compressed_20261112/repo/src'
sys.path[:0]=[str(APP),str(VENDOR)]
import sys,types,json
from pathlib import Path
sys.path[:0]=[str(APP)]
from gd_auto_economics import attach_estimates
from gd_settlement_panel import simulation_rows
from gd_full_day_ui import economic_total

def example(actual,estimated):
 row=dict(resource='G1',energy_contribution_CNY='100',energy_revenue_CNY='100',generation_cost_CNY='0',executed_startup_cost_CNY='0',agc_revenue_CNY=actual,reserve_revenue_CNY='10')
 p=dict(agc_estimates={} if estimated is None else {'G1':dict(amount_CNY=estimated)},reserve_estimate=None,diagnostics=[],source_pins={})
 return attach_estimates(dict(rows=[row]),p)
results=[]
for actual,est,want,profit,basis in [('20','99','20','130','supplied_component'),(None,'99','99','209','research_mileage_estimate'),('0','99','0','110','supplied_component'),(None,None,None,None,'missing')]:
 e=example(actual,est);r=e['rows'][0];view=simulation_rows(e['rows'])[0]
 assert r['simulated_agc_gross_revenue_CNY']==want and view['调频收入（元）']==want
 assert r['estimated_spot_margin_before_service_allocation_CNY']==profit
 assert r['agc_revenue_basis']==basis
 results.append(dict(actual=actual,estimate=est,display=view['调频收入（元）'],profit=profit))
# Exercise actual cumulative implementation with mocked source reader only.
a=example('20','99');a.update(start='00',end='01')
b=example(None,'99');b.update(start='01',end='02')
module=types.ModuleType('gd_settlement_bridge');module.from_export=lambda p,*a:p;module.components=lambda p:{'unit_economics':{'a':a,'b':b}[p]};sys.modules['gd_settlement_bridge']=module
pairs=[({'run_id':'test'},{'path':p,'sha256':'mock'}) for p in ['a','b']]
r=economic_total(pairs)[0];assert str(r['simulated_agc_gross_revenue_CNY'])=='119';assert str(r['estimated_spot_margin_before_service_allocation_CNY'])=='339';assert r['agc_revenue_basis']=='mixed';assert str(simulation_rows([r])[0]['调频收入（元）'])=='119'
print(json.dumps({'passed':True,'cases':results,'cumulative_agc':str(r['simulated_agc_gross_revenue_CNY']),'cumulative_profit':str(r['estimated_spot_margin_before_service_allocation_CNY'])},ensure_ascii=False,indent=2))

# Explicit arithmetic producer's names are supported without filling explicit missing.
for value in (0, 12, None):
 r={'reserve_revenue_CNY':value,'simulated_spot_profit_CNY':value}
 v=simulation_rows([r])[0]
 assert v['行政备用补偿估算（元）']==value and v['模拟收益（月度分摊前，元）']==value
 r.update(simulated_reserve_gross_revenue_CNY=None,estimated_spot_margin_before_service_allocation_CNY=None)
 v=simulation_rows([r])[0]
 assert v['行政备用补偿估算（元）'] is None and v['模拟收益（月度分摊前，元）'] is None
