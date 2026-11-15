import gd_live_control as ctl
from gd_price_quality import render as render_price_quality
def render(da,run):
 import streamlit as st
 from gd_post_scuc_panel import render as render_post_scope
 render_post_scope(run)
 with st.expander('同一新分支：实时 + 小时调频 + 独立定价/核算（默认4窗，可选96窗）',expanded=True):
  st.warning('研究入口：固定五机各10MW资格、400/400MW保持；通用非零IEEE39启停尚未完整实测。默认4窗；96窗尚未完整原生验证，非全天合规产品。')
  st.caption('同一live session：seedPRICE → W0/W1/W2各SCUC+SCED+PRICE → W3交接 → H0。4窗最多12次、96窗最多288次HiGHS，SCED采用计划中明确的IPM+crossover，无自动回退。缺价不补0，额外已下发3格不计入小时；不混下方旧base结果。')
  st.warning('默认普通模式仅保存本机结果，不满足独立外存要求。每15分钟外存模式需已配置且在线的授权协调器；本机目录或上传成功不等于回读确认。无人值守独立外存尚未部署。')
  st.caption('有功/DC线路约束市场仿真；未评估交流/无功。新运行不调用AC，也不以历史AC结果阻断市场链。')
  windows=st.selectbox('运行范围（不会自动扩大预算）',[4,96],format_func=lambda v:'首小时4窗/12调用'if v==4 else'研究全天96窗/288调用，未完整原生验证',key='live_windows')
  post_mode=st.checkbox('明确采用五个合资格独立厂站、总量50的饱和规划post-SCUC研究模式（仅4窗；规划不等于调频发布）',value=False,disabled=windows!=4,key='live_post_planning')
  if windows!=4:post_mode=False
  if post_mode:st.warning('H1在当前SCUC通过后才做真实内部调频分配并重建固定整数SCED；H2/H3是规划，partialH4不冒完整小时证明。不是一般n资源/全天模式，不能与289参考修订同时选择。最多仍12调用。')
  post_day=st.checkbox('独立选择全天post-SCUC研究调频及接受后D1参考（96窗/最多289；尚未全天原生验证）',value=False,disabled=windows!=96,key='live_post_day')
  if windows!=96:post_day=False
  if post_day:st.warning('五机饱和规划不是已发布调频。逐小时SCUC后内部分配再SCED；H24必须W94完整接受于23:45可用后才做独立参考/q95，不提前produce24；H25不续。与原289和4窗post互斥。')
  selection=None;nextday_explicit=False
  revision=st.checkbox('独立选择同运行D+1研究参考修订（仅96窗；最多289调用，非原DA98/正式发布）',value=False,disabled=windows!=96,key='live_reference_revision')
  if windows!=96:revision=False
  if revision:st.warning('仅承诺尝试：W93接受且H24可信可用后、W94前，额外1次3600秒求解时限参考SCED；q95 PRICE已包含在96价预算。原DA整数与t287预测初态，D0/储能/δ/400/50不变，H25不续50。失败即停，不自动回退原参考。')
  if st.session_state.get('day_source_identity')!=(da,run):
   st.session_state['day_source_identity']=(da,run);st.session_state['nextday_selection_path']='';st.session_state['nextday_select_confirm']=False;st.session_state['nextday_creation_confirm']=False
  if windows==96:
   st.warning('96窗在启动前必须明确选有效D+1研究声明。原50MW调频/400+400MW备用及固定五机资格来源不变；仅延长已声明合成规则有效期，不是运营资格自动续期。声明登记不提前消费。原模式按W93前置合同；新post96按W94完整接受后23:45来源合同。')
   with st.expander('可选：构造独立、来源绑定的D+1研究声明（0求解）'):
    dn=st.text_input('新D+1声明名称','nextday_research',key='nextday_name')
    default_at=''
    try:
     from datetime import datetime,timedelta
     from pathlib import Path
     default_at=(datetime.fromisoformat(ctl.read(Path(run)/'seed/request.json')['origin'])+timedelta(hours=23)).isoformat()
    except Exception:pass
    available=st.text_input('明确假设声明可用时刻（ISO时间含时区，不是实测）',default_at,key='nextday_available')
    yes_day=st.checkbox('明确创建同五机合成资格/报价及原参数的D+1研究有效期声明，保留50/400/400；非实际资格认证',key='nextday_creation_confirm')
    if st.button('创建来源绑定研究声明（0求解）',disabled=not(yes_day and available and dn),key='nextday_create'):
     try:
      created=ctl.create_nextday(da,run,dn,available,confirmed=True);st.session_state['nextday_selection_path']=created['selection_path'];st.session_state['nextday_select_confirm']=False;st.json(created)
     except Exception as exc:st.error(str(exc))
   selection=st.text_input('选择来源绑定的SELECTION.json路径',key='nextday_selection_path')
   nextday_explicit=st.checkbox('明确选择以上独立D+1研究声明用于本96窗计划，允许按因果时刻消费，不是提前发布/安全授权',key='nextday_select_confirm')
  finite_h1=st.checkbox('另行选择有限候选H1研究模式（需上方五机规划；仅4窗/原需求50，H1到期00:30才用真实数据；不是全天通用）',value=False,key='live_finite_h1')
  finite_hourly=st.checkbox('另行选择H1–H24逐小时有限候选研究（仅原5台合格机/50MW、双400MW；需96窗与后SCUC日规划，H24明确选择D+1，H25拒绝）',value=False,key='live_finite_hourly')
  if finite_hourly:
   st.caption('仅在每小时申报到期时消费来源绑定的数据，不提前视为出清或安全发布。H2已单独验证；此UI完整96窗链尚未实跑，不支持任意资格库存。')
  event_sim=st.checkbox('明确允许本研究按已接受计划生成模拟启停指令（不是实际调度授权）',value=False,key='live_event_sim')
  counter=False;ac_once=False;loss_once=False  # Retained positional API slots, no AC/bypass permission.
  st.caption('模拟启停仍需明确授权；市场数学、储能、备用、AGC及时标门禁保持。')
  event_price=st.checkbox('另行选用固定曲线/混合季度非定价研究映射：整季固定原3格均值，非广东正式实现认证；默认关闭，遇事件/停机不擅自套用',value=False,key='live_event_price')
  if st.session_state.get('stop_source_identity')!=(da,run,windows):
   st.session_state['stop_source_identity']=(da,run,windows);st.session_state.pop('stop_inventory',None);st.session_state['stop_independent_permission']=False
  if st.button('只读核当前所选DA停机来源/可用范围（0求解）',key='stop_inspect'):
   try:st.session_state['stop_inventory']=ctl.stop_choices(da,run,windows)
   except Exception as exc:st.error(str(exc))
  stop_request=None;inventory=st.session_state.get('stop_inventory')
  if inventory:
   st.json({k:v for k,v in inventory.items()if k!='source_pins'});choices=[v for v in inventory['events']if v['selectable']]
   if not choices:st.info('此来源在当前首小时/保留AGC约束下无可用停机许可。域外DA延伸事件不能插入本分支；不会降低50MW调频来制造可用事件。')
   else:
    selected=st.selectbox('已核实的研究停机来源（单位/绝对5分钟格）',list(range(len(choices))),format_func=lambda i:choices[i]['unit']+' / '+str(choices[i]['absolute_tick']),key='stop_choice')
    event=choices[selected];st.json(event)
    title=st.text_input('独立研究停机声明名称',key='stop_name')
    declaration_identity=(event['unit'],event['absolute_tick'],event['curve_sha256'],title)
    if st.session_state.get('stop_declaration_identity')!=declaration_identity:st.session_state['stop_declaration_identity']=declaration_identity;st.session_state['stop_independent_permission']=False
    allow_stop=st.checkbox('独立许可：仅此来源/时刻/曲线的研究停机DA保护覆盖，不是官方指令、不解除AGC或安全要求',value=False,key='stop_independent_permission')
    if allow_stop and title.strip():stop_request=dict(unit=event['unit'],absolute_tick=event['absolute_tick'],declaration_name=title,research_stop_override=True)
    st.caption('研究启停模拟选项只许可回执模拟，不等于上述DA保护覆盖；两项互不代替。官方指令导入不支持。')
  from gd_pricing_clock_panel import render as render_clock
  pricing_clock_path=render_clock((da,run))
  with st.expander('高级：定价参考带研究参数（可选）',expanded=False):
   delta_percent=st.number_input('定价参考带 δ（%，研究假设）',min_value=0.0,max_value=100.0,value=10.0,step=1.0,key='live_pricing_delta_percent')
   pricing_delta=float(delta_percent)/100.0
   st.caption('默认10%仅为研究假设，尚未取得广东现行δ核定值；0–100%仅为本软件支持范围，不是官方允许区间。修改后须重新查看计划；不改变已有结果。')
   st.caption('官方2026 V1.0细则3.4.4.5参考日前SCED；附录3负荷平衡罚因子区域默认1000元/MWh，不等于已核实当日广东运行参数。不得为消除缺额自动放宽参考带。')
  name=st.text_input('新完整会话名','live_first_hour',key='live_name');identity=(da,run,name,event_sim,counter,ac_once,event_price,str(stop_request),windows,selection,nextday_explicit,revision,post_mode,post_day,loss_once,finite_h1,finite_hourly,pricing_clock_path,pricing_delta)
  if st.session_state.get('live_identity')!=identity:
   st.session_state['live_identity']=identity
   for k in ('live_plan','live_status'):st.session_state.pop(k,None)
   st.session_state['live_confirm']=False
  if st.button('查看完整预算/来源计划（0求解）',key='live_plan_button'):
   st.session_state.pop('live_plan',None);st.session_state['live_confirm']=False
   try:st.session_state['live_plan']=ctl.plan(da,run,name,event_sim,counter,ac_once,event_price,stop_request,windows,selection,nextday_explicit,revision,post_mode,None,post_day,loss_once,finite_h1,finite_hourly,pricing_clock_path,pricing_delta=pricing_delta)
   except Exception as e:st.error(str(e))
  p=st.session_state.get('live_plan')
  if p:st.json(p)
  yes=st.checkbox('明确采用研究边界与统一定价profile，授权上方本新分支完整列出的有功/DC市场优化预算及无偏差研究交接；不是实际调度或计量账单',key='live_confirm')
  if st.button('启动同一新分支一次',key='live_start',disabled=not(p and yes)):
   try:st.session_state['live_status']=ctl.start(p,confirmed=True);st.session_state.pop('live_plan',None)
   except Exception as e:st.error(str(e))
  if st.button('读取同一分支进度/价格/小时（0求解）',key='live_read'):
   st.session_state.pop('live_status',None)
   try:st.session_state['live_status']=ctl.status(ctl.location(run,name))
   except Exception as e:st.error(str(e))
  if st.button('读取已注册的历史结果快照（仅展示，不恢复运行）',key='live_read_history'):
   st.session_state.pop('live_status',None)
   try:
    from gd_agc_results import registered_history
    st.session_state['live_status']=registered_history(ctl.location(run,name))
   except Exception as exc:st.error(str(exc))
  if 'live_status'in st.session_state:
   z=st.session_state['live_status']
   if z.get('model_scope')=='active_power_dc':st.caption('有功/DC线路约束市场仿真；未评估交流/无功。模拟结果不是实际调度授权。')
   else:st.caption('历史/未声明新范围的会话；保留原身份，旧AC元数据不作为新DC范围门禁。')
   for block in z.get('candidate_blocks',[]):
    st.caption('历史候选元数据：'+block['status']+'；不用于新DC范围放行或阻断。')
    st.caption('以下保留历史记录原身份及诊断次数，不代表本次新运行。')
    if block.get('loss_correction'):st.caption('旧损耗纠正仅历史元数据，不用于新DC市场门禁。')
    st.json(block)
   for numeric in z.get('numerical',[]):
    if numeric['SCED_response_kind']=='native_primal_independent_derived_dual':st.warning('窗'+str(numeric['window'])+'：原生 primal + 独立派生数值证书；原生dual未通过，完整派生KKT与系统安全仍须分开。')
    st.json(numeric)
   if z.get('agc_results'):
    from gd_agc_results import render as render_agc
    render_agc(z['agc_results'])
   if z.get('events'):st.write('本分支启停事件及来源时钟');st.json(z['events'])
   for safety in z.get('safety',[]):st.write('DC模型检查记录' if safety.get('model_scope')=='active_power_dc' else '历史安全诊断元数据（不用于新DC范围门禁）');st.json(safety)
   if z.get('persistence'):st.json(z['persistence'])
   st.write('实时完成窗数：'+str(z.get('completed_windows',0)))
   for q in z.get('quarters',[]):
    st.write('季度 '+str(q['quarter']));st.dataframe([{'节点':n,'研究价 元/MWh':v}for n,v in q['raw_prices'].items()],hide_index=True)
    render_price_quality(q.get('price_quality'))
    normalization=q.get('zero_vre_reference_normalization')
    if normalization and normalization.get('changes'):
     with st.expander('零可用风光参考规范化：原始值/规范值/差值与来源'):
      st.caption('仅有原SCED精确零可用等式和非负边界证明时规范化极小数值残差；原raw不改，不是把任意负数截为0。')
      st.json(normalization)
   h=z.get('hour');view=0
   if z.get('hours'):
    view=st.selectbox('读取已完成研究小时',list(range(len(z['hours']))),key='live_hour_view');h=z['hours'][view]
   if h:
    from gd_settlement_panel import render_energy_hour
    render_energy_hour(h,z.get('hour_exports',[])[view]if z.get('hour_exports')else None)
   st.json({k:v for k,v in z.items()if k not in ('hour','quarters','events','safety')})
  st.caption('默认0调用。重复点击、失败或未知均不重启；可在下方按已保存来源核对接续；未知启动不能重试。关页面不停止后台。以上新分支卡与下方旧逐步/base入口分开，不能混作同一运行。')

  # Legacy AC-driven future-cap correction UI is intentionally not reachable.
 from gd_tail_panel import render as render_tail
 render_tail(da)

 # Normal reviewed continuation UI; never invokes a solve while rendering.
 from gd_resume_panel import render as render_resume
 render_resume(run,name)
