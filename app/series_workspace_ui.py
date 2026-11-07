"""A retained, compact task/day workflow. Never simulates missing capabilities."""
from __future__ import annotations
import copy,json
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import pandas as pd
import streamlit as st
from common import ROOT,sc,scen_config
from smm import series_workspace as ws
from smm import series_ui_adapter as adapter

KIND_CN={'wind':'风电','pv':'光伏','load':'负荷','natural_inflow':'天然来水'}
POLICY_CN={'responsive':'更新及时','baseline':'常规','delayed':'观测延迟','persistent_high':'持续偏高','persistent_low':'持续偏低','turning_point_lag':'转折跟随较慢'}
PRESET_CN={'wind_low':'持续低风','wind_normal':'常态风','wind_high':'高风','wind_ramp':'风速爬坡','wind_volatile':'高波动风',
'pv_clear':'晴空','pv_overcast':'阴天','pv_broken_cloud':'间歇云','pv_fast_cloud':'快速云变','load_normal':'常态负荷','load_low':'低负荷','load_high':'高负荷','load_peak_shift':'峰时偏移','load_volatile':'高波动负荷',
'inflow_steady':'平稳来水','inflow_dry':'持续低来水','inflow_rising':'涨水','inflow_pulse':'洪峰脉冲','inflow_recession':'退水'}


def _save(root,m,new,**kwargs):
    ws.update_task(root,new,expected_revision=m['revision'],**kwargs)
    st.rerun()


def scene_summary(cfg):
    batteries=cfg.units.get('storage',[])
    strict=sum(h.get('commitment',{}).get('mode')=='unit_duration_v1' for h in cfg.units.get('hydro',[]))
    bid_name={'legacy_v1':'旧版报价','cost_basis_v2':'按成本编制报价'}.get(cfg.base.get('default_bid_model','legacy_v1'),'自定义报价')
    return f"储能{len(batteries)}站 · {sum(float(u['pmax']) for u in batteries):g} MW / {sum(float(u['ecap']) for u in batteries):g} MWh · 水电逐台初态{strict}站 · {bid_name}"


def create_panel(tasks):
    with st.expander('新建模拟',expanded=not tasks):
        scenes=sc.list_scenarios()
        # Apply the selection on the next render, before its widget exists.
        pending_source=st.session_state.pop('series_create_source_pending',None)
        if pending_source in scenes:
            st.session_state['series_create_source']=pending_source
        source=st.selectbox('选择场景',scenes,key='series_create_source',help='选择已有的机组、网络和报价配置；每天的风光、负荷与来水在创建后设置。')
        cfg=scen_config(source)
        with st.form('series_create'):
            c1,c2=st.columns(2)
            start=c1.date_input('从哪天开始',value=date.fromisoformat(str(cfg.base['date'])))
            count=c2.number_input('模拟几天',min_value=1,max_value=31,value=1,step=1)
            with st.expander('更多设置（可选）'):
                title=st.text_input('模拟名称',value=source+'模拟')
                input_model=st.selectbox('任务输入方式',['causal_physical_v3','deterministic_known_inputs_v1'],format_func=lambda x:'按时更新预测（研究模拟）' if x=='causal_physical_v3' else '预先知道全部实际曲线（仅联调）',help='不改变实际过程；因果模式仅使用发布时点已知的信息。')
                c1,c2=st.columns(2)
                seed=c1.text_input('随机种子',value=str(cfg.base['seed']),help='相同输入和种子可复现同一组研究情景。')
                tz=c2.text_input('运行时区',value=cfg.base.get('simulation_timezone','Asia/Shanghai'),help='IANA时区；经营日须完整24小时。')
            if st.form_submit_button('创建模拟',type='primary'):
                try:
                    root=ws.create_task(ROOT/'series_tasks',sc.scenario_path(source),title=title,start_date=str(start),day_count=int(count),timezone=tz,root_seed=seed,input_model=input_model)
                    st.session_state['series_task']=str(root);st.rerun()
                except (OSError,ValueError,KeyError,TypeError) as exc:st.error(str(exc))
        with st.expander('场景管理（可选）'):
            st.caption('已有场景可以直接使用。只有想采用当前版本默认参数时，才需要另建场景。')
            st.caption('所选场景：'+scene_summary(cfg))
            if st.button('查看或修改场景机组',key='series_open_source'):
                st.session_state['scenario']=source
                st.session_state['_sel_scenario']=source
                st.switch_page('pages/2_运行.py')
            from smm.config import load_config
            current=load_config(root=sc.ROOT)
            st.caption('当前版本默认：'+scene_summary(current))
            new_name=st.text_input('新方案名称',value='新版默认_研究')
            if st.button('另建新版默认方案'):
                try:
                    created=sc.create_scenario(new_name,source=None,note='用户明确从当前版本默认配置新建；未覆盖已有场景或手工报价')
                    st.session_state['series_create_source_pending']=created.name;st.rerun()
                except (ValueError,OSError) as exc:st.error(str(exc))


