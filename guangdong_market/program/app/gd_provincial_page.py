"""Provincial frontend: fresh DA per run; no historical-result substitution."""
from pathlib import Path
import json

def _read_network_report(path):
 """Validate the report emitted by the current DA worker before showing RT controls."""
 import math
 def unique_fields(pairs):
  obj={}
  for key,value in pairs:
   if key in obj:raise ValueError('越限报告包含重复字段：'+key)
   obj[key]=value
  return obj
 report=json.loads(path.read_text(),object_pairs_hook=unique_fields)
 if not isinstance(report,dict) or report.get('schema')!='gd.network_violation_summary.v1':
  raise ValueError('越限报告格式或版本不符')
 rows=report.get('violations')
 def mw(value):
  return type(value) in (int,float) and math.isfinite(value)
 top=report.get('maximum_excess_mw')
 if (not isinstance(rows,list) or not mw(top) or top<0
     or report.get('has_violation') is not bool(rows)
     or report.get('network_secure') is not (not rows)):
  raise ValueError('越限报告缺少有效汇总，或有无越限与明细不一致')
 for r in rows:
  if (not isinstance(r,dict) or not isinstance(r.get('resource'),str) or not r['resource'].strip()
      or r.get('resource_type') not in ('line','section') or r.get('direction') not in ('upper','lower')
      or type(r.get('tick')) is not int or r['tick']<0
      or r.get('role')!=('delivery' if r['tick']<96 else 'representative')
      or not mw(r.get('excess_mw')) or r['excess_mw']<=0):
   raise ValueError('越限报告的设备、时段或越限量无效')
 delivery=sum(r['role']=='delivery' for r in rows)
 if (top!=max((r['excess_mw'] for r in rows),default=0.)
     or type(report.get('delivery_violation_count')) is not int or report['delivery_violation_count']!=delivery
     or type(report.get('representative_violation_count')) is not int or report['representative_violation_count']!=len(rows)-delivery):
  raise ValueError('越限报告的最大值或计数与明细不一致')
 return report

