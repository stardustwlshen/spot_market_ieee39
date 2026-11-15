"""Explicit read-only partial-result viewer. Not a live-host or formal-bill importer."""
from pathlib import Path
import json,hashlib
B=Path(__file__).resolve().parent
FILES=('PRICE_COMPATIBILITY.json','QUARTER_PRICES.json','PHYSICAL_CELLS.json','HOURS.json','ACCOUNT_ROWS.json')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_bytes())
def load(bundle,index_sha,*,recheck_external=False):
 root=Path(bundle).resolve();p=root/'INDEX.json'
 if sha(p)!=index_sha:raise ValueError('registered partial INDEX changed')
 index=read(p);files=index['files'];tables={}
 for name in FILES:
  h=files[name];h=h['sha256']if isinstance(h,dict)else h
  if sha(root/name)!=h:raise ValueError('partial result source changed: '+name)
  tables[name]=read(root/name)
 # External raw paths are traceability references, not portable snapshot dependencies.
 # Verifying packaged content does not repeat mathematical acceptance.
 refs=index.get('source_pins',{});missing=[];changed=[];checked=0
 if recheck_external:
  for f,h in refs.items():
   if not Path(f).is_file():missing.append(f)
   elif sha(f)!=h:changed.append(f)
   else:checked+=1
 provenance=dict(snapshot_content_hashes_verified=True,mathematics_revalidated_here=False,external_reference_count=len(refs),external_hashes_rechecked=recheck_external,external_hashes_matching=checked,missing_external_files=missing,changed_external_files=changed,live_restore_allowed=False,new_acceptance=False)
 index={**index,'display_integrity':provenance}
 if index.get('safety_release')is not False or index.get('formal_bill')is not None:raise ValueError('partial view cannot grant release or bill')
 return index,tables

def rows(table):
 if isinstance(table,list):return table
 if isinstance(table,dict)and isinstance(table.get('rows'),list):return table['rows']
 return None

def render(selected_da):
 from gd_final_results_panel import render as render_final
 render_final(selected_da)
 import streamlit as st
 with st.expander('固定研究快照：全天参考对照 / 新调频首小时（只读）',expanded=False):
  st.warning('固定示例快照，不随当前场景/报价编辑自动重算。只读研究结果，不恢复 live host；两分支参考假设不同，不能默默把修订价填回原缺价分支，不是正式账单/利润或安全放行。')
  choice=st.selectbox('明确选择结果来源',['原参考缺价分支（95价，23完整小时）','修订参考研究分支（96价，24小时）','干净post-SCUC首小时（4价，H1研究调频）','有限候选H1独立首小时（12次真实调用）'],key='readonly_reference_branch')
  finite=choice.startswith('有限');revised=choice.startswith('修订');clean=choice.startswith('干净')
  registration=B/('FINITE_H1_REGISTRATION.json'if finite else 'CLEAN_HOUR_REGISTRATION.json'if clean else 'REVISION_DAY_REGISTRATION.json'if revised else'PARTIAL_DAY_REGISTRATION.json')
  if not registration.exists():st.info('同来源PRICE兼容与部分核算正在独立验证，尚未注册可展示bundle。没有注册前不搬用旧价。');return
  try:
   # This registration must be pinned alongside installed code, never chosen by user upload.
   from gd_live_control import sources
   sources();reg=read(registration);relative=Path(reg['bundle'])
   if relative.is_absolute():raise ValueError('snapshot bundle registration must be app-relative')
   root=(B/relative).resolve()
   if not root.is_relative_to(B.parent.resolve()):raise ValueError('snapshot bundle escapes application')
   index,tables=load(root,reg['index_sha256'])
   source_da=index.get('selected_DA')
   if source_da and str(Path(selected_da).resolve())!=str(Path(source_da).resolve()):
    if not st.checkbox('明确查看此独立历史来源，不代表当前所选DA',key='partial_history_select'):st.info('当前DA不同，结果未自动套用。');return
   st.info('已核对打包快照内容完整性。数学验收曾在留存原始来源上完成，本次未重验数学；旧工作区绝对路径只作可追溯引用。缺少原始文件不等于此处重新验收，也不恢复live身份。')
   st.caption('固定源案例hash '+reg.get('source_case_sha256','未提供')[:16]+'；DA artifact hash '+reg.get('source_DA_artifacts_sha256','未提供')[:16])
   st.caption('固定源DA：'+str(source_da)+'；INDEX哈希 '+reg['index_sha256'][:16]+'；源run '+str(index.get('run_id',''))+'。真正新运行只读取worker同run输出，非此固定示例。')
   if finite:
    if index.get('source_kind')!='finite_H1_first_hour_fixed_example':raise ValueError('wrong finite H1 snapshot')
    st.metric('模型列示账户首小时电能量分项净合计（研究，元）',str(index['hour0_energy_component_CNY']))
    st.info('独立真实12次：单到期H1有限choice→实际50分配→固定SCED→W3消费，12执行/15下发、4价54行。仅原c10e库存，非全天通用；此固定示例不因编辑重算。')
    with st.expander('H1完整原始耐久研究回执（只读，不授执行权限）'):st.json(tables['PRICE_COMPATIBILITY.json']['H1_research_receipt'])
   if clean:
    if index.get('source_kind')!='clean_post_scuc_first_hour_fixed_example':raise ValueError('wrong fixed first-hour source')
    st.metric('模型列示账户首小时电能量分项净合计（研究，元）',str(index['hour0_energy_component_CNY']))
    st.info('新独立12次求解已完成：12格执行、15格已下发、4季度价、54行。H1为内部已接受研究调频回执，不是规划预测或实际市场发布；该历史完整回执系事后按原hash核对重导，不冒称当初独立落盘。')
    with st.expander('H1完整研究声明/分配/价格/来源（只读）'):
     st.json(tables['PRICE_COMPATIBILITY.json']['H1_research_receipt'])
   if revised:
    if index.get('source_kind')!='counterfactual_D1_reference_revision_research_branch'or index.get('original_missing_branch_preserved')is not True:raise ValueError('not registered independent reference revision')
    st.info('独立D+1参考修订假设：只q95采用新参考来源；已有记录包括1次参考SCED+1次PRICE，不是本页面新求解。原缺价分支保留。')
    st.metric('模型列示账户电能量分项净合计（研究，元）',str(index['full_day_energy_amount_CNY']))
   else:
    if index.get('full_day_energy_amount_CNY')is not None:raise ValueError('original missing-price branch cannot have whole-day amount')
   st.json({k:v for k,v in index.items()if k not in ('files','source_pins')})
   physical,prices,hours,accounts,proof=st.tabs(['5分钟执行/功率/SOC/AGC','15分钟价格与缺口','完整小时/末小时缺项','已完整小时账户分项','来源兼容证明'])
   for tab,name in ((physical,'PHYSICAL_CELLS.json'),(prices,'QUARTER_PRICES.json'),(hours,'HOURS.json'),(accounts,'ACCOUNT_ROWS.json'),(proof,'PRICE_COMPATIBILITY.json')):
    with tab:
     data=tables[name];rs=rows(data)
     if rs is not None:
      if name=='PHYSICAL_CELLS.json':render_physical_charts(rs)
      if name=='QUARTER_PRICES.json':render_price_chart(rs)
      if name=='PHYSICAL_CELLS.json':st.dataframe([dict(r,**{'时段末SOC时刻':_soc_end(r)}) if isinstance(r.get('storage_SOC_MWh'),dict) else r for r in rs],hide_index=True)
      else:st.dataframe(rs,hide_index=True)
     else:st.json(data)
   with accounts:st.caption('仅列本选定来源的已完成小时账户电能量分项；分项净合计不是市场总额、利润、正式净账单。原分支缺价小时仍不补造。')
  except Exception as exc:st.error('部分结果未通过来源读取：'+str(exc))