def local_time(value,timezone):
    try:
        return datetime.fromisoformat(str(value).replace('Z','+00:00')).astimezone(ZoneInfo(timezone)).strftime('%m-%d %H:%M')
    except (ValueError,TypeError):return '未记录'


def initial_summary(state):
    groups=[('火电',[dict(机组=u,工况='开机' if x['online'] else '停机',初始出力_MW=x['p0'],已持续_小时=x['hours']) for u,x in state['thermal'].items()]),
        ('电池',[dict(机组=u,初始电量_MWh=x) for u,x in state['storage'].items()]),
        ('水电',[dict(电站=u,初始库容_万立方米=x,初始出力_MW=state['hydro_p'][u],开机台数=state['hydro_online'][u]) for u,x in state['hydro'].items()]),
        ('抽蓄',[dict(电站=u,初始上库量_万立方米=x['V'],发电台数=x['ng'],抽水台数=x['np']) for u,x in state['pumped'].items()])]
    available=[g for g in groups if g[1]]
    if not available:return
    for tab,(name,rows) in zip(st.tabs([g[0] for g in available]),available):
        with tab:
            frame=pd.DataFrame(rows);frame.columns=[c.replace('_',' · ') for c in frame.columns]
            st.dataframe(frame,hide_index=True,width='stretch')


def initial_panel(root,m,begun):
    st.caption('只设置第一天；后续自动接续前一天实际末态。')
    if not begun:
        with st.expander('从上一天实际结果接续'):
            st.caption('检查点必须完整、相邻且设备/规则/网络一致；导入后只读，不重新声明初态。')
            upload=st.file_uploader('选择上一天的状态文件',type=['json'],key='series_checkpoint_import',help='使用完整日保存的 actual_checkpoint.json；不从当前场景猜补。')
            if upload is not None and st.button('校验并采用实际日末状态'):
                try:
                    checkpoint=json.loads(upload.getvalue())
                    new=copy.deepcopy(m);new['initial_state']=adapter.import_actual_checkpoint(root,m,checkpoint)
                    new['initial_state_source']='executed_checkpoint';new['source_checkpoint_hash']=checkpoint['checkpoint_hash']
                    _save(root,m,new)
                except Exception as exc:st.error(str(exc))
    if m['initial_state'] is None:
        with st.expander('先看默认起点',expanded=True):
            try:initial_summary(adapter.research_initial(root,m)['state'])
            except Exception as exc:st.warning('起点尚不能采用：'+str(exc))
        acknowledge=st.checkbox('采用上述配置作为研究起点',key=f'initial_ack:{m["task_id"]}',disabled=begun,
            help='这是研究声明，不是实测历史。水电逐台持续时间必须已在基础场景设置；不会自动补年龄。')
        if st.button('确认研究起点',disabled=not acknowledge or begun):
            try:
                new=copy.deepcopy(m);new['initial_state']=adapter.research_initial(root,m);new['initial_state_source']='research_assumption';_save(root,m,new)
            except Exception as exc:st.error(str(exc))
        return
    packed=m['initial_state'];state=packed['state']
    editable=not begun and m['initial_state_source']=='research_assumption'
    origin='研究起点' if m['initial_state_source']=='research_assumption' else '上一日实际末态（只读）'
    st.caption(f"{local_time(packed['state_at'],m['timezone'])} · {m['timezone']} · {origin}")
    initial_summary(state)
    if begun:st.info('初态已冻结；改变初态须新建研究任务。')
    if begun or m['initial_state_source']!='research_assumption':
        with st.expander('复制为可编辑研究初态（新任务分支）'):
            st.warning('这会中断原实际接续链，保留原记录；日报价必须重新确认。')
            reason=st.text_input('研究分支原因',key='series_fork_reason')
            if st.button('新建断开接续的研究分支',disabled=not reason.strip()):
                try:
                    target=ws.fork_research_task(root,m,reason=reason)
                    st.session_state['series_task']=str(target);st.rerun()
                except Exception as exc:st.error(str(exc))
    with st.expander('调整起始状态',expanded=False):
        choices=[(family,uid) for family in ('thermal','storage','hydro','pumped') for uid in state[family]]
        family,uid=st.selectbox('选择机组或电站',choices,format_func=lambda x:x[1],key='series_initial_unit')
        with st.form(f'initial_edit:{m["task_id"]}:{uid}'):
            changes={}
            if family=='thermal':
                old=state[family][uid];on=st.checkbox('起始时已开机',value=old['online'],disabled=not editable)
                p=st.number_input('上一执行功率（MW）',value=float(old['p0']),format='%.15g',disabled=not editable)
                h=st.number_input('这一状态已持续（小时）',value=float(old['hours']),min_value=0.,format='%.15g',disabled=not editable)
                changes={'thermal':{uid:dict(online=on,p0=p,hours=h)}}
            elif family=='storage':changes={'storage':{uid:st.number_input('起始电量（MWh）',value=float(state['storage'][uid]),format='%.15g',disabled=not editable)}}
            elif family=='pumped':
                changes={'pumped':{uid:{'V':st.number_input('起始上库量（万m³）',value=float(state['pumped'][uid]['V']),format='%.15g',disabled=not editable)}}}
                st.caption('工况与切换前史须一起修改，在下方高级设置中核对；不能只改一个开关清除义务。')
            else:
                v=st.number_input('起始库容（万m³）',value=float(state['hydro'][uid]),format='%.15g',disabled=not editable)
                p=st.number_input('上一执行功率（MW）',value=float(state['hydro_p'][uid]),format='%.15g',disabled=not editable)
                changes={'hydro':{uid:dict(V=v,P=p)}}
                hc=state.get('hydro_commitment',{}).get('stations',{}).get(uid,{}).get('units',{})
                if hc:
                    data=[dict(机组=u,开机=r['online'],已持续小时=r['elapsed_h'],时长含义={'exact':'精确时长','lower_bound':'至少已持续'}[r['elapsed_kind']],最早切换=local_time(r['required_until'],m['timezone'])) for u,r in hc.items()]
                    edited=st.data_editor(pd.DataFrame(data),hide_index=True,disabled=['机组','最早切换'] if editable else True,num_rows='fixed',key=f'hydro_initial_editor:{m["task_id"]}:{m["revision"]}:'+uid,
                        column_config={'时长含义':st.column_config.SelectboxColumn(options=['精确时长','至少已持续'],help='精确时长表示已知持续多久；至少已持续只声明下界。'), '最早切换':st.column_config.TextColumn(help='按任务时区显示；未履行的最小开停时间不得跳过。')})
                    changes['hydro_units']={uid:{r['机组']:dict(online=bool(r['开机']),elapsed_h=float(r['已持续小时']),elapsed_kind={'精确时长':'exact','至少已持续':'lower_bound'}[r['时长含义']]) for r in edited.to_dict('records')}}
            if st.form_submit_button('校验并保存初态',disabled=begun or m['initial_state_source']!='research_assumption'):
                try:
                    new=copy.deepcopy(m);new['initial_state']=adapter.update_research_state(root,m,changes);_save(root,m,new)
                except Exception as exc:st.error(str(exc))
    with st.expander('高级：完整初态与运行前史'):
        st.caption('包含在途水与切换历史。所有修改仍须通过同一个物理校验器；缺失历史不自动置零。')
        raw=st.text_area('完整研究初态 JSON',value=json.dumps(packed,ensure_ascii=False,indent=2),height=240,key=f'initial_json:{m["task_id"]}:{m["revision"]}',disabled=not editable)
        if st.button('校验并保存完整初态',disabled=begun or m['initial_state_source']!='research_assumption'):
            try:
                new=copy.deepcopy(m);new['initial_state']=adapter.validate_initial(root,m,json.loads(raw));_save(root,m,new)
            except Exception as exc:st.error(str(exc))


