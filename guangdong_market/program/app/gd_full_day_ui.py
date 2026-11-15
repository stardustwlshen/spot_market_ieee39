"""Presentation/control helpers for a single 96-quarter simulation; no model edits."""
from pathlib import Path
import json,uuid

def start_day(ready,clock,delta,control=None):
 if not clock:raise ValueError('请先勾选输入可用时间研究假设，再开始实时计算')
 if control is None:import gd_live_control as control
 name='full_day_'+uuid.uuid4().hex[:12]
 p=control.plan(ready['da'],ready['run'],name,event_simulation=True,event_price_profile=True,windows=96,dispatch_mode='provincial8',pricing_clock_path=clock,pricing_delta=delta)
 receipt=control.start(p,confirmed=True)
 return dict(output=p['output'],receipt=receipt,windows=96)

def progress(status):
 done=int(status.get('completed_windows',0));priced=len(status.get('quarters',[]));hours=len(status.get('hours',[]));end=status.get('exit')or{}
 complete=end.get('status')=='complete_research' and done==96 and priced==96 and hours==24
 failed=bool(end) and not complete
 return dict(executed=done,priced=priced,hours=hours,complete=complete,failed=failed,fraction=min(1.,max(0.,done/96)),message=end.get('error','') if failed else '',end_time=f'{done//4:02d}:{(done%4)*15:02d}')

def hour_pairs(status):
 exports={Path(e['path']).stem:e for e in status.get('hour_exports',[])}
 rows=[]
 for i,h in enumerate(status.get('hours',[])):
  key='HOUR'+str(i)
  if key not in exports:raise ValueError('缺少小时结果的来源绑定：'+key)
  if json.loads(Path(exports[key]['path']).read_text())!=h:raise ValueError('小时结果与来源文件不一致：'+key)
  rows.append((h,exports[key]))
 return rows

def economic_total(pairs):
 from gd_settlement_bridge import components,from_export
 from decimal import Decimal
 totals={};fields=('energy_revenue_CNY','generation_cost_CNY','executed_startup_cost_CNY','simulated_reserve_gross_revenue_CNY','estimated_spot_margin_before_service_allocation_CNY')
 seen=set()
 for h,e in pairs:
  econ=components(from_export(e['path'],h['run_id'],e['sha256']))['unit_economics']
  period=(econ['start'],econ['end'])
  if period in seen:raise ValueError('重复小时不能累计收益')
  seen.add(period)
  for row in econ['rows']:
   r=totals.setdefault(row['resource'],dict(resource=row['resource'],**{f:Decimal(0) for f in fields},agc_estimate={'amount_CNY':Decimal(0)}))
   for f in fields:
    v=row.get(f);r[f]=None if v is None or r[f] is None else r[f]+Decimal(str(v))
   v=(row.get('agc_estimate')or{}).get('amount_CNY');old=r['agc_estimate']['amount_CNY'];r['agc_estimate']['amount_CNY']=None if v is None or old is None else old+Decimal(str(v))
 return list(totals.values())

def render_status(status):
 import streamlit as st
 p=progress(status)
 st.progress(p['fraction'],text=f"实时调度 {p['executed']}/96 时段；定价 {p['priced']}/96；结算 {p['hours']}/24 小时")
 if p['complete']:st.success('单日求解流程完成：96 时段调度和定价、24 小时结算齐全；是否存在越限及目标偏差请看下方异常。')
 elif p['failed']:st.error('实时计算停止：'+(p['message'] or '完成记录不齐全。请查看诊断。'))
 else:st.caption('已执行至 '+p['end_time']+'；后续时段尚未完成。')
 st.caption('结果和运行记录保存于本机。未配置独立存储，不代表已完成云端或其他电脑备份。失败重算从当日初态开始，不会把旧尝试的电量、费用再次计入。')
 pairs=hour_pairs(status)
 if pairs:
  from gd_execution_alerts import render as render_alerts
  render_alerts(pairs)
  mode=st.radio('查看结果',['累计汇总','分小时'],horizontal=True,key='full_day_result_mode')
  if mode=='分小时':
   i=st.selectbox('结果小时',range(len(pairs)),format_func=lambda n:f'{n:02d}:00–{n+1:02d}:00',key='full_day_hour')
   from gd_settlement_panel import render_energy_hour
   render_energy_hour(pairs[i][0],export=pairs[i][1])
  else:
   st.caption('累计范围：已完成的 '+str(len(pairs))+' 个小时，不含未完成时段。')
   from gd_settlement_panel import simulation_rows
   rows=economic_total(pairs);g=st.selectbox('查看机组',['全部']+[r['resource']for r in rows],key='full_day_sum_unit')
   st.dataframe(simulation_rows([r for r in rows if g=='全部' or r['resource']==g]),hide_index=True)
   st.caption('模拟收益为月度辅助服务分摊前估算，不是正式净利润。')
 with st.expander('运行诊断'):st.json(status)
 return p
