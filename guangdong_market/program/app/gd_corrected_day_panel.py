"""Read-only panel; host provides fixed registered bundle path/hash, never a run alias."""
from pathlib import Path
import json
from gd_corrected_day_bundle import load_bundle,da_identity

def render(*,selected_da,registered_bundle=None,expected_manifest_sha256=None,historical_identity=None):
 import streamlit as st
 st.subheader('旧版v6研究日（明确版本比较，只读）')
 st.caption('研究结算模型结果，不是当前新编辑场景的重新求解；实际账单与支付不在本模拟任务范围。')
 if not registered_bundle or not expected_manifest_sha256:
  st.info('完整研究日结果尚待最终核验。缺失时间段不会补零，也不会拿已下发计划冒充已执行结果。');return
 historical=st.checkbox('仅查看固定历史原案例（不使用当前选择或编辑场景）',value=False,key='corrected_day_historical')if historical_identity else False
 if historical:st.caption('使用已注册原案例身份读取固定历史结果，不读取云端原绝对路径，不代表当前场景已求解。')
 key=(str(Path(selected_da).resolve())if selected_da else'',str(registered_bundle),expected_manifest_sha256,historical)
 if st.session_state.get('corrected_day_selected_key')!=key:
  st.session_state['corrected_day_selected_key']=key;st.session_state.pop('corrected_day_view',None)
 if st.button('核验并读取固定研究日',key='read_corrected_day'):
  try:
   if not historical and not selected_da:raise ValueError('请选择该历史研究日原始日前来源')
   z=load_bundle(registered_bundle,expected_manifest_sha256=expected_manifest_sha256,selected_DA_identity=historical_identity if historical else da_identity(selected_da));st.session_state['corrected_day_view']=z
  except Exception as e:st.session_state.pop('corrected_day_view',None);st.error(str(e))
 z=st.session_state.get('corrected_day_view')
 if not z:return
 if z['status']=='pending':st.info('完整研究日材料仍未齐，等待，不补值。');return
 st.write({'小时':z['hours'],'15分钟价格时段':z['quarter_prices'],'已执行5分钟格':z['executed_cells'],'研究结算行':z['ledger_rows'],'模型列示账户电能量分项净合计（研究）':z['total_energy_component_CNY']})
 diagnostics=json.loads((Path(registered_bundle)/'PRICE_RELAXATION_DIAGNOSTICS.json').read_text())
 kinds={}
 for row in diagnostics:
  for item in row.get('nonzero_published',[]):
   kind=item['variable'].split('[')[0];v=abs(float(item['value']))
   stat=kinds.setdefault(kind,{'quarters':set(),'maximum_MW':0.0});stat['quarters'].add(row['quarter']);stat['maximum_MW']=max(stat['maximum_MW'],v)
 if kinds:
  st.warning('规则允许定价松弛；本次包含PRICE虚拟过剩。非零不等于RT物理失衡或天然不合规；VRE资格/研究解释仍需区分正式适用性。')
  st.write([{'定价松弛类型':k,'涉及已发布季度数':len(v['quarters']),'最大MW':v['maximum_MW']}for k,v in sorted(kinds.items())])
  st.caption('仅列出本次实际非零定价松弛，不表示缺电、备用或网络松弛均非零。')
 st.caption('只读结果包完整性已核验；原始优化证据按来源索引与独立备份追溯，未在此重新求解或重新核全量原始文件。')
 import pandas as pd
 h=pd.DataFrame(z['hour_rows']);h['total_energy_component_CNY']=pd.to_numeric(h['total_energy_component_CNY']);st.line_chart(h.set_index('hour')['total_energy_component_CNY'])
 nodes=sorted({r['node']for r in z['quarter_rows']},key=int);node=st.selectbox('查看节点原始研究价格',nodes,key='corrected_day_node');q=pd.DataFrame([r for r in z['quarter_rows']if r['node']==node]);q['raw_research_CNY_per_MWh']=pd.to_numeric(q['raw_research_CNY_per_MWh']);st.line_chart(q.set_index('day_quarter')['raw_research_CNY_per_MWh'])
 st.warning('保守日前保护与研究时序假设仍明确保留。通用三态/小时调频接口仍为实验模块，不等于已在此全天自动执行或安全发布。')
 for name in ('HOURS.csv','QUARTER_PRICES.csv','LEDGER.csv','EXECUTED_CELLS.csv','SOURCE_INDEX.json'):
  st.download_button('下载 '+name,(Path(registered_bundle)/name).read_bytes(),file_name=name,key='corrected_day_'+name)