def day_panel(root,m,begun,day_date):
    causal=m['generation']['model']=='causal_physical_v3'
    summary=[]
    for d in m['days']:
        row=dict(日期=d['date'],水期='丰水' if d['season']=='wet' else '枯水')
        if causal:
            from smm.causal_presets import DEFAULTS
            for kind in ws.KINDS:
                preset=d['process_specs'][kind].get('preset') or DEFAULTS[kind]
                row[KIND_CN[kind]]=PRESET_CN.get(preset,preset)+' / '+POLICY_CN.get(d['forecast_policy'].get(kind,'baseline'),'常规')
        else:row.update(风电倍率=d['wind_multiplier'],光伏倍率=d['pv_multiplier'],负荷倍率=d['load_multiplier'])
        row.update(报价=ws.bid_status(d))
        summary.append(row)
    if len(summary)>1:
        with st.expander('查看全部日期安排'):
            st.dataframe(pd.DataFrame(summary),hide_index=True,width='stretch')
    d=ws.day_by_date(m,day_date)
    chosen=next(row for row in summary if row['日期']==day_date)
    st.caption(' · '.join(str(value).split(' / ')[0] for key,value in chosen.items() if key not in ('日期','报价')))
    st.caption('可直接使用上述场景；需要改变风光、负荷或来水时再展开修改。')
    with st.expander('调整当天场景（可选）'):
        caps=adapter.capabilities();catalog=caps['causal']
        with st.form(f'series_day:{m["task_id"]}:{day_date}'):
            season=st.selectbox('来水季节模板',['wet','dry'],index=0 if d['season']=='wet' else 1,format_func=lambda s:'丰水' if s=='wet' else '枯水')
            wind,pv,load=(d[k] for k in ('wind_multiplier','pv_multiplier','load_multiplier'))
            if not causal:
                c1,c2,c3=st.columns(3)
                wind=c1.number_input('风电倍率',.1,2.,float(wind),format='%.15g')
                pv=c2.number_input('光伏倍率',.1,2.,float(pv),format='%.15g')
                load=c3.number_input('负荷倍率',.1,2.,float(load),format='%.15g')
                st.caption('固定已知曲线联调不使用预测误差场景。')
            specs=copy.deepcopy(d['process_specs']);policies=copy.deepcopy(d['forecast_policy'])
            if catalog and causal:
                cols=st.columns(2)
                for i,kind in enumerate(ws.KINDS):
                    col=cols[i%2]
                    items=catalog['actual_presets'][kind]
                    from smm.causal_presets import DEFAULTS
                    current=specs[kind]['preset'];current=current if current in items else DEFAULTS[kind]
                    name=col.selectbox({'wind':'风电情况','pv':'光伏天气','load':'用电水平','natural_inflow':'来水情况'}[kind],items,index=items.index(current),format_func=lambda x:PRESET_CN.get(x,x))
                    specs[kind]=dict(preset=name,parameters=specs[kind]['parameters'] if name==current else {})
                with st.expander('预测情况 · 可分别调整四类误差'):
                    st.caption('研究情形，不代表实际预测准确率。')
                    for i,kind in enumerate(ws.KINDS):
                        policies[kind]=st.selectbox(KIND_CN[kind]+'预测情况',catalog['forecast_policies'],index=catalog['forecast_policies'].index(policies.get(kind,'baseline')),format_func=lambda x:POLICY_CN.get(x,x))
                if not catalog.get('consumer_integration_ready'):st.warning('新预测过程已注册，消费者联调尚未通过；现在只能保存选择，不能启动该模式。')
                with st.expander('详细调整：四类过程参数'):
                    from smm.causal_presets import resolve_actual
                    for kind in ws.KINDS:
                        st.markdown('**'+KIND_CN[kind]+'**')
                        values=resolve_actual(kind,specs[kind]);bounds=catalog['parameter_schema'][kind]['bounds']
                        for param,value in values.items():
                            if isinstance(value,str):
                                st.caption('云况：'+{'clear':'晴空','shaded':'遮云','switching':'晴阴交替'}.get(value,str(value)));continue
                            lo,hi=bounds[param]
                            help_text={'target':'风电为容量因子；负荷为需求倍率；来水为参考补给倍率。',
                                'target_end':'事件结束的风电目标容量因子。','event_hour':'事件在当天的当地小时。',
                                'duration_h':'事件持续小时数。','amplitude':'来水脉冲相对固定参考流量的倍率。',
                                'clear_k':'晴态透过率，0到1。','shaded_k':'遮云透过率，0到1。',
                                'peak_shift_h':'相对公开日形的峰时偏移小时。',
                                'slow_sigma':'慢变化创新强度，不是输出曲线的误差百分比。','fast_sigma':'快变化创新强度，不是输出曲线的误差百分比。',
                                'rho':'资源空间共同分量相关强度。','rho_slow':'慢过程共同分量相关强度。','rho_fast':'快过程共同分量相关强度。',
                                'sigma':'过程状态创新强度，不是预测准确率。','noise_amplitude':'来水低频噪声相对参考流量强度。'}
                            help_=help_text.get(param,'时间常数或驻留时长（小时）；越大变化越慢。' if param.endswith('_h') else '该预设的模型过程参数。')
                            parameter_name={'target':'目标水平','target_end':'结束目标','event_hour':'开始时刻（当地小时）','duration_h':'持续时间（小时）','slow_h':'慢变化时间（小时）','fast_h':'快变化时间（小时）','slow_sigma':'慢变化波动强度','fast_sigma':'快变化波动强度','rho_slow':'慢变化空间相关程度','rho_fast':'快变化空间相关程度','clear_k':'晴空透过率','shaded_k':'遮云透过率','clear_h':'晴段平均时长（小时）','shaded_h':'阴段平均时长（小时）','response_h':'响应时间（小时）','sigma':'波动强度','rho':'空间相关程度','peak_shift_h':'峰时偏移（小时）','base_h':'基流响应时间（小时）','rise_h':'涨水响应时间（小时）','fall_h':'退水响应时间（小时）','route_h':'汇流响应时间（小时）','noise_h':'扰动时间（小时）','noise_amplitude':'低频扰动强度','lag_h':'到达滞后（小时）'}.get(param,param)
                            number=st.number_input(f'{KIND_CN[kind]} · {parameter_name}',min_value=float(lo),max_value=float(hi),value=float(value),format='%.15g',help=help_,key=f'process:{day_date}:{kind}:{specs[kind]["preset"]}:{param}')
                            specs[kind]['parameters'][param]=number
            elif causal:st.info('四资源因果预测模块尚未安装，误差选项暂不开放。')
            if st.form_submit_button('保存该日场景',disabled=begun):
                new=copy.deepcopy(m);x=ws.day_by_date(new,day_date)
                values=dict(season=season,wind_multiplier=wind,pv_multiplier=pv,load_multiplier=load)
                x['locked']=sorted(set(x['locked'])|{k for k,v in values.items() if v!=d[k]})
                for group,new_values in ((('process_specs',specs),('forecast_policy',policies)) if causal else ()):
                    for kind,value in new_values.items():
                        old=d[group].get(kind)
                        if group=='process_specs' and catalog:
                            from smm.causal_presets import resolve_actual,DEFAULTS
                            original=copy.deepcopy(old or {})
                            if not original.get('preset'):original['preset']=DEFAULTS[kind]
                            changed=resolve_actual(kind,value)!=resolve_actual(kind,original) or value['preset']!=original['preset']
                        else:changed=value!=(old or 'baseline')
                        if changed:x['locked']=sorted(set(x['locked'])|{f'{group}.{kind}'})
                x.update(values);x['process_specs']=specs;x['forecast_policy']=policies;x['source']='user_edited'
                _save(root,m,new)
        with st.expander('应用到多天 / 随机生成'):
            targets=st.multiselect('目标日期',[x['date'] for x in m['days']],default=[x['date'] for x in m['days']],key='series_batch_dates')
            st.caption('随机只填未手改字段，不改变报价，也不把随机选项当逐时白噪声。')
            c1,c2=st.columns(2)
            if c1.button('随机生成所选天',disabled=begun or not targets):_save(root,m,ws.randomize_unlocked(m,targets))
            if c2.button('把这天场景复制过去',disabled=begun or not targets):_save(root,m,ws.copy_day_settings(m,day_date,targets))
    return day_date


