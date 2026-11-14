import streamlit as st
import gd_price_ui_backend as api

def render(da_run='',rt_run='',parameters='',prepared=''):
 st.subheader('实时独立定价：已安装程序入口')
 st.warning('研究参数与储能定价域不是已认证官方参数；未取得调频/交流安全放行，不形成正式价格发布或账单。')
 st.caption('本版另固定采用参考带 δ=0.1、风光报价 −50 与弃电罚因子 1000 元/MWh 等研究参数，仅支持热机稳定状态。显式 ML/MR/M1 文件不等于全机制已经官方化；最终交流反馈定价域尚未接通。')
 da=st.text_input('所选日前运行目录',value=da_run,key='pr_da');rt=st.text_input('所选实时运行目录',value=rt_run,key='pr_rt');w=int(st.number_input('实时窗口编号',min_value=0,max_value=95,value=4,key='pr_window'));pars=st.text_input('显式研究罚因子 JSON',value=parameters,key='pr_parameters')
 choice=st.radio('独立定价域',['日前 SCED 参考域（研究解释，默认）','当前 SCED 参考域（独立研究对照）'],key='pr_domain');boundary='DA_reference_R61_compatible_research' if choice.startswith('日前')else'current_sced_research'
 out=st.text_input('全新价格准备目录',key='pr_output')
 if st.button('预检价格准备（不求解）',key='pr_preflight'):
  try:st.session_state['pr_contract']=api.preflight(da,rt,w,pars,out,boundary);st.success('预检通过。单次原生预算60秒；准备仅构建未来8个15分钟模型。')
  except Exception as e:st.session_state.pop('pr_contract',None);st.error(str(e))
 if st.button('构建独立价格模型（0次求解）',key='pr_prepare',disabled='pr_contract'not in st.session_state):
  try:
   c=api.preflight(da,rt,w,pars,out,boundary)
   if c!=st.session_state['pr_contract']:raise ValueError('输入已改变，请重新预检')
   st.session_state['pr_prepared_result']=api.prepare(c);st.session_state.pop('pr_contract',None);st.success('模型已构建，尚未求解。')
  except Exception as e:st.error(str(e))
 if 'pr_prepared_result'in st.session_state:
  r=st.session_state['pr_prepared_result'];st.caption(f"模型已准备：{r['columns']} 个变量、{r['rows']} 条约束。尚未实际求解。")
  with st.expander('准备证据和技术详情'):st.json(r)
 p=st.text_input('要运行或核验的已准备目录',value=prepared,key='pr_prepared')
 confirmed=st.checkbox('明确启动一次 HiGHS 研究定价（60秒预算，未知状态不重试）',key='pr_confirm')
 if st.button('启动一次实际 PRICE',key='pr_native',disabled=not confirmed):
  try:st.json(api.start_native(p,confirmed))
  except Exception as e:st.error(str(e))
 if st.button('刷新实际求解状态',key='pr_status'):
  try:st.json(api.status(p))
  except Exception as e:st.error(str(e))
 accepted=st.text_input('独立接受结果 JSON（新建或读取）',key='pr_accepted')
 if st.button('核验已完成结果（不重复求解）',key='pr_accept'):
  try:st.session_state['pr_accept_result']=api.accept_completed(p,accepted);st.success('已完成独立数学验收，未重新求解。')
  except Exception as e:st.error(str(e))
 if st.button('读取并核对已接受价格',key='pr_read'):
  try:st.session_state['pr_accept_result']=api.read_accepted(accepted)
  except Exception as e:st.error(str(e))
 if 'pr_accept_result'in st.session_state:
  z=st.session_state['pr_accept_result'];c=z['context'];st.caption(f"候选价格交付：{c['start']} 至 {c['end']}；只显示首季度，不把8期预测当8期发布。")
  st.dataframe([{'节点':n,'独立研究节点电价（元/MWh）':v}for n,v in z['raw_prices'].items()],hide_index=True)
  with st.expander('数学验收详情'):st.json(z['KKT'])
 with st.expander('生成可编辑研究参数样例（非官方参数）'):
  sample=st.text_input('全新参数样例目录',key='pr_parameters_new')
  agree=st.checkbox('明确采用ML/MR/M1为1000/200/100元/MWh的研究样例，不视作广东正式数值',key='pr_parameters_consent')
  if st.button('生成研究参数样例（0求解）',disabled=not agree,key='pr_parameters_create'):
   try:
    x=api.create_research_parameters(sample,confirmed=True);st.success('样例已生成，将下列文件路径填入显式研究罚因子栏。其适用日期仍须与所选算例一致。');st.code(x['parameters'])
   except Exception as e:st.error(str(e))
 st.subheader('已齐小时研究核算')
 h=int(st.number_input('目标小时（0至23）',min_value=0,max_value=23,value=0,key='pr_hour'));quarters=st.text_area('按时间顺序填写四季度价格 JSON，每行一个',key='pr_quarters');ho=st.text_input('全新小时核算目录',key='pr_hour_output')
 st.caption('仅核算实际票据中已执行的12段；已下发未执行、缺价或不足一小时均拒绝，不补零。假设无长协等合同，不是正式账单或利润。')
 if st.button('核算已齐小时（0次求解）',key='pr_account'):
  try:
   r=api.account_hour(rt,da,h,[x.strip()for x in quarters.splitlines()if x.strip()],ho);st.metric('模型列示账户电能量分项净合计（研究）（元）',r['total_energy_component_CNY']);st.dataframe([{'资源':x['resource'],'节点':x['node'],'方向':{'charge':'充电','discharge':'发电/放电'}.get(x['direction'],x['direction']),'日前电量（MWh）':x['QDA_MWh'],'模拟执行电量（MWh）':x['simulated_executed_MWh'],'日前小时价（元/MWh）':x['DA_hour_price'],'实时小时价（元/MWh）':x['RT_hour_price'],'能量分项（元）':x['energy_amount_CNY']}for x in r['rows']],hide_index=True)
  except Exception as e:st.error(str(e))
