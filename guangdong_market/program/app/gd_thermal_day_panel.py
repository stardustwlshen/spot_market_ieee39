from pathlib import Path
from decimal import Decimal,localcontext
import json,hashlib
from gd_corrected_day_bundle import csvread,validate_tables,da_identity
B=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def load_day(bundle,expected,identity):
 p=Path(bundle);m=json.loads((p/'MANIFEST.json').read_text())
 if sha(p/'MANIFEST.json')!=expected or m['schema']!='gd.readonly.thermal_DA_anchor_day.v9'or identity!=m['DA_identity']:raise ValueError('错误DA或新版结果注册改变，不能代替当前场景')
 for n,h in m['files'].items():
  f=(p/n).resolve()
  if not f.is_relative_to(p.resolve())or sha(f)!=h:raise ValueError('新版结果文件改变')
 s=json.loads((p/'SUMMARY.json').read_text());idx=json.loads((p/'BRANCH96_INDEX.json').read_text());cmp=json.loads((p/'ACCOUNT_COMPARISON.json').read_text());h=csvread(p/'HOURS.csv');q=csvread(p/'QUARTER_PRICES.csv');ledger=csvread(p/'LEDGER.csv');cells=csvread(p/'EXECUTED_CELLS.csv')
 if s['status']!='thermal_DA_reference_research_day_accounting_verified'or s['new_physical_dispatch']or s['safety_release']or s['payable']:raise ValueError('未知或错误新版结果状态')
 # Internal validator projection only: preserve original summary/status on disk and in result.
 projection={**s,'branch_id':s['original_RT_branch'],'run_id':s['original_RT_run_id']}
 validate_tables(projection,h,q,ledger,cells,[{'quarter':i,'relaxation_diagnostic_not_embedded':True}for i in range(96)])
 if idx['status']!='complete'or len(idx['quarters'])!=96:raise ValueError('新版价格索引不完整')
 for i,r in enumerate(idx['quarters']):
  if r['day_quarter']!=i or not r['KKT']['passed']:raise ValueError('完整KKT索引缺失')
  rows=[v for v in q if int(v['day_quarter'])==i]
  if any(Decimal(v['raw_research_CNY_per_MWh'])!=Decimal(str(r['raw_prices'][v['node']]))for v in rows):raise ValueError('季度价格与新索引不一致')
 with localcontext()as c:
  c.prec=80
  for a in cmp['resources']:
   total=sum((Decimal(r['energy_amount_CNY'])for r in ledger if r['resource']==a['resource']),Decimal(0))
   if abs(total-Decimal(a['new_CNY']))>Decimal('1e-12'):raise ValueError('账户比较与新账不一致')
 return {'summary':s,'hours':h,'quarters':q,'comparison':cmp,'raw_native_replayed_here':False,'new_v9_native_day':False}
def render(selected_da):
 import streamlit as st
 st.subheader('新版：thermal DA区间 + DA前季首锚研究日')
 rp=B/'THERMAL_DAY.json'
 if not rp.exists():st.info('新版96季度/24小时最终结果尚未注册，不填零，不借旧价。');return
 r=json.loads(rp.read_text());p=(B/r['bundle']).resolve()
 historical=st.checkbox('查看已注册新版原案例，不代表当前编辑场景',value=False,key='thermal_day_historical')
 key=(selected_da,historical,r['manifest_sha256'])
 if st.session_state.get('thermal_day_key')!=key:st.session_state['thermal_day_key']=key;st.session_state.pop('thermal_day_value',None)
 if st.button('核验并读取新版完整研究日',key='thermal_day_read'):
  try:
   if not historical and not str(selected_da).strip():raise ValueError('先选择匹配的日前来源，或明确勾选固定历史原案例')
   st.session_state['thermal_day_value']=load_day(p,r['manifest_sha256'],r['DA_identity']if historical else da_identity(selected_da))
  except Exception as e:st.session_state.pop('thermal_day_value',None);st.error(str(e))
 z=st.session_state.get('thermal_day_value')
 if not z:return
 s=z['summary'];st.write({'小时':s['hours'],'季度':s['quarter_prices'],'原RT执行格':s['executed_cells'],'研究结算行':s['rows'],'模型列示账户电能量分项净合计（研究）':s['total_energy_component_CNY']})
 st.warning('DA前季首锚是统一研究解释，未获官方认证；storage/δ/VRE资格限制保留。原RT288格未重算；这不是v9新原生全天，更不是AGC/交流安全放行。规则允许松弛，非零不等于物理失衡或天然不合规。')
 st.info('系统总额仅数值级变化，但账户分配改变；不能把总额近似不变说成所有主体不变。')
 st.write({'变化超过一分的账户':z['comparison']['accounts_changed_over_cent'],'原typed状态':s['status']})
 st.dataframe(z['comparison']['resources'],hide_index=True)
 import pandas as pd
 h=pd.DataFrame(z['hours']);h['total_energy_component_CNY']=pd.to_numeric(h['total_energy_component_CNY']);st.line_chart(h.set_index('hour')['total_energy_component_CNY'])
 for n in ('HOURS.csv','QUARTER_PRICES.csv','LEDGER.csv','ACCOUNT_COMPARISON.json','SOURCE_INDEX.json'):
  st.download_button('下载新版 '+n,(p/n).read_bytes(),file_name=n,key='thermal_day_'+n)
 st.caption('只读物化结果与源索引完整性核验，不在此重复原生求解；原模型由独立备份追溯。')