def bid_panel(root,m,day_date,begun):
    day=ws.day_by_date(m,day_date);blocked=(Path(root)/'run'/f'day_{day_date}').exists()
    cfg=adapter.day_config(root,m,day_date)
    st.subheader(f"{day_date} · {ws.bid_status(day)}")
    if blocked:st.info('这一天已开始出清，报价只读。')
    elif day['bid_package'] is None:st.info('先为这一天生成或复制报价，再逐机组调整并确认。')
    else:st.caption(f"报价 v{day['bid_package']['version']} · 只属于这一天，修改后须重新确认。")
    from common import TYPE_CN
    from unit_input_view import render_unit_summary,render_unit_information
    unit_types={u['id']:u.get('type',group) for group in ('thermal','hydro','renewable','storage','pumped','tie') for u in cfg.units.get(group,[]) if u.get('enabled',True)}
    package=day['bid_package']
    choices=list(package['units']) if package is not None else list(unit_types)
    uid=st.selectbox('选择报价机组',choices,key='series_bid_unit',format_func=lambda u:f'{u} · {TYPE_CN.get(unit_types.get(u),"机组")}')
    render_unit_summary(cfg,uid)
    with st.expander('查看完整机组参数'):
        st.caption('这里是场景设备档案；本次模拟的起始状态在“场景 → 起始状态”中核对。')
        render_unit_information(cfg,uid,compact=True)
    candidate=Path(root)/'source/quote_candidate.json'
    if package is None:
        if st.button('生成这天的报价草稿',disabled=blocked,type='primary'):
            try:
                from smm.bids import default_bids
                _save(root,m,ws.replace_daily_bids(m,day_date,default_bids(cfg),source='explicit_cost_template'),changed_day=day_date,bids_only=True)
            except Exception as exc:st.error(str(exc))
    with st.expander('复制或重新生成报价'):
        if st.button('复制基础方案报价到这天',disabled=blocked or not candidate.exists()):
            _save(root,m,ws.replace_daily_bids(m,day_date,json.loads(candidate.read_text(encoding='utf-8')),source='explicit_base_copy'),changed_day=day_date,bids_only=True)
        if package is not None and st.button('生成这天的报价草稿',disabled=blocked):
            try:
                from smm.bids import default_bids
                _save(root,m,ws.replace_daily_bids(m,day_date,default_bids(cfg),source='explicit_cost_template'),changed_day=day_date,bids_only=True)
            except Exception as exc:st.error(str(exc))
        available=[d['date'] for d in m['days'] if d['bid_package'] is not None and d['date']!=day_date]
        if available:
            src=st.selectbox('从其他日期复制报价',available,key='series_bid_copy_source')
            if st.button('明确复制所选日报价',disabled=blocked):
                _save(root,m,ws.replace_daily_bids(m,day_date,ws.day_by_date(m,src)['bid_package'],source='explicit_day_copy:'+src),changed_day=day_date,bids_only=True)
        if package is not None and m['generation']['model']=='causal_physical_v3':
            st.caption('风光可用量留空时使用已发布预测；手工申报保持原样。')
            if st.button('用当日已发布预测填写风光可用量',disabled=blocked):
                try:
                    updated=adapter.apply_issued_bid_availability(root,m,day_date)
                    _save(root,m,ws.replace_daily_bids(m,day_date,updated,source='explicit_issued_DA_forecast'),changed_day=day_date,bids_only=True)
                except Exception as exc:st.error(str(exc))
    if package is None:return
    b=package['units'][uid]
    with st.form(f'series_bid:{m["task_id"]}:{day_date}:{uid}:{package["version"]}'):
        edited=copy.deepcopy(b)
        from quote_simulation import quote_rule_notes
        typ=next((u.get('type',group) for group in ('thermal','hydro','renewable','storage','pumped','tie') for u in cfg.units.get(group,[]) if u['id']==uid),'unknown')
        curves={'segments':'发电报价','gen_segments':'抽蓄发电报价','pump_segments':'抽水报价','dis_segments':'放电报价','ch_segments':'充电报价'}
        for curve,label in curves.items():
            if curve not in b:continue
            st.markdown('**'+label+'**')
            notes=quote_rule_notes(cfg,uid,typ,curve)
            st.caption(notes[0])
            if len(notes)>1:
                with st.expander('报价规则与分段说明'):
                    for note in notes[1:]:st.caption(note)
            frame=st.data_editor(pd.DataFrame(b[curve]),num_rows='dynamic',hide_index=True,column_order=['p_start','p_end','price'],column_config={'p_start':st.column_config.NumberColumn('起始功率 · MW'),'p_end':st.column_config.NumberColumn('截止功率 · MW'),'price':st.column_config.NumberColumn('报价 · 元/MWh')},key=f'series_curve:{day_date}:{uid}:{curve}:{package["version"]}')
            edited[curve]=frame.to_dict('records')
        extra={k:v for k,v in b.items() if k not in curves}
        with st.expander('高级申报参数（可修改）'):
            descriptions={'mode':'price_quantity为报量报价；quantity_only为报量不报价。储能可选self_schedule自调度，抽蓄可选dispatch调度，水电可选planned计划。仅允许该类型支持的方式。',
                'startup_cost':'冷/温/热启动申报价，元/次；不是利润核算成本。','noload_cost':'开机空载申报价，元/小时；不是利润核算成本。',
                'soc_min':'申报最低SOC，0到1；仍受设备物理范围限制。','soc_max':'申报最高SOC，0到1；仍受设备物理范围限制。',
                'soc_end':'申报日末SOC，0到1；null按模型的未申报目标处理。','vup_end':'抽蓄申报日末上库量，万m³；null按未申报目标处理。',
                'avail_pmax':'该日各时段申报可用功率上限，MW；不自动改写实际曲线。',
                'availability_provenance':'因果模式中可用量的来源：已发布预测、公开上界或明确用户声明。'}
            for name in extra:
                if name in descriptions:st.caption(name+'：'+descriptions[name])
            text=st.text_area('其他申报参数（高级）',value=json.dumps(extra,ensure_ascii=False,indent=2),help='保留未改字段和继承语义；不自动用技术缺省值覆盖手工报价。')
        if st.form_submit_button('保存该主体日报价',disabled=blocked):
            try:
                rest=json.loads(text)
                if not isinstance(rest,dict) or set(rest)&set(curves):raise ValueError('其他参数不能替换分段表')
                edited={k:v for k,v in edited.items() if k in curves};edited.update(rest)
                updated=copy.deepcopy(package);updated['units'][uid]=edited
                from smm.bids import validate_bids,raise_on_errors
                raise_on_errors(validate_bids(cfg,updated,require_complete=True))
                _save(root,m,ws.replace_daily_bids(m,day_date,updated,source='user_edited'),changed_day=day_date,bids_only=True)
            except Exception as exc:st.error(str(exc))
    st.download_button('下载该日完整报价',json.dumps(package,ensure_ascii=False,indent=2),file_name=f'{day_date}_报价_v{package["version"]}.json',mime='application/json')
    st.caption('默认报价可直接确认；改价后先保存。')
    if st.button('确认该日全部机组报价',disabled=blocked or ws.bid_status(day)=='已确认',type='primary'):
        try:_save(root,m,ws.confirm_daily_bids(m,day_date,cfg),changed_day=day_date,bids_only=True)
        except Exception as exc:st.error(str(exc))