def render(parent_run='',standalone=False):
 import streamlit as st
 import gd_provincial_prepare as prep
 import gd_live_control as ctl
 from gd_full_day_ui import day_exit_kind
 if standalone:st.set_page_config(page_title='单日市场模拟',layout='wide')
 st.title('单日市场模拟')
 st.caption('设置场景 → 计算日前 → 运行实时 → 查看结果。修改输入后需重新计算。')
 # The active execution is independent of the editable next-run draft.
 tracked=st.session_state.get('prov_day')
 tracked_status=None;tracking_error=None
 if tracked:
  try:tracked_status=ctl.status(tracked['output'])
  except Exception as exc:tracking_error=str(exc)
 busy=bool(tracked and (tracking_error or day_exit_kind(tracked_status)=='unknown'))
 if busy:st.info('当前实时任务仍在运行或状态待核实。输入修改仅用于下一次计算，不会改变当前任务；结束前不能重复启动。')
 st.caption('日前计划 · 15 分钟实时滚动 · 有功/DC 网络')
 configured=Path(__file__).with_name('PROVINCIAL_INPUT.json')
 cfg=json.loads(configured.read_text()) if configured.exists() else {}
 source_default=parent_run if parent_run and (Path(parent_run)/'SHORT_CONTEXT.json').is_file() else cfg.get('source_base',str(Path(__file__).resolve().parents[1]/'data/default_scenario'))
 st.subheader('① 设置场景与报价')
 st.caption('可先使用默认场景；需要时再展开机组参数。')
 with st.expander('高级：场景目录与结果保存位置',expanded=not bool(source_default)):
  source=st.text_input('当前场景输入目录',source_default,key='prov_source')
  st.caption('使用当前场景的负荷、风光、机组参数和报价重新计算日前；不会复用旧出清结果。当前入口接收来源完整的场景目录。')
  output=st.text_input('结果保存目录',str(Path(__file__).resolve().parents[1]/'runs'),key='prov_out')
 name=st.text_input('本次运行名称','market_day',key='prov_name',help='用于区分本次试验。计算会建立独立结果，不覆盖此前同名尝试。')
 with st.expander('高级：定价与求解说明'):
  delta=st.number_input('定价出力范围 δ（研究假设，%）',0.,100.,10.,1.,key='prov_delta')/100
  st.caption('HiGHS：相对间隙 1%，单次求解上限 3600 秒。次日峰谷输入沿用本日对应点；代表点不参与当日结算。')
 pins=None
 if source:
  try:pins=prep.inputs_identity(source)
  except Exception as exc:st.error(str(exc))
 load_factor=st.number_input('全网负荷倍率',0.1,3.0,1.0,0.05,help='1.0 为原始负荷，1.1 为增加 10%；改变后需重新计算日前和实时。')
 bid_factors={};ramp_factors={};thermal_edits={};resource_edits={};additions=[];renewable_bids={};unsaved_units=[]
 st.session_state['optional_dirty_storage']=[];st.session_state['optional_dirty_renewable']=[]
 if pins:
  from gd_provincial_inputs import thermal_parameters
  parameters=thermal_parameters(source)
  source_identity=json.dumps(pins,sort_keys=True)
  import hashlib
  source_token=hashlib.sha256(source_identity.encode()).hexdigest()[:12]
  if st.session_state.get('unit_edits_source')!=source_identity:
   st.session_state['unit_edits_source']=source_identity;st.session_state['unit_edits']={};st.session_state['thermal_drafts']={}
  with st.expander('修改火电机组：报价、技术参数与初始状态'):
   st.caption('选择一台机组 → 修改参数 → 保存本机组。报价决定申报价格，不等于发电成本；保存仅影响下一次计算。')
   unit=st.selectbox('火电机组',sorted(parameters),key='edit_thermal_unit')
   base=parameters[unit];saved=st.session_state['unit_edits'].get(unit,{})
   from gd_thermal_drafts import accepted_edit,capture,discard,dirty_units
   accepted=accepted_edit(base,saved)
   draft=st.session_state.get('thermal_drafts',{}).get(unit,accepted)
   current={**base,**draft['parameters']}
   st.caption(f"节点 {base['bus']}")
   with st.container():
    capacity=st.number_input('最大出力（MW）',min_value=0.,value=float(current['max_mw']),key='capacity_'+unit+'_'+source_token,help='分段报价的电量宽度按可调区间同比调整，报价单价和爬坡不自动改变；利润使用的默认发电成本按容量档位更新。',on_change=capture,args=(unit,source_token))
    minimum=st.number_input('最小出力（MW）',min_value=0.,value=float(current['min_mw']),key='minimum_'+unit+'_'+source_token,help='仅支持已核验的线性研究模板：轨迹功率同比缩放、时长不变，基段与轨迹费用按原单价重算；不支持复杂外部曲线。',on_change=capture,args=(unit,source_token))
    bid=st.number_input('分段电能量报价倍率',0.1,3.0,float(draft['bid_factor']),0.05,key='bid_'+unit+'_'+source_token,on_change=capture,args=(unit,source_token))
    ramp=st.number_input('上下爬坡能力倍率',0.1,3.0,float(draft['ramp_factor']),0.05,key='ramp_'+unit+'_'+source_token,on_change=capture,args=(unit,source_token))
    online=st.selectbox('初始状态',['开机','停机'],index=0 if current['initial_online'] else 1,key='online_'+unit+'_'+source_token,on_change=capture,args=(unit,source_token))
    initial_power=st.number_input('初始出力（MW）',min_value=0.,value=float(current['initial_power_mw']),key='power_'+unit+'_'+source_token,on_change=capture,args=(unit,source_token))
    age=st.number_input('初始状态已持续（小时）',0.,value=float(current['initial_state_age_h']),key='age_'+unit+'_'+source_token,on_change=capture,args=(unit,source_token))
    up=st.number_input('最小开机时间（小时）',0.,value=float(current['min_up_h']),key='up_'+unit+'_'+source_token,on_change=capture,args=(unit,source_token))
    down=st.number_input('最小停机时间（小时）',0.,value=float(current['min_down_h']),key='down_'+unit+'_'+source_token,on_change=capture,args=(unit,source_token))
    if st.button('保存这台机组的修改',key='save_thermal_'+unit+'_'+source_token):
     edit=dict(min_mw=minimum,max_mw=capacity,initial_online=int(online=='开机'),initial_power_mw=initial_power,initial_state_age_h=age,min_up_h=up,min_down_h=down)
     from gd_provincial_inputs import validate_thermal_edits
     try:validate_thermal_edits(source,{unit:edit},ramp_factors={unit:ramp})
     except (ValueError,KeyError) as exc:st.error(str(exc)+'。未保存。')
     else:st.session_state['unit_edits'][unit]=dict(parameters=edit,bid_factor=bid,ramp_factor=ramp);st.session_state.setdefault('thermal_drafts',{}).pop(unit,None);st.success('已保存，切换机组不会丢失。重新计算后生效。')
   if unit in dirty_units(parameters,st.session_state['unit_edits'],st.session_state.get('thermal_drafts',{})):
    st.button('撤销这台机组的未保存修改',key='discard_thermal_'+unit+'_'+source_token,on_click=discard,args=(unit,source_token,accepted))
   from gd_provincial_inputs import capacity_cost_preview
   saved_capacity=st.session_state['unit_edits'].get(unit,{}).get('parameters',{}).get('max_mw',base['max_mw'])
   costs=capacity_cost_preview(base['technology'],base['max_mw'],saved_capacity)
   st.caption(f"利润默认发电成本（假设值，非报价）：{costs['before']['generation_CNY_MWh']} → {costs['after']['generation_CNY_MWh']} 元/MWh，按已保存容量分档；结果页可另改成本。旧运行手填成本不覆盖，也不自动复制到新运行。")
   st.caption('暂不支持删除机组或编辑备用、调频报价。最小出力的处理方式见字段帮助。')
   if st.session_state['unit_edits']:st.dataframe([{'机组':g,'最小出力MW':z['parameters'].get('min_mw',parameters[g]['min_mw']),'最大出力MW':z['parameters'].get('max_mw',parameters[g]['max_mw']),'报价倍率':z['bid_factor'],'爬坡倍率':z['ramp_factor'],'初始开机':bool(z['parameters']['initial_online']),'初始出力MW':z['parameters']['initial_power_mw']}for g,z in st.session_state['unit_edits'].items()],hide_index=True)
  unsaved_units=dirty_units(parameters,st.session_state['unit_edits'],st.session_state.get('thermal_drafts',{}))
  for g,z in st.session_state['unit_edits'].items():
   bid_factors[g]=z['bid_factor'];ramp_factors[g]=z['ramp_factor'];thermal_edits[g]=z['parameters']
  from gd_resource_editor import render_resource_editor
  resource_edits=render_resource_editor(source,source_token)
  from gd_renewable_bid_editor import render_renewable_bid_editor
  renewable_bids=render_renewable_bid_editor(source,source_token)
  unsaved_units+=st.session_state.get('optional_dirty_storage',[])+st.session_state.get('optional_dirty_renewable',[])
 forecast_labels={'无误差':'none','低误差':'low','中误差':'normal','高误差':'high'}
 forecast_levels={}
 for kind,label,col in zip(('wind','solar','load'),('风电预测误差','光伏预测误差','负荷预测误差'),st.columns(3)):
  forecast_levels[kind]=forecast_labels[col.selectbox(label,list(forecast_labels),key='forecast_'+kind)]
 with st.expander('预测误差与模型说明'):
  st.caption('使用合成场景。实时首 15 分钟已知该时段实际均值；后续 7 时段仅用此前观测更新预测，因此不等同严格在线预测。仅计算有功/DC 网络，不检验交流与无功。完整方法见 QUICKSTART.md。')
 if pins:
  from gd_resource_add_editor import render_resource_add_editor
  additions=render_resource_add_editor(source,source_token)
 identity=(str(source),str(output),name,delta,json.dumps(pins,sort_keys=True),load_factor,json.dumps(bid_factors),json.dumps(ramp_factors),json.dumps(thermal_edits,sort_keys=True),json.dumps(resource_edits,sort_keys=True),json.dumps(additions,sort_keys=True),json.dumps(forecast_levels,sort_keys=True),json.dumps(renewable_bids,sort_keys=True))
 if st.session_state.get('prov_identity')!=identity:
  st.session_state['prov_identity']=identity
  for key in ('prov_ready','prov_rt_plan','prov_status'):st.session_state.pop(key,None)
 st.subheader('② 计算日前计划')
 st.caption('使用已保存输入计算日前计划与价格，不会启动全天实时计算。')
 if pins is None:st.info('尚不能计算：请先修正上方场景输入错误。')
 if unsaved_units:st.warning('参数修改尚未保存：'+ '、'.join(unsaved_units)+'。请保存或撤销后再计算。')
 if st.button('计算日前并准备实时',disabled=pins is None or busy or bool(unsaved_units),key='prov_prepare',type='primary') and pins is not None and not busy and not unsaved_units:
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
 st.subheader('③ 运行全天实时计算')
 st.caption('核对下方研究假设后启动；全天共 96 个时段，进度在下方显示。')
 network_file=Path(ready['da'])/'NETWORK_VIOLATIONS.json'
 network=None
 try:network=_read_network_report(network_file)
 except (OSError,UnicodeError,ValueError,OverflowError) as exc:
  st.error(f'无法核验日前线路或断面越限报告：{network_file}。原因：{exc}。已保留日前结果及已有任务，暂不可启动实时。请检查文件读取权限或重新计算日前生成新结果，不要手动修改报告。')
 if network is not None and network['has_violation']:
  st.warning('日前存在线路或断面越限：最大 '+format(network['maximum_excess_mw'],'.2f')+' MW。已计入罚值，求解成功不代表无越限。')
  with st.expander('查看越限时段'):
   st.dataframe([{'线路或断面':r['resource'],'时段':r['tick']+1,'超限MW':r['excess_mw'],'范围':'当日' if r['role']=='delivery' else '次日代表点'}for r in network['violations']],hide_index=True)
 with st.expander('计算方法'):
  st.caption('实时参考同周期 SCED。相关规则存在不同解释，本程序采用此约定；详细口径见 QUICKSTART.md。')
 # Existing live engine supports an explicit information-availability declaration.
 from gd_pricing_clock_panel import render as render_clock
 clock=render_clock((ready['da'],ready['run'])) if network is not None else None
 from gd_full_day_ui import start_day
 day=st.session_state.get('prov_day')
 current=None
 if day:
  try:current=ctl.status(day['output'])
  except Exception as exc:st.error('进度读取失败：'+str(exc))
 can_restart=bool(day and current and day_exit_kind(current)=='launch_failed')
 if not day or can_restart:
  st.caption('结果保存在本机，尚未配置自动备份。')
  label='从当日初态重新计算实时（保留失败记录）' if can_restart else '开始全天实时计算（96 时段）'
  if st.button(label,key='prov_rt_start',disabled=network is None or clock is None or busy or bool(unsaved_units)) and network is not None and clock is not None and not busy and not unsaved_units:
   try:
    st.session_state['prov_day']=start_day(ready,clock,delta)
    st.rerun()
   except Exception as exc:st.error(str(exc))
 if st.session_state.get('prov_day'):_render_tracked_day(current)


def _render_tracked_day(current=None,tracking_error=None):
 import streamlit as st
 import gd_live_control as ctl
 from gd_full_day_ui import day_exit_kind,render_status
 warning=(st.session_state.get('prov_day',{}).get('receipt') or {}).get('warning')
 st.subheader('④ 查看进度与结果')
 st.caption('显示本次已启动任务；未完成时段留空。收益是研究估算，非完整净利润。')
 if warning:st.warning(warning)
 if tracking_error:st.error('当前任务状态读取失败：'+tracking_error+'；保留任务记录，不自动重新启动。')
 st.button('刷新运行状态／检查重试',key='prov_day_refresh')
 @st.fragment(run_every=None if day_exit_kind(current)!='unknown' else '5s')
 def live_progress():
  try:
   observed=ctl.status(st.session_state['prov_day']['output'])
   render_status(observed)
   if day_exit_kind(observed)!='unknown' and day_exit_kind(current)=='unknown':st.rerun()
  except Exception as exc:st.error('结果读取失败：'+str(exc))
 live_progress()
