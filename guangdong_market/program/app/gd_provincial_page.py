"""Provincial frontend: fresh DA per run; no historical-result substitution."""
from pathlib import Path
import json

def render(parent_run='',standalone=False):
 import streamlit as st
 import gd_provincial_prepare as prep
 import gd_live_control as ctl
 if standalone:st.set_page_config(page_title='单日市场模拟',layout='wide')
 st.title('单日市场模拟')
 # The active execution is independent of the editable next-run draft.
 tracked=st.session_state.get('prov_day')
 tracked_status=None;tracking_error=None
 if tracked:
  try:tracked_status=ctl.status(tracked['output'])
  except Exception as exc:tracking_error=str(exc)
 busy=bool(tracked and (tracking_error or not (tracked_status or {}).get('exit')))
 if busy:st.info('当前实时任务仍在运行或状态待核实。输入修改仅用于下一次计算，不会改变当前任务；结束前不能重复启动。')
 st.caption('日前 96 时段＋次日峰谷代表点；实时每 15 分钟。仅有功与直流网络。')
 configured=Path(__file__).with_name('PROVINCIAL_INPUT.json')
 cfg=json.loads(configured.read_text()) if configured.exists() else {}
 source_default=parent_run if parent_run and (Path(parent_run)/'SHORT_CONTEXT.json').is_file() else cfg.get('source_base',str(Path(__file__).resolve().parents[1]/'data/default_scenario'))
 with st.expander('场景输入与存储位置',expanded=not bool(source_default)):
  source=st.text_input('当前场景输入目录',source_default,key='prov_source')
  st.caption('使用当前场景的负荷、风光、机组参数和报价重新计算日前；不会复用旧出清结果。当前入口接收来源完整的场景目录。')
  output=st.text_input('结果保存目录',str(Path(__file__).resolve().parents[1]/'runs'),key='prov_out')
 name=st.text_input('本次运行名称','market_day',key='prov_name')
 with st.expander('高级参数'):
  delta=st.number_input('定价出力范围 δ（研究假设，%）',0.,100.,10.,1.,key='prov_delta')/100
  st.caption('HiGHS：相对间隙 1%，单次求解上限 3600 秒。次日峰谷输入沿用本日对应点；代表点不参与当日结算。')
 pins=None
 if source:
  try:pins=prep.inputs_identity(source)
  except Exception as exc:st.error(str(exc))
 load_factor=st.number_input('全网负荷倍率',0.1,3.0,1.0,0.05)
 bid_factors={};ramp_factors={};thermal_edits={};resource_edits={};additions=[];renewable_bids={}
 if pins:
  from gd_provincial_inputs import thermal_parameters
  parameters=thermal_parameters(source)
  source_identity=json.dumps(pins,sort_keys=True)
  import hashlib
  source_token=hashlib.sha256(source_identity.encode()).hexdigest()[:12]
  if st.session_state.get('unit_edits_source')!=source_identity:
   st.session_state['unit_edits_source']=source_identity;st.session_state['unit_edits']={}
  with st.expander('机组报价、技术参数与初始状态'):
   unit=st.selectbox('火电机组',sorted(parameters),key='edit_thermal_unit')
   base=parameters[unit];saved=st.session_state['unit_edits'].get(unit,{})
   current={**base,**saved.get('parameters',{})}
   st.caption(f"节点 {base['bus']}；最小出力 {base['min_mw']:g} MW；最大出力 {base['max_mw']:g} MW")
   with st.form('unit_form_'+unit+'_'+source_token):
    bid=st.number_input('分段电能量报价倍率',0.1,3.0,float(saved.get('bid_factor',1.)),0.05,key='bid_'+unit+'_'+source_token)
    ramp=st.number_input('上下爬坡能力倍率',0.1,3.0,float(saved.get('ramp_factor',1.)),0.05,key='ramp_'+unit+'_'+source_token)
    online=st.selectbox('初始状态',['开机','停机'],index=0 if current['initial_online'] else 1,key='online_'+unit+'_'+source_token)
    initial_power=st.number_input('初始出力（MW）',0.,float(base['max_mw']),float(current['initial_power_mw']),key='power_'+unit+'_'+source_token)
    age=st.number_input('初始状态已持续（小时）',0.,value=float(current['initial_state_age_h']),key='age_'+unit+'_'+source_token)
    up=st.number_input('最小开机时间（小时）',0.,value=float(current['min_up_h']),key='up_'+unit+'_'+source_token)
    down=st.number_input('最小停机时间（小时）',0.,value=float(current['min_down_h']),key='down_'+unit+'_'+source_token)
    if st.form_submit_button('保存这台机组的修改'):
     edit=dict(initial_online=int(online=='开机'),initial_power_mw=initial_power,initial_state_age_h=age,min_up_h=up,min_down_h=down)
     valid=(base['min_mw']<=initial_power<=base['max_mw']) if online=='开机' else initial_power==0
     if not valid:st.error('开机出力应在最小与最大出力间；停机出力须为0。未保存。')
     else:st.session_state['unit_edits'][unit]=dict(parameters=edit,bid_factor=bid,ramp_factor=ramp);st.success('已保存，切换机组不会丢失。重新计算后生效。')
   st.caption('先保存再切换机组。现有火电容量、删除机组、备用和调频报价尚未接入普通界面，不能在此修改。')
   if st.session_state['unit_edits']:st.dataframe([{'机组':g,'报价倍率':z['bid_factor'],'爬坡倍率':z['ramp_factor'],'初始开机':bool(z['parameters']['initial_online']),'初始出力MW':z['parameters']['initial_power_mw']}for g,z in st.session_state['unit_edits'].items()],hide_index=True)
  for g,z in st.session_state['unit_edits'].items():
   bid_factors[g]=z['bid_factor'];ramp_factors[g]=z['ramp_factor'];thermal_edits[g]=z['parameters']
  from gd_resource_editor import render_resource_editor
  resource_edits=render_resource_editor(source,source_token)
  from gd_renewable_bid_editor import render_renewable_bid_editor
  renewable_bids=render_renewable_bid_editor(source,source_token)
 forecast_labels={'无误差':'none','低误差':'low','中误差':'normal','高误差':'high'}
 forecast_levels={}
 for kind,label,col in zip(('wind','solar','load'),('风电预测误差','光伏预测误差','负荷预测误差'),st.columns(3)):
  forecast_levels[kind]=forecast_labels[col.selectbox(label,list(forecast_labels),key='forecast_'+kind)]
 st.caption('合成研究场景。实时首15分钟使用该时段实际均值（完美当前时段信息假设）；后续7时段仅用此前观测更新预测。不是严格无未来信息的实时仿真。')
 if pins:
  from gd_resource_add_editor import render_resource_add_editor
  additions=render_resource_add_editor(source,source_token)
 identity=(str(source),str(output),name,delta,json.dumps(pins,sort_keys=True),load_factor,json.dumps(bid_factors),json.dumps(ramp_factors),json.dumps(thermal_edits,sort_keys=True),json.dumps(resource_edits,sort_keys=True),json.dumps(additions,sort_keys=True),json.dumps(forecast_levels,sort_keys=True),json.dumps(renewable_bids,sort_keys=True))
 if st.session_state.get('prov_identity')!=identity:
  st.session_state['prov_identity']=identity
  for key in ('prov_ready','prov_rt_plan','prov_status'):st.session_state.pop(key,None)
 if st.button('计算日前并准备实时',disabled=pins is None or busy,key='prov_prepare'):
  try:
   st.session_state.pop('prov_ready',None)
   from gd_unified_backend import layout
   dest=Path(layout(output,name)['root'])
   if dest.exists() or Path(str(dest)+'_inputs').exists():
    import uuid
    dest=dest.with_name(dest.name+'_attempt_'+uuid.uuid4().hex[:8])
   bar=st.progress(0,text='准备输入')
   from gd_provincial_inputs import snapshot
   edited=snapshot(source,str(dest)+'_inputs',load_factor=load_factor,thermal_bid_factors=bid_factors,thermal_ramp_factors=ramp_factors,thermal_edits=thermal_edits,resource_edits=resource_edits,forecast_levels=forecast_levels,additions=additions,renewable_bids=renewable_bids)
   result=prep.prepare_fresh(edited,dest,confirmed=True,delta=delta,progress=lambda i,label:bar.progress(i/4,text=label))
   bar.progress(1.,text='日前完成');st.session_state['prov_ready']=result
   st.session_state.pop('prov_day',None)
   tracked_status=None;tracking_error=None
  except Exception as exc:
   st.error(str(exc));st.info('未复用旧结果。可以调整输入后再次点击计算，每次尝试独立保存。')
 ready=st.session_state.get('prov_ready')
 if not ready:
  if st.session_state.get('prov_day'):
   st.caption('下方为此前任务的结果，与当前修改中的输入分开。新输入需重新计算日前后再运行实时。')
   _render_tracked_day(tracked_status,tracking_error)
  return
 st.success('日前已完成，实时初态已建立。')
 network_file=Path(ready['da'])/'NETWORK_VIOLATIONS.json'
 if network_file.is_file():
  network=json.loads(network_file.read_text())
  if network.get('has_violation'):
   st.warning('日前存在线路或断面越限：最大 '+format(network['maximum_excess_mw'],'.2f')+' MW。已计入罚值，求解成功不代表无越限。')
   with st.expander('查看越限时段'):
    st.dataframe([{'线路或断面':r['resource'],'时段':r['tick']+1,'超限MW':r['excess_mw'],'范围':'当日' if r['role']=='delivery' else '次日代表点'}for r in network['violations']],hide_index=True)
 st.caption('实时参考同周期 SCED；原文参考定义有歧义，该选择为本程序约定。')
 # Existing live engine supports an explicit information-availability declaration.
 from gd_pricing_clock_panel import render as render_clock
 clock=render_clock((ready['da'],ready['run']))
 from gd_full_day_ui import start_day,render_status,progress
 day=st.session_state.get('prov_day')
 current=None
 if day:
  try:current=ctl.status(day['output'])
  except Exception as exc:st.error('进度读取失败：'+str(exc))
 can_restart=bool(current and progress(current)['failed'])
 if not day or can_restart:
  st.warning('当前为本机保存，未配置独立备份。电脑或磁盘故障可能丢失进度；这里不会自动上传云端，也不会生成外部保存回执。')
  label='从当日初态重新计算实时（保留失败记录）' if can_restart else '开始全天实时计算（96 时段）'
  if st.button(label,key='prov_rt_start',disabled=clock is None):
   try:
    st.session_state['prov_day']=start_day(ready,clock,delta)
    st.rerun()
   except Exception as exc:st.error(str(exc))
 if st.session_state.get('prov_day'):_render_tracked_day(current)


def _render_tracked_day(current=None,tracking_error=None):
 import streamlit as st
 import gd_live_control as ctl
 from gd_full_day_ui import render_status
 warning=(st.session_state.get('prov_day',{}).get('receipt') or {}).get('warning')
 if warning:st.warning(warning)
 if tracking_error:st.error('当前任务状态读取失败：'+tracking_error+'；保留任务记录，不自动重新启动。')
 st.button('刷新运行状态／检查重试',key='prov_day_refresh')
 @st.fragment(run_every=None if current and current.get('exit') else '5s')
 def live_progress():
  try:
   observed=ctl.status(st.session_state['prov_day']['output'])
   render_status(observed)
   if observed.get('exit') and not (current and current.get('exit')):st.rerun()
  except Exception as exc:st.error('结果读取失败：'+str(exc))
 live_progress()