def run_panel(root,m,begun):
    caps=adapter.capabilities()
    labels={'deterministic_known_inputs_v1':'固定实际曲线、全部时段已知（联调）','causal_physical_v3':'四资源因果预测（研究场景）'}
    options=list(labels)
    with st.expander('高级：输入与预测方式'):
        model=st.selectbox('输入与预测方式',options,index=options.index(m['generation']['model']),format_func=labels.get,disabled=begun,key='series_generation_model')
        if model!=m['generation']['model'] and st.button('保存输入方式',disabled=begun):
            new=copy.deepcopy(m);new['generation']['model']=model
            for day in new['days']:day['bid_receipt']=None
            _save(root,m,new)
    if m['generation']['model']=='deterministic_known_inputs_v1':
        st.warning('联调模式明确假设未来实际曲线全部已知，不用于评估预测误差或实际预测表现。')
        selected=st.selectbox('预览日期',[d['date'] for d in m['days']],key='series_preview_date')
        try:
            p=adapter.deterministic_profiles(root,m,selected)
            cols=[x for x in p if x.endswith('_actual')]
            st.line_chart(p[cols].rename(columns=lambda c:c.removesuffix('_actual')),y_label='MW')
        except Exception as exc:st.error(str(exc))
    else:
        if caps['causal_execution_contract']:
            st.caption('按时更新预测；实时当前段使用完美测量假设。')
        else:st.warning('因果输入接口尚不完整，仅可预览，禁止运行。')
        with st.expander('可选：先看曲线和预测'):
            with st.expander('公开规模与来水基准'):
                st.caption('负荷基准为6254.23 MW × 基础场景load_scale；网络节点负荷仅用于分配权重。')
                st.caption('来水以基础场景正参考流量为公开基准；逐日季节可设为零来水，但初始参考为零时本模式阻断，不自动改成常量。')
            c1,c2,c3=st.columns(3)
            d=c1.selectbox('预测预览日期',[d['date'] for d in m['days']],key='causal_preview_date')
            stage=c2.selectbox('预览发布阶段',['DA','ID','RT'],format_func=lambda x:{'DA':'日前','ID':'日内参考','RT':'实时预测'}[x])
            period=c3.number_input('发布时段（0至95）',min_value=0,max_value=95,value=0,disabled=stage=='DA',help='每段15分钟；日前固定0段。此处仅预览，不推进执行状态。')
            if st.button('生成并冻结这版输入预览'):
                try:st.session_state['causal_preview:'+str(root)]=(ws.input_signature(m),d,stage,int(period),adapter.causal_preview(root,m,d,stage=stage,issue_period=int(period)))
                except Exception as exc:st.error(str(exc))
            saved=st.session_state.get('causal_preview:'+str(root))
            if saved:
                if saved[:4]!=(ws.input_signature(m),d,stage,int(period)):st.warning('场景或预览时点已改变，请重新生成预览；旧运行不受影响。')
                else:
                    preview=saved[4]
                    st.caption('预测发布：'+preview['issue_time_utc']+'；实际曲线仅供离线对照，未交给预测器。')
                    tabs=st.tabs([KIND_CN[k] for k in ws.KINDS])
                    for kind,tab in zip(ws.KINDS,tabs):
                        with tab:
                            resources=[r for r in preview['resources'] if r['kind']==kind]
                            if not resources:st.info('本任务没有该类资源。');continue
                            uid=st.selectbox('预览主体',[r['id'] for r in resources],key='causal_resource:'+kind)
                            unit=resources[0]['unit']
                            st.line_chart(pd.DataFrame({'原发布预测':preview['forecast'][uid],'实际（离线对照）':preview['actual'][uid]},index=preview['period']),y_label='m³/s' if unit=='m3/s' else 'MW',color=['#2563eb','#dc2626'])
                    with st.expander('预览来源'):
                        st.write({'实际实现指纹':preview['actual_hash'],'发布证据':preview['evidence']})

    with st.expander('运行设置（可选）'):
        mode=st.radio('遇到未确认报价的日期',['all_ready','ready_prefix'],format_func=lambda x:'等全部日期确认后再运行' if x=='all_ready' else '先运行已确认的日期，之后暂停',key='series_admission')
    if st.button('检查输入'):
        st.session_state['series_validation:'+str(root)]=adapter.readiness(root,m,mode)
    check=st.session_state.get('series_validation:'+str(root))
    valid=bool(check and check['input_signature']==ws.input_signature(m) and check['mode']==mode)
    if check and not valid:st.warning('输入或运行方式已改变，请重新验证。')
    if valid:
        for error in check['errors']:st.error(error)
        for warning in check['warnings']:st.warning(warning)
        if check['ready']:st.success('输入准入通过；不代表出清可行或精度通过。')
    state=adapter.launch_state(root)
    if st.button('开始模拟',type='primary',disabled=not valid or not check['ready'] or state in ('alive','unknown')):
        try:adapter.launch(root,expected_signature=ws.input_signature(m),mode=mode);st.rerun()
        except Exception as exc:st.error(str(exc))
    if state=='alive':
        st.info('正在连续运行。取消与恢复当前只支持已完成的日界，不会自动重跑未提交日。')
        if st.button('请求在当前完整日结束后停止'):
            (Path(root)/'cancel_after_day.request').touch(exist_ok=True);st.rerun()
    elif state=='unknown':st.warning('运行进程身份尚未核实，不会再启动第二个任务。')


