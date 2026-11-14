from pathlib import Path
import streamlit as st
import gd_ui_short_price_backend as api

def show(z):
 if 'raw_prices'in z:
  c=z['context'];st.caption(f"首个季度：{c['start']} 至 {c['end']}。其他7季是预测域，不冒充发布。")
  st.dataframe([{'节点':k,'独立DC研究价 元/MWh':v}for k,v in z['raw_prices'].items()],hide_index=True)
 elif 'total_energy_component_CNY'in z:st.metric('模型列示账户电能量分项净合计（研究）（元）',z['total_energy_component_CNY']);st.caption(f"{z['executed_cells']} 格执行，{z['rows']} 条金额行；不是成本、利润或正式账单。")
 else:st.json(z)

def render(parent_run=''):
 st.subheader('短域独立PRICE与完整小时核算')
 st.warning('仅支持已安装短域CLI1研究链；旧48小时及CLI2全天链不能混用。零网损DC研究价，未取得交流安全认证，不发布正式价格/账单。')
 st.caption('δ、VRE报价/弃电罚、负备用/一次调频、资源资格、时标和公式解释均须显式合同。原case的0需求是研究输入，不是广东正式参数。')
 default=Path(__file__).resolve().parents[1]/'engines/guangdong_short_price_v1/guangdong_short_price_user_cli_v1_20261112/program.py'
 entry=st.text_input('短域PRICE程序',str(default),key='spr_entry');da=st.text_input('短域PRICE来源日前',parent_run,key='spr_da');run=st.text_input('短域运行目录（已接受窗）',key='spr_run');w=int(st.number_input('短域PRICE窗口',min_value=0,max_value=94,value=0,key='spr_window'))
 with st.expander('生成命名研究参数（也可使用自行编辑JSON）'):
  new=st.text_input('全新研究参数JSON',key='spr_newparams');name=st.text_input('研究参数名称','原case显式参数研究',key='spr_name');domain=st.radio('研究参考域',['current_sced_research','DA_reference_R61_compatible_research'],key='spr_domain')
  ok=st.checkbox('明确采用δ=.1、负荷/事故/网络/弃电罚1000/200/100/1000、VRE报价−50及原case备用声明的研究样例，不认为官方认证',key='spr_sample_confirm')
  if st.button('生成显式研究参数（0求解）',disabled=not ok,key='spr_parameters'):
   try:st.session_state['spr_sample']=api.parameters(entry,da,run,w,new,name,domain,True);st.success('参数已生成，请填写下方参数路径；可编辑但不隐藏假设。')
   except Exception as e:st.error(str(e))
 pars=st.text_input('显式完整参数JSON',key='spr_parameters_path');out=st.text_input('全新短域PRICE准备目录',key='spr_output')
 if st.button('构建短域PRICE（0求解）',key='spr_prepare'):
  try:z=api.prepare(entry,da,run,w,pars,out);st.success(f"纯构模通过：{z['columns']}变量、{z['rows']}约束。尚未求解。")
  except Exception as e:st.error(str(e))
 prepared=st.text_input('已准备PRICE目录',key='spr_prepared');confirm=st.checkbox('明确启动一次HiGHS研究PRICE（60秒原生预算），不是安全发布',key='spr_native_confirm')
 if st.button('启动短域PRICE一次',disabled=not confirm,key='spr_native'):
  try:st.json(api.launch(entry,prepared,True))
  except Exception as e:st.error(str(e))
 if st.button('刷新短域PRICE状态（不重算）',key='spr_status'):
  try:st.json(api.status(entry,prepared))
  except Exception as e:st.error(str(e))
 accepted=st.text_input('短域PRICE接受/读取JSON',key='spr_accepted')
 if st.button('严格接受已完成PRICE',key='spr_accept'):
  try:st.session_state['spr_result']=api.accept(entry,prepared,accepted)
  except Exception as e:st.error(str(e))
 if st.button('读取已验价格或研究小时（不重签）',key='spr_read'):
  try:st.session_state['spr_result']=api.read_result(entry,accepted)
  except Exception as e:st.error(str(e))
 if 'spr_result'in st.session_state:show(st.session_state['spr_result'])
 with st.expander('完整小时核算（必须12执行格与4匹配价格）'):
  hour=int(st.number_input('核算小时',min_value=0,max_value=23,value=0,key='spr_hour'));qs=st.text_area('四季度接受JSON，按顺序每行一个',key='spr_quarters');ho=st.text_input('全新短域小时目录',key='spr_hour_output')
  st.caption('初始seed价格须同日前来源，不以SCED对偶或别的场景补价。下发未执行不计入；缺输入拒绝，不填零。')
  if st.button('核算短域完整小时（0求解）',key='spr_account'):
   try:z=api.hour(entry,da,run,hour,[x.strip()for x in qs.splitlines()if x.strip()],ho);st.metric('模型列示账户电能量分项净合计（研究）（元）',z['total']);st.caption('无真实合同/计量/支付，不是正式账单。')
   except Exception as e:st.error(str(e))
 st.caption('重复点击不重算已完成native；未知或中断尝试保留并拒绝自动重试。没有安全取消按钮。')
