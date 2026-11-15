"""Minimal real input-to-result economics UI, all values bound to selected ledger."""
from pathlib import Path
import json,hashlib
import gd_economics_inputs as api
DEFAULT_STORE=Path(__file__).resolve().parents[1]/'runs_economics'

def render(view,store=None):
 import streamlit as st
 if not view.get('run_id') or not view.get('settlement_hour_rows'):return
 st.subheader('机组收入与成本')
 starts=sorted({r['start']for r in view['settlement_hour_rows']})
 choice=st.selectbox('分析时段',['全部已显示小时']+starts,key='econ_period_'+view['run_id']);start=None if choice=='全部已显示小时' else choice
 directory=Path(store)if store else DEFAULT_STORE
 try:record=api.load(view,directory,start)
 except (ValueError,OSError)as exc:st.error('已保存输入未通过绑定核验：'+str(exc));return
 digest=record['ledger_digest'];resource=st.selectbox('分析机组',list(record['units']),key='econ_unit_'+digest)
 u=record['units'][resource]
 # Persisted edits/imports are a new editor revision; old widget state must not
 # overwrite newly imported values on the next Save.
 revision=hashlib.sha256(json.dumps(u,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()
 prefix='econ_'+digest+'_'+resource+'_'+revision
 # Evidence result is scoped by exact ledger AND source inventory, not only run id.
 evidence_key='econ_evidence_'+digest+'_'+str(hash(json.dumps(view.get('source_manifest',{}),sort_keys=True)))
 if evidence_key not in st.session_state:st.session_state[evidence_key]=api.proven_zero_startup(view,start)
 evidence=st.session_state[evidence_key]
 with st.expander('成本假设（可修改）',expanded=False):
  rate=st.number_input('发电成本（元/MWh）',min_value=0.0,value=float(u['generation_CNY_MWh'])if u.get('generation_CNY_MWh')is not None else None,key=prefix+'_rate')
  auto_zero=resource in evidence['resources'] and u.get('executed_startup_cost_CNY')is None
  startup=st.number_input('所选时段启停总成本（元）',min_value=0.0,value=float(u['executed_startup_cost_CNY'])if u.get('executed_startup_cost_CNY')is not None else None,key=prefix+'_startup')
  if auto_zero:st.caption('完整执行记录显示所选时段无启停：未手填时，启停成本按0计。')
  source=st.text_input('来源/依据',value=u.get('source',''),key=prefix+'_source',placeholder='例如：用户给定的机组研究成本')
 with st.expander('辅助服务收入（可留空）'):
  agc=st.number_input('所选时段调频净收入（元）',value=float(u['agc_CNY'])if u.get('agc_CNY')is not None else None,key=prefix+'_agc')
  reserve=st.number_input('所选时段备用收入（元）',value=float(u['reserve_CNY'])if u.get('reserve_CNY')is not None else None,key=prefix+'_reserve')
  st.caption('填已核算净额，不填容量或里程。没有来源则留空，不视为零。')
 if st.button('保存并计算',key=prefix+'_save'):
  try:
   record['units'][resource]=dict(generation_CNY_MWh=rate,executed_startup_cost_CNY=startup,agc_CNY=agc,reserve_CNY=reserve,source=source)
   api.save(view,record,directory,start);st.success('已保存到本运行和所选时段。')
  except (ValueError,OSError)as exc:st.error(str(exc));return
 with st.expander('批量导入成本/服务CSV'):
  st.caption('列：resource,generation_CNY_MWh,executed_startup_cost_CNY,agc_CNY,reserve_CNY,source。金额是所选时段合计；留空表示未知。')
  uploaded=st.file_uploader('选择CSV',type=['csv'],key='econ_upload_'+digest)
  if st.button('导入并保存',disabled=uploaded is None,key='econ_import_'+digest):
   try:record=api.upload_csv(view,uploaded.getvalue(),start,base=record);api.save(view,record,directory,start);st.rerun()
   except (ValueError,OSError)as exc:st.error(str(exc));return
 with st.expander('调频估算假设',expanded=False):
  mileage_ratio=st.number_input('每小时每MW中标容量的调频里程（假设）',min_value=0.0,value=10.0,key='econ_mileage_'+digest)
  st.caption('按已中标容量和出清价估算毛收入，不是实测里程；未扣考核、分摊。')
 result=api.calculate(view,record,start,evidence,agc_mileage_ratio=mileage_ratio);row=next(r for r in result['rows']if r['resource']==resource)
 names={'energy_revenue_CNY':'电能量收入','agc_revenue_CNY':'调频收入','reserve_revenue_CNY':'备用收入','generation_cost_CNY':'发电成本','executed_startup_cost_CNY':'启停成本','energy_contribution_CNY':'电能量利润（未计辅助服务）','simulated_spot_profit_CNY':'模拟现货净额'}
 st.dataframe([{'项目':label,'金额（元）':None if row[k]is None else str(row[k])}for k,label in names.items()],hide_index=True)
 if row.get('agc_estimate'):
  st.dataframe([{'项目':'调频毛收入（估算）','金额（元）':row['agc_estimate']['amount_CNY']},{'项目':'电能量利润＋调频毛收入（估算，未计备用及服务扣款）','金额（元）':str(row['estimated_energy_plus_agc_margin_CNY']) if row['estimated_energy_plus_agc_margin_CNY'] is not None else None}],hide_index=True)
 if row['missing']:st.warning('待补：'+'；'.join(row['missing']))
 else:st.success('所选机组的研究现货净额输入已齐。')
 st.caption('净额仅覆盖上述收入减发电/启停成本；不是正式账单，不含运维等未列费用。输入按运行和账目摘要保存，切源不会沿用。')