def results_panel(root,m):
    root=Path(root);path=root/'run/series_status.json'
    if path.exists():
        status=json.loads(path.read_text(encoding='utf-8'))
        labels={'completed':'所选日期计算完成','active':'运行中','awaiting_bids':'等待下一日报价','cancelled_at_day_boundary':'已在日界停止','failed_uncommitted_day':'当日失败，未形成完整提交'}
        st.info(f"{labels.get(status['status'],status['status'])} · 已完成 {status.get('completed_days',0)} / {len(m['days'])} 天")
        pending=status.get('terminal_pending_obligations',[])
        if pending:
            st.warning('末端仍有待后续实际执行的义务：'+'；'.join(f"{x.get('unit',x.get('unit_id','主体'))} 剩余 {x.get('remaining_h','未知')} 小时" for x in pending))
        st.caption('计算完成不代表全部物理、松弛或求解精度检查通过；请按日期查看诊断。')
    outcome=root/'worker_outcome.json'
    if outcome.exists():
        o=json.loads(outcome.read_text(encoding='utf-8'))
        if o.get('status')=='failed':st.error(o.get('error','运行失败'))
    financial=root/'run/series_financial.json'
    if financial.exists():
        from series_financial_view import render_series_financial
        render_series_financial(json.loads(financial.read_text(encoding='utf-8')))
    records=[r for r in adapter.completed_day_metadata(root.parent) if Path(r['series_root'])==root/'run']
    if records:
        bydate={r['operating_date']:r for r in records}
        d=st.selectbox('查看已完成日期',list(bydate),key='series_result_date')
        st.caption('所选日期的输入、状态、报价与结果绑定同一运行快照。')
        if st.button('打开该日全部阶段、拓扑与主体利润'):
            st.session_state['result_sel']=bydate[d]['dir'];st.switch_page('pages/3_结果.py')
    else:st.info('暂无已完整提交的日结果；未运行日不显示为零。')
    if st.button('刷新任务状态'):st.rerun()
    with st.expander('来源与运行日志'):
        st.write(dict(任务=m['task_id'],修订=m['revision'],基础场景=m['source']['name'],来源指纹=m['source']['hash'],时区=m['timezone']))
        st.download_button('下载任务输入清单',json.dumps(m,ensure_ascii=False,indent=2),file_name='series_manifest.json',mime='application/json')
        p=root/'run.log'
        if p.exists():st.code('\n'.join(p.read_text(errors='replace').splitlines()[-35:]))


