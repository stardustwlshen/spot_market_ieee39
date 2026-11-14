import gd_live_control as ctl
from gd_price_quality import render as render_price_quality
def render(da,run):
 import streamlit as st
 from gd_post_scuc_panel import render as render_post_scope
 render_post_scope(run)
 with st.expander('同一新分支：实时 + 小时调频 + 独立定价/核算（默认4窗，可选96窗）',expanded=True):
  st.warning('研究入口：固定五机各10MW资格、400/400MW保持；通用非零IEEE39启停和系统安全尚未实测放行。默认4窗；96窗尚未完整原生验证，非全天合规产品。')
  st.caption('同一live session：seedPRICE → W0/W1/W2各SCUC+SCED+PRICE → W3交接 → H0。4窗最多12次、96窗最多288次HiGHS，SCED统一simplex，无自动回退。缺价不补0，额外已下发3格不计入小时；不混下方旧base结果。')
  st.caption('本隔离候选：AC在候选完整数学验收后、handoff前执行；默认缺失或失败安全检查不推进状态。仅显式反事实研究可保留失败并继续模拟，非安全发布。')
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
  counter=st.checkbox('系统安全未验证或失败时仍允许继续反事实研究计算，保留blocked状态、不发布',value=False,key='live_counterfactual')
  st.caption('两个选项独立，默认关闭。无模拟指令授权时不得生成启停回执；没有安全放行且未允许反事实继续时，接受当前数学结果后停止后续计算。')
  ac_once=st.checkbox('额外运行本分支W0候选首5分钟点一次AC诊断（在handoff之前）（研究Q假设，不代表全小时/全天）',value=False,key='live_ac_once')
  loss_once=st.checkbox('另行授权同一未下发候选一次同候选损耗纠正诊断：最多额外1次SCED和1次AC，仅原c10e/50/400研究条件；仍不安全则阻断',value=False,key='live_loss_once')
  if loss_once and not ac_once:st.warning('损耗纠正必须同时明确选择首次AC诊断；默认不启用。')
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
  name=st.text_input('新完整会话名','live_first_hour',key='live_name');identity=(da,run,name,event_sim,counter,ac_once,event_price,str(stop_request),windows,selection,nextday_explicit,revision,post_mode,post_day,loss_once,finite_h1,finite_hourly)
  if st.session_state.get('live_identity')!=identity:
   st.session_state['live_identity']=identity
   for k in ('live_plan','live_status'):st.session_state.pop(k,None)
   st.session_state['live_confirm']=False
  if st.button('查看完整预算/来源计划（0求解）',key='live_plan_button'):
   st.session_state.pop('live_plan',None);st.session_state['live_confirm']=False
   try:st.session_state['live_plan']=ctl.plan(da,run,name,event_sim,counter,ac_once,event_price,stop_request,windows,selection,nextday_explicit,revision,post_mode,None,post_day,loss_once,finite_h1,finite_hourly)
   except Exception as e:st.error(str(e))
  p=st.session_state.get('live_plan')
  if p:st.json(p)
  yes=st.checkbox('明确采用研究边界与统一定价profile，授权上方本新分支完整列出的优化及AC预算（含所选额外纠正）及无偏差研究交接；不是实际调度或计量账单',key='live_confirm')
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
   z=st.session_state['live_status'];st.error('正式发布始终未授权；模型KKT通过不等于系统安全通过。')
   for block in z.get('candidate_blocks',[]):
    st.error('候选已通过数值验收，但状态衔接前安全隔离：'+block['status']+'。不是全小时完成或数学失败。')
    st.write('已占用AC次数：'+str(block['AC_calls_consumed'])+'；handoff存在：'+str(block['handoff_exists']))
    if block.get('loss_correction'):st.warning('同候选损耗纠正诊断：原候选和纠正候选分列保留；两轮AC不等于安全通过。纠正后仍阻断handoff及PRICE。')
    st.json(block)
   for numeric in z.get('numerical',[]):
    if numeric['SCED_response_kind']=='native_primal_independent_derived_dual':st.warning('窗'+str(numeric['window'])+'：原生 primal + 独立派生数值证书；原生dual未通过，完整派生KKT与系统安全仍须分开。')
    st.json(numeric)
   if z.get('agc_results'):
    from gd_agc_results import render as render_agc
    render_agc(z['agc_results'])
   if z.get('events'):st.write('本分支启停事件及来源时钟');st.json(z['events'])
   for safety in z.get('safety',[]):st.write('本分支安全检查（逐点、逐来源）');st.json(safety)
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

  # This form speaks only to a still-running worker. No host is reconstructed from JSON.
  try:future=ctl.future_state(ctl.location(run,name))
  except Exception as exc:future=None;st.error(str(exc))
  if future:
   st.warning('原安全阻断保留。未来功率基点上限不是技术降额，也不能修复历史失败点。默认只构模，另行明确确认最多2次求解；10分钟过期或进程退出不可恢复。')
   st.json(future)
   if future.get('interactive')and future['status']=='waiting_proposal':
    desc=future['description'];unit=st.selectbox('未来首个5分钟格的热机',list(desc['units']),key='cap_unit')
    bounds=desc['units'][unit];st.caption('允许上限范围 '+str(bounds)+'；不从AC诊断自动推导数值。')
    cap=st.number_input('明确研究功率上限 MW',min_value=float(bounds['minimum_cap_mw']),max_value=float(bounds['maximum_cap_mw']),value=float(bounds['maximum_cap_mw']),key='cap_value_'+unit)
    reason=st.text_input('声明理由',key='cap_reason');source=st.text_input('数据来源/明确研究假设',key='cap_source')
    if st.button('发送声明以生成真实登记计划（0求解）',disabled=not(reason and source),key='cap_propose'):
     try:st.json(ctl.future_command(ctl.location(run,name),'proposal',request=dict(unit=unit,maximum_power_mw=cap,reason=reason,source_statement=source)))
     except Exception as exc:st.error(str(exc))
   if future.get('interactive')and future['status']=='waiting_confirmation':
    digest=future['registration_plan']['confirmation_digest']
    yes_cap=st.checkbox('确认上面完整来源/未来时段/上限声明，仅登记并构模，不授权求解、不宣称安全修复',key='cap_confirm_'+digest)
    if st.button('确认登记并构造未来候选（0求解）',disabled=not yes_cap,key='cap_register_'+digest):
     try:st.json(ctl.future_command(ctl.location(run,name),'confirm',confirmation_digest=digest,confirmed=True))
     except Exception as exc:st.error(str(exc))
   st.caption('发送后点击上方读取进度刷新。仅一次声明及一次2调用确认；不运行额外AC/PRICE。进程退出后不可执行或持久恢复。')

   if future.get('interactive')and future['status']=='waiting_execution_confirmation':
    digest=future['registration_plan']['confirmation_digest'];st.json(future['budget'])
    execute_yes=st.checkbox('单独授权以上已构造未来候选1次SCUC+1次SCED（每次求解3600秒、单线程、SCUC gap1%/SCED为LP无gap），无重试，无AC/定价，保留原安全失败',key='cap_execute_yes_'+digest)
    if st.button('运行未来研究候选一次（2调用）',disabled=not execute_yes,key='cap_execute_'+digest):
     try:st.json(ctl.future_command(ctl.location(run,name),'execute',confirmation_digest=digest,confirmed=True))
     except Exception as exc:st.error(str(exc))
 from gd_tail_panel import render as render_tail
 render_tail(da)

 # Normal reviewed continuation UI; never invokes a solve while rendering.
 from gd_resume_panel import render as render_resume
 render_resume(run,name)