def _soc_end(cell):
 # This registered historical panel has fixed five-minute cells.  Old snapshots
 # omit end; derive only the timestamp, never the stored end-energy value.
 if cell.get('end'):return cell['end']
 if cell.get('start'):
  from datetime import datetime,timedelta
  return (datetime.fromisoformat(cell['start'])+timedelta(minutes=5)).isoformat()
 return None

def render_physical_charts(cells):
 import streamlit as st
 import pandas as pd
 import plotly.express as px
 for key,title,unit in [('thermal_MW','热机执行功率','MW'),('storage_charge_MW','储能充电功率','MW'),('storage_discharge_MW','储能放电功率','MW'),('storage_SOC_MWh','储能时段末SOC','MWh'),('VRE_MW','风光执行功率','MW'),('AGC_up_MW','调频向上预留','MW'),('AGC_down_MW','调频向下预留','MW')]:
  plotted=[]
  for c in cells:
   value=c.get(key)
   if isinstance(value,dict):
    for resource,amount in value.items():
     at=_soc_end(c) if key=='storage_SOC_MWh' else c.get('start',c.get('tick'))
     if isinstance(amount,(int,float,str))and amount is not None and at is not None:plotted.append({'时刻':at,'资源':resource,unit:float(amount)})
  if plotted:st.plotly_chart(px.line(pd.DataFrame(plotted),x='时刻',y=unit,color='资源',title=title),width='stretch')
  elif key in ('storage_SOC_MWh','AGC_up_MW','AGC_down_MW'):st.caption(title+'：快照未提供该字段，保持未知，不由功率或资格推造。')

def render_price_chart(quarters):
 import streamlit as st
 import pandas as pd
 import plotly.express as px
 points=[];missing=[]
 for q in quarters:
  prices=q.get('raw_prices')
  if isinstance(prices,dict):
   points.extend({'季度':q['day_quarter'],'节点':str(node),'研究价元/MWh':value}for node,value in prices.items())
  else:missing.append(q)
 if points:
  frame=pd.DataFrame(points).pivot(index='节点',columns='季度',values='研究价元/MWh').reindex(columns=range(96))
  st.plotly_chart(px.imshow(frame,aspect='auto',labels={'x':'15分钟季度','y':'节点','color':'研究价元/MWh'},title='已核价格；空白保持缺项'),width='stretch')
 if missing:st.warning('存在缺价，不补0，不给全天净总额。');st.json(missing)