def system_panel(root,m,day_date):
    from common import TYPE_CN
    from network_view import render_input_network
    from unit_input_view import resource_capacity_rows,scenario_unit_catalog,render_unit_information
    cfg=adapter.day_config(root,m,day_date)
    st.caption('本次模拟使用的设备与网络；每日风光、负荷和来水在“场景”中设置。')
    render_input_network(cfg,'series_network:'+m['task_id'],structure_only=True)
    with st.expander('各类资源容量'):
        st.dataframe(pd.DataFrame(resource_capacity_rows(cfg)),hide_index=True,width='stretch')
    catalog=scenario_unit_catalog(cfg)
    units={u['id']:(group,u) for group,u in catalog}
    uid=st.selectbox('查看其他机组',list(units),key='series_peer_unit',
        format_func=lambda u:f'{u} · {TYPE_CN.get(units[u][1].get("type",units[u][0]),"机组")}')
    render_unit_information(cfg,uid,compact=True)
    with st.expander('修改设备或高级参数'):
        st.caption('本次模拟保留创建时的场景。修改原场景后，请用侧栏“任务基础输入与修订”新建修订，旧输入与结果保持不变。')
        if st.button('打开原场景参数',key='series_edit_source'):
            source=m['source']['name']
            if source not in sc.list_scenarios():st.error('原场景已不存在；本次模拟的输入副本仍保留。')
            else:
                st.session_state['scenario']=source
                st.session_state['_sel_scenario']=source
                st.switch_page('pages/2_运行.py')


