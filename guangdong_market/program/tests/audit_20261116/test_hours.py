import sys,json,hashlib,tempfile
from pathlib import Path
from datetime import datetime,timedelta
from streamlit.testing.v1 import AppTest
ROOT=Path(__file__).resolve().parents[2];P=ROOT/'app'
sys.path[:0]=[str(ROOT/'app'),str(P)]
from gd_balance_diagnostics import STATUS
out=Path(tempfile.mkdtemp(prefix='hour_ui_'));hours=[];exports=[]
for h in range(2):
 start=f'2026-01-01T0{h}:00:00';end=f'2026-01-01T0{h+1}:00:00'
 rows=[dict(resource=g,node='1',technology='thermal',direction='generation',start=start,end=end,QDA_MWh=10,simulated_executed_MWh=11,deviation_MWh=1,DA_hour_price=100,RT_hour_price=110,energy_amount_CNY=1110)for g in ('G1','G2')]
 e=dict(schema='gd.live_host.execution_view.v3',run_id='test',origin='2026-01-01T00:00:00',rt_clock_mode='provincial_15min_execution',cells=list(range(h*4,(h+1)*4)),executed_end=(h+1)*4,excluded_issued_ticks=[],values={f'{k}[{g},{t}]':0 for k in ('start','stop')for g in ('G1','G2')for t in range(h*4,(h+1)*4)},source_pins={})
 e['values'][f'line_over[L1,{h*4}]']=2 if h==0 else 0
 ep=out/f'EXECUTION_H{h}.json';ep.write_text(json.dumps(e))
 hour=dict(run_id='test',hour=h,executed_cells=4,input_binding={'RT_physical_interval_minutes':15},source_pins={str(ep):hashlib.sha256(ep.read_bytes()).hexdigest()},rows=rows,physical_cells=[],hour_prices={})
 if h==0:hour.update(economic_status=STATUS,economic_reason='供需松弛，经济性不可用',rows=[],dispatch_rows=[{k:v for k,v in row.items() if k in ('resource','node','technology','direction','start','end','QDA_MWh','simulated_executed_MWh')}for row in rows],balance_diagnostics=[dict(tick=0,residual_MW=-2,shortfall_MW=2,surplus_MW=0)])
 hp=out/f'HOUR{h}.json';hp.write_text(json.dumps(hour));hours.append(hour);exports.append(dict(path=str(hp),sha256=hashlib.sha256(hp.read_bytes()).hexdigest()))
status=dict(completed_windows=8,quarters=list(range(8)),hours=hours,hour_exports=exports)
sp=out/'status.json';sp.write_text(json.dumps(status))
# Supply explicit same-hour costs to the real panel instead of the separate bridge.
# collect/executed_events and all rendering are production implementations, not stubs.
script=f'''import sys,json
sys.path[:0]=[{str(ROOT/'app')!r},{str(P)!r}]
import streamlit as st
import gd_settlement_panel as panel
if not hasattr(panel,'_original_render'):panel._original_render=panel.render_energy_hour
original=panel._original_render
def wrapped(result,export=None):
 inputs=None
 if result.get('rows'):
  r=result['rows'][0]
  inputs=dict(run_id=result['run_id'],start=r['start'],end=r['end'],source='synthetic test costs',costs={{g:dict(source='test',generation_CNY_MWh=20,executed_startup_cost_CNY=0) for g in ('G1','G2')}},services={{g:{{k:dict(source='test',amount_CNY=0)for k in ('agc','reserve')}}for g in ('G1','G2')}})
 return original(result,export=export,economics_inputs=inputs)
panel.render_energy_hour=wrapped
from gd_full_day_ui import render_status
st.session_state.setdefault('full_day_result_mode','分小时')
render_status(json.load(open({str(sp)!r})))
'''
at=AppTest.from_string(script).run();assert not at.exception,at.exception
assert '其中供需松弛异常 1 小时' in at.get('progress')[0].proto.text
assert '结算 2' not in at.get('progress')[0].proto.text
assert any('不可用' in e.value for e in at.error)
assert any('越限' in e.value for e in at.warning)
assert len(at.selectbox)==2
assert '偏差电量（MWh）' not in at.dataframe[-1].value.columns
assert set(at.dataframe[-1].value['方向'])=={'发电'}
at.selectbox[1].select('G2').run();assert not at.exception
assert list(at.dataframe[-1].value['机组'])==['G2']
at.selectbox[0].select(1).run();assert not at.exception,at.exception
assert not any('经济性来源检查失败' in e.value for e in at.error)
at.selectbox[1].select('G1').run();assert not at.exception
assert list(at.dataframe[-1].value['机组'])==['G1']
at.selectbox[0].select(0).run();assert not at.exception
# Every rerender must revalidate the pinned execution file.
(out/'EXECUTION_H0.json').write_text('{}');at.run();assert at.exception and '来源校验失败' in at.exception[0].value
print(json.dumps({'passed':True,'checks':['mixed-hour render','real collect/executed_events','Chinese abnormal columns','abnormal unit filter','normal unit and details filter','switch back','tampered execution rejected'],'scope':'synthetic hash-bound files, real rendering and diagnostics, normal economics supplied explicitly; no solver or bridge integration','fixture':str(out)},ensure_ascii=False))