def render_series_workspace():
    tasks=ws.list_tasks(ROOT/'series_tasks')
    if tasks:
        with st.sidebar:create_panel(tasks)
    else:create_panel(tasks)
    for path,m,error in tasks:
        if error:st.warning(f'任务 {path.name} 无法读取，原文件保留：{error}')
    valid={str(root):m for root,m,error in tasks if m is not None}
    if not valid:return
    if st.session_state.get('series_task') not in valid:st.session_state['series_task']=list(valid)[-1]
    root=Path(st.selectbox('当前模拟',list(valid),format_func=lambda p:valid[p]['title']+' · '+valid[p]['start_date'],key='series_task'))
    m=ws.load_task(root);begun=ws._begun(root)
    with st.sidebar:
        with st.expander('任务基础输入与修订'):
            st.caption('本任务使用创建时的基础场景副本。基础场景后来修改，不会自动覆盖本任务或旧结果。')
            if st.button('用当前基础场景新建修订任务'):
                try:
                    child=ws.revise_from_current_base(root,m)
                    st.session_state['series_task']=str(child);st.rerun()
                except Exception as exc:st.error(str(exc))
            st.caption('保留逐日手工场景与报价，但初态需重新核对、每天报价需重新确认；新机组不自动补报价。')
    ready=sum(ws.bid_status(d)=='已确认' for d in m['days'])
    initial='已确认' if m['initial_state'] is not None else '待确认'
    st.caption(f"{m['start_date']} 至 {m['days'][-1]['date']} · {len(m['days'])}天 · 起始状态{initial} · 报价已确认 {ready}/{len(m['days'])}天")
    day_date=st.selectbox('当前编辑日期',[d['date'] for d in m['days']],key='series_edit_date')
    tabs=st.tabs(['场景','机组与报价','全网概况','运行','结果'])
    with tabs[0]:
        day_panel(root,m,begun,day_date)
        with st.expander('起始状态 · '+initial,expanded=False):
            initial_panel(root,m,begun)
    with tabs[1]:bid_panel(root,m,day_date,begun)
    with tabs[2]:system_panel(root,m,day_date)
    with tabs[3]:
        st.caption('多日模拟逐日接续实际状态；这是顺序研究，不是 D−1 市场时序。')
        run_panel(root,m,begun)
    with tabs[4]:results_panel(root,m)
