"""Shared research dashboard widgets; all missing stage/eligibility data stays 未记录."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import plotly.express as px
import streamlit as st
from common import display_frame, df_download, localize_figure, TYPE_CN
from chinese_labels import value_label


def frozen_bids(directory):
    d=Path(directory)
    for filename in ('resolved_bids.json','submitted_bids.json'):
        if (d/filename).exists():return json.loads((d/filename).read_text())
    files=list((d/'scenario_snapshot/bids').rglob('bids_v*.json'))
    if not files:return None
    meta=json.loads((d/'meta.json').read_text()) if (d/'meta.json').exists() else {}
    match=[p for p in files if p.stem==f"bids_v{meta.get('bid_version')}"]
    return json.loads((match[0] if match else max(files,key=lambda p:int(p.stem.split('_v')[-1]))).read_text())


def frozen_units(directory):
    from smm.config import load_config
    snap=Path(directory)/'scenario_snapshot'
    return load_config(root=snap).units if (snap/'config/units.yaml').exists() else {}


def _frame(value):
    return value.copy() if isinstance(value,pd.DataFrame) else pd.DataFrame(value or [])


def unit_dashboard(tables, *, bids=None, settlement=None, prefix='unit', indicative=False, units_config=None):
    units=_frame(tables.get('units'))
    if units.empty:return
    st.subheader('逐主体：报价、运行与电能量收益')
    uid=st.selectbox('查看机组/电站',sorted(units.unit.unique()),key=prefix+':unit')
    u=units[units.unit==uid].sort_values('period').copy()
    storage=_frame(tables.get('storage'));storage=storage[storage.unit==uid] if not storage.empty else storage
    prices=_frame(tables.get('prices'))
    if not prices.empty and 'bus' in u and 'bus' in prices:
        price_columns=[c for c in ('period','bus','lmp','lmp_raw','dispatch_raw_lmp','pricing_source') if c in prices]
        u=u.drop(columns=[c for c in price_columns if c not in ('period','bus') and c in u],errors='ignore').merge(prices[price_columns],on=['period','bus'],how='left')
    if not storage.empty:
        columns=[c for c in ('period','E_mwh','E_start','soc','P_ch','P_dis','u_ch','u_dis') if c in storage]
        u=u.merge(storage[columns],on='period',how='left')
        technical=next((s for s in (units_config or {}).get('storage',[]) if s['id']==uid),None)
        if technical and 'E_mwh' in u:u['soc']=u.E_mwh/float(technical['ecap'])
    for key,idcol in [('pumped','unit'),('hydro','station')]:
        water=_frame(tables.get(key))
        if not water.empty and idcol in water:
            water=water[water[idcol]==uid]
            if not water.empty:
                columns=[c for c in ('period','V_up','V_start','V_1e4m3','P_gen','P_pump','n_gen','n_pump') if c in water]
                u=u.merge(water[columns],on='period',how='left')

    unit_export=u.copy()
    sysdata=_frame(tables.get('system'))
    if 'reserve_down_enabled' in sysdata and not sysdata.empty and sysdata.reserve_down_enabled.notna().all() and sysdata.reserve_down_enabled.eq(False).all():
        u=u.drop(columns=['r_dn'],errors='ignore')
        unit_export['reserve_down_enabled']=False
    c1,c2,c3=st.columns(3)
    c1.metric('主体 / 类型',f"{uid} / {TYPE_CN.get(u.type.iloc[0],u.type.iloc[0]) if 'type' in u else '未记录'}")
    c2.metric('最新净出力 MW',f"{u.P_mw.iloc[-1]:.2f}")
    c3.metric('最新启停/工况',str(u.on.iloc[-1]) if 'on' in u else '未记录')
    tab_bid,tab_state,tab_revenue=st.tabs(['电能量报价','逐时状态','能量结算（非利润）'])
    with tab_bid:
        entry=(bids or {}).get('units',{}).get(uid)
        if entry is None:st.info('未记录：该历史结果未保存可核对的报价副本。')
        else:
            with st.expander('报价原始字段（技术核对）'):
                st.json(entry)
            curves=[]
            for kind in ('segments','dis_segments','ch_segments','gen_segments','pump_segments'):
                for k,segment in enumerate(entry.get(kind) or []):curves.append({'curve':kind,'segment':k+1,**segment})
            if curves:st.dataframe(display_frame(pd.DataFrame(curves)),hide_index=True,width='stretch')
    with tab_state:
        st.plotly_chart(localize_figure(px.line(u,x='period',y=[c for c in ('P_mw',) if c in u],title='该阶段净出力 MW')),width='stretch',key=prefix+':power')
        if 'soc' in u:st.plotly_chart(localize_figure(px.line(u,x='period',y='soc',title='储能荷电状态（比例）')),width='stretch',key=prefix+':soc')
        if 'E_mwh' in u:st.plotly_chart(localize_figure(px.line(u,x='period',y='E_mwh',title='储能能量 MWh')),width='stretch',key=prefix+':energy')
        st.dataframe(display_frame(u),hide_index=True,width='stretch')
        df_download(unit_export, f'{uid}_{"indicative" if indicative else "executed"}.csv', label='下载当前主体筛选CSV', key=prefix+':download')
    with tab_revenue:
        q=_frame(settlement)
        idcol=next((c for c in ('unit','entity') if c in q),None)
        selected=q[q[idcol].astype(str).eq(uid) | q[idcol].astype(str).str.startswith(uid+'_')] if idcol else pd.DataFrame()
        if selected.empty:st.info('电能量结算：未记录（尚未结算或该主体明细未保存）。运行中不把现价×功率冒充完整日收益。')
        else:st.dataframe(display_frame(selected),hide_index=True,width='stretch')

        st.caption('电能量结算是转移支付，不等于物理成本或利润；不含调频、容量电费。')


def stages_dashboard(stages, *, prefix='stages'):
    st.subheader('窗口 × 阶段对照（诊断，不覆盖正式执行）')
    if not stages:
        st.info('未记录：此运行未保存启停预计划完整逐主体快照，不能从最终能量调度反推或冒充。正式结果仍可在上方查看。')
        return
    names={'scuc_preplan':'启停预计划（未直接执行）','agc_auxiliary':'历史调频辅助优化（仅作可行筛选）','energy_sced':'正式能量调度（日前为计划、实时首段执行）','auxiliary_pricing':'辅助定价（数量不执行）'}
    historical='agc_auxiliary' in stages
    if not historical:names.pop('agc_auxiliary')
    else:st.warning('历史调频实验阶段，非当前纯电能量流程。')
    missing=[label for stage,label in names.items() if not stages.get(stage)]
    if missing:st.caption('未启用或未记录阶段（未记录）：'+'、'.join(missing))
    st.dataframe(display_frame(pd.DataFrame([{'stage':names.get(stage,stage),**(data or {}).get('summary',{})} for stage,data in stages.items()])),hide_index=True,width='stretch')
    rows=[]
    for stage,data in stages.items():
        for row in (data or {}).get('units',[]):rows.append({'stage':names.get(stage,stage),**row})
    if not rows:
        with st.expander('原始阶段记录（技术核对）'):
            st.json(stages)
        return
    frame=pd.DataFrame(rows)
    if not historical:frame=frame.drop(columns=['q_reg_mw'],errors='ignore')
    uid=st.selectbox('阶段对照主体',sorted(frame.unit.unique()),key=prefix+':unit')
    chosen=frame[frame.unit==uid]
    st.caption('正式能量调度固定启停计划的整数决策；连续变量可因退化或限时近优方案变化。启用辅助定价时，数量仍来自正式调度，价格来自独立辅助模型，不能混为同一目标函数。' if not historical else '历史实验辅助LP不执行；此三阶段不属于当前纯电能量范围。')
    st.dataframe(display_frame(chosen),hide_index=True,width='stretch')
    if 'P_mw' in chosen:
        st.plotly_chart(localize_figure(px.line(chosen,x='period',y='P_mw',color='stage',markers=True,title='正式调度与辅助模型的出力对照（辅助数量不执行）')),width='stretch',key=prefix+':plot')
    df_download(chosen, f'{uid}_stage_diagnostics.csv', label='下载当前阶段对照CSV', key=prefix+':download')
    with st.expander('整数决策与原始诊断（技术核验）'):
        stage_name=st.selectbox('整数决策阶段',list(stages),format_func=lambda x:names.get(x,x),key=prefix+':integer_stage')
        integers=(stages[stage_name] or {}).get('integer_values')
        if integers is None:st.info('未记录：此旧样例未保存全部整数变量，不能由启停摘要重建。')
        else:
            integer_table=pd.DataFrame(integers)
            if 'index' in integer_table:integer_table['index']=integer_table['index'].map(lambda x:json.dumps(x,ensure_ascii=False))
            st.dataframe(display_frame(integer_table),hide_index=True,width='stretch')
        st.download_button('下载原字段阶段诊断文件（技术用途）',json.dumps(stages,ensure_ascii=False,default=str).encode('utf-8'),
                           file_name='stage_diagnostics.json',mime='application/json',key=prefix+':json')



def system_metrics(tables):
    system=_frame(tables.get('system'));units=_frame(tables.get('units'))
    if units.empty:return
    period=int(units.period.max());last=units[units.period==period]
    row=system[system.period==period].iloc[0] if not system.empty and 'period' in system else None
    c=st.columns(4)
    c[0].metric('所选结果末时段',period)
    c[1].metric('负荷 MW',f"{row['load_mw']:.2f}" if row is not None and 'load_mw' in row else '未记录')
    power=pd.to_numeric(last.P_mw,errors='coerce').replace([float('inf'),-float('inf')],float('nan'))
    complete=power.notna().all() and len(last)==units.unit.nunique() and not last.unit.duplicated().any() if 'unit' in units else power.notna().all()
    has_tie='type' in last and last.type.eq('tie').any()
    label='已保存净供给（含来电） MW' if has_tie else '已保存主体净出力 MW'
    c[2].metric(label,f'{power.sum():.2f}' if complete else '未记录完整')
    prices=_frame(tables.get('prices'));last_price=prices[prices.period==period] if not prices.empty else prices
    c[3].metric('节点算术平均结算价 元/MWh',f'{last_price.lmp.mean():.2f}' if not last_price.empty else '未记录')
    st.caption('仅合计所选末时段保存的主体出力，储能充电/抽蓄抽水为负。'
               + ('已包含保存的区外来电，不能再次相加；区外来电不是本地报价机组。' if has_tie else
                  '该时段未记录区外来电行，不能据此推断来电为零或宣称全网供给完整。'))


def hydro_window_dashboard(history, *, prefix='hydro_window'):
    """Display solved lookahead targets, never label their endpoints as execution."""
    st.subheader('水电窗口末库容目标：前瞻诊断（按记录区分软/硬）')
    if not history:
        st.info('未记录：该结果未保存水电目标窗口诊断；旧固定日前结果不据此反推新策略目标。')
        return
    rows=[row for entries in history.values() for row in entries]
    frame=pd.DataFrame(rows).rename(columns={
        'station':'unit','start_volume_1e4m3':'current_V_1e4m3','start_power_mw':'current_P_mw',
        'terminal_target_1e4m3':'target_V_1e4m3','terminal_actual_1e4m3':'actual_terminal_V_1e4m3',
        'station_ramp_excess_max_mw':'ramp_violation_max_mw'})
    if frame.empty:return
    # The checks CSV reports absolute error; render a separately calculated signed
    # difference without mislabeling that field or changing the source evidence.
    frame['signed_terminal_error_1e4m3']=frame.actual_terminal_V_1e4m3-frame.target_V_1e4m3
    uid=st.selectbox('水电诊断电站',sorted(frame.unit.unique()),key=prefix+':station')
    selected=frame[frame.unit==uid].sort_values('window_start')
    starts=list(selected.window_start.astype(int))
    start=st.selectbox('水电窗口起点',starts,index=len(starts)-1,key=prefix+':start')
    row=selected[selected.window_start==start].iloc[-1]
    kind = row.get('terminal_kind')
    soft = kind == 'soft_da_window_end'
    hard = kind in ('hard_da_window_end', 'declared_day_end', 'hard_declared_day_end')
    st.caption('下面的“计划末值”属于前瞻窗口末，绝不是当前已执行库容。')
    if soft:
        st.info('记录为带罚软跟踪目标：非零目标偏差不等于物理约束违规；跟踪罚值是窗口调度目标项，不是现金结算或运营成本。')
    elif hard:
        st.caption('记录为硬终端目标：偏差需满足求解容差。')
    else:
        st.caption('此历史记录未明确保存软/硬目标类型；仅展示偏差，不推断规则或把未知目标认证为可行。')
    metrics=st.columns(3)
    metrics[0].metric('窗口起点实际库容 万m³',f"{row['current_V_1e4m3']:.6f}")
    metrics[1].metric('窗口前已执行功率 MW',f"{row['current_P_mw']:.6f}")
    metrics[2].metric('窗口时段范围（不含结束边界）',f"{int(start)} → {int(row['window_end_exclusive'])}")
    metrics=st.columns(3)
    fmt=lambda value: '未记录' if pd.isna(value) else f'{float(value):.6f}'
    metrics[0].metric('日前同钟点目标 万m³',fmt(row['target_V_1e4m3']))
    metrics[1].metric('求解计划末库容 万m³',f"{row['actual_terminal_V_1e4m3']:.6f}")
    metrics[2].metric('计划末值−目标 万m³',fmt(row['signed_terminal_error_1e4m3']))
    if pd.isna(row['target_V_1e4m3']):st.info('此记录未启用窗口末硬目标（例如历史执行日前计划对照）；目标与偏差按未记录展示，不当作硬目标验证。')
    if hard and pd.notna(row['terminal_error_1e4m3']) and abs(row['terminal_error_1e4m3'])>1e-5:
        st.error('水电硬末目标诊断偏差超出1e-5万m³显示阈值，请检查求解与数据，不得当作软松弛接受。')
    if soft and pd.notna(row.get('tracking_equation_error_1e4m3')) and row.get('tracking_equation_error_1e4m3')>1e-5:
        st.error('软跟踪偏差与松弛量的等式校核不一致；这不是允许的普通目标偏差。')
    if row.get('ramp_violation_max_mw',0)>1e-5:
        st.error('聚合端点爬坡诊断存在超限；此表不是逐台动态可行性证明。')
    st.dataframe(display_frame(selected),hide_index=True,width='stretch')
    df_download(selected, f'{uid}_hydro_window_targets.csv', label='下载所选电站水电窗口诊断CSV', key=prefix+':download')


def reserve_dashboard(tables, *, prefix='reserve', indicative=False):
    """Display recorded awards only; missing historical measurements stay missing."""
    st.subheader('能量出清安全备用（非独立交易市场）')
    st.caption('这里是电能量模型的安全备用约束，不是新增备用交易或调频市场；不新增备用报价或付款。'
               + ('日前数据为计划。' if indicative else '实时数据只含各滚动窗口实际执行的首时段；不使用前瞻窗口最大短缺替代首段。'))
    system=_frame(tables.get('system'))
    down_flag=next((c for c in ('reserve_down_enabled','down_enabled') if c in system),None)
    down_disabled=bool(down_flag and not system.empty and system[down_flag].notna().all() and system[down_flag].eq(False).all())
    if down_disabled:
        st.info('本场景仅安排上备用：下备用需求与分配已停用。物理荷电状态/库容上下界仍需满足；具体备用筛选口径以保存的记录为准，不代表下备用安排。')
    names={'R_up_req':'上备用需求 MW','R_dn_req':'下备用需求 MW',
           'R_up_supply':'上备用分配 MW','R_dn_supply':'下备用分配 MW',
           'R_up_short':'上备用短缺 MW','R_dn_short':'下备用短缺 MW',
           'R_up_re_credit_mw':'新能源上备用折算 MW','R_up_balance_mw':'上备用供需残差 MW'}
    if down_disabled:names={c:label for c,label in names.items() if not c.startswith('R_dn')}
    available=[c for c in names if c in system and system[c].notna().any()]
    missing=[label for c,label in names.items() if c not in available]
    if missing:st.info('未记录：'+'、'.join(missing)+'。不以0代替缺失数据。')
    if available and 'period' in system:
        selected=system[['period',*available]].sort_values('period').rename(columns=names)
        st.plotly_chart(localize_figure(px.line(selected,x='period',y=list(selected.columns[1:]),title='系统安全备用：需求、分配与短缺（MW）')),width='stretch',key=prefix+':systemplot')
        st.dataframe(display_frame(selected),hide_index=True,width='stretch')
        df_download(system[[c for c in system if c=='period' or c.startswith('R_') or c in ('reserve_down_enabled','down_enabled')]], 'safety_reserve_system.csv', label='下载系统安全备用CSV', key=prefix+':systemcsv')
    units=_frame(tables.get('units'))
    if units.empty or 'unit' not in units:
        st.info('逐主体安全备用：未记录。');return
    uid=st.selectbox('安全备用主体',sorted(units.unit.unique()),key=prefix+':unit')
    selected=units[units.unit==uid].sort_values('period')
    columns=[c for c in (('r_up',) if down_disabled else ('r_up','r_dn')) if c in selected and selected[c].notna().any()]
    if not columns:
        st.info('该主体上/下备用未记录；不能从能量出力或装机容量反推实际备用。')
        return
    incremental=('reserve_formulation' in selected and selected.reserve_formulation.notna().all()
                 and selected.reserve_formulation.eq('incremental_current_stock').all())
    display=selected[[c for c in ('period','unit','type','P_mw','on','reserve_formulation','reserve_eligible','reserve_qualified',*columns) if c in selected]].copy()
    st.plotly_chart(localize_figure(px.line(display,x='period',y=columns,title=f'{uid} 安排的备用容量 MW')),width='stretch',key=prefix+':unitplot')
    st.caption(('本场景仅展示r_up上备用；r_dn已停用。' if down_disabled else '')+'上备用／下备用是优化安排的预留容量，不代表已发生备用激活。设计资格为工况允许参与；记录的资格不等于供给容量。缺失值保持空缺，备用未设独立采购成本，供给约束为≥需求，可能存在超配/退化；不自动证明全网络可交付。')
    if incremental:
        st.caption('本结果采用当前时段初 SOC/库容（首段为实际初态）及功率余量的增量备用筛选，不新增备用资格整数；记录的参与资格仅由配置与工况推导，不是决策变量。此筛选不代表基准出力＋备用在全持续时长内可交付。')
    else:
        st.caption('历史或未标明口径的结果按保存字段展示，不套用当前增量筛选解释。历史资格为1也可能分配0 MW，不能当作供给。')
    if incremental:
        display=display.rename(columns={'reserve_qualified':'由配置与工况推导的备用资格'})
    st.dataframe(display_frame(display),hide_index=True,width='stretch')
    export_awards=selected[[c for c in ('period','unit','type','P_mw','on','reserve_formulation','reserve_eligible','reserve_qualified','r_up','r_dn') if c in selected]].copy()
    if down_flag:export_awards=export_awards.merge(system[['period',down_flag]],on='period',how='left')
    df_download(export_awards, f'{uid}_safety_reserve.csv', label='下载所选主体安全备用CSV', key=prefix+':unitcsv')

    resource_kind=selected['type'].iloc[0] if 'type' in selected else None
    if resource_kind not in ('storage','pumped'):return
    st.subheader('储能/抽蓄备用筛选诊断（不是已激活轨迹）')
    if resource_kind=='storage':
        st.caption('本研究电池采用理想逆变器响应：备用激活可跨充电/放电或从空闲响应。持续时长与能力余量按保存的诊断值展示；不把它解释为实际发生的充放电切换。')
    else:
        st.caption('本研究抽蓄只计发电工况的连续备用；定速抽水及空闲不计备用。不是把电池跨工况响应假设套用于抽蓄。')
    ability=_frame(tables.get(resource_kind))
    if ability.empty or 'unit' not in ability:
        st.info('备用能力诊断未记录，不能据此宣称1小时持续激活可行。');return
    ability=ability[ability.unit==uid].sort_values('period').copy()
    if 'reserve_qualified' not in ability and 'reserve_qualified' in selected:
        ability=ability.merge(selected[['period','reserve_qualified']],on='period',how='left')
    if not incremental and 'reserve_qualified' in ability:
        # Qualification-off means the conditional duration guard is inactive,
        # not a violated or zero-margin one-hour constraint.
        duration_columns=[c for c in ability if c.startswith('reserve_duration_')]
        ability.loc[ability.reserve_qualified.eq(0),duration_columns]=float('nan')
    fields=['T_dur_h','reserve_power_up_margin_mw','reserve_power_down_margin_mw',
            'reserve_duration_up_margin_mwh','reserve_duration_down_margin_mwh',
            'reserve_duration_up_margin_1e4m3','reserve_duration_down_margin_1e4m3',
            'reserve_duration_lower_margin_mwh','reserve_duration_upper_margin_mwh',
            'reserve_duration_lower_margin_1e4m3','reserve_duration_upper_margin_1e4m3']
    fields=[c for c in fields if not (resource_kind=='storage' and c.endswith('_1e4m3')) and not (resource_kind=='pumped' and c.endswith('_mwh'))]
    recorded=[c for c in fields if c in ability and ability[c].notna().any()]
    if not recorded:
        st.info('备用能力诊断未记录；未启用的方向不按0余量显示。');return
    keep=[c for c in ('period','unit','mode','reserve_formulation','reserve_eligible','reserve_qualified','E_start','E_mwh','V_start','V_up',*fields) if c in ability]
    ability=ability[keep]
    export_ability=ability.copy()
    if down_flag:export_ability=export_ability.merge(system[['period',down_flag]],on='period',how='left')
    if down_disabled and not incremental:
        # Lower/upper are BOTH physical bounds for the up-activation Pu=P+ru;
        # they are not up/down reserve products. Prefer unambiguous new names.
        old_duration=[c for c in ability if c.startswith('reserve_duration_up_') and any(x.startswith('reserve_duration_lower_') for x in ability)]
        ability=ability.drop(columns=[c for c in ability if '_down_' in c]+old_duration)
        st.caption('上备用持续激活功率＝净出力＋上备用：下界／上界 分别是同一次上备用激活的能量/库容下界与上界余量，两侧都要核验，不是下备用商品。')
    if incremental:
        # Lower/upper are compatibility aliases for incremental up/down margins.
        ability=ability.drop(columns=[c for c in ability if c.startswith(('reserve_duration_lower_', 'reserve_duration_upper_'))
                                      or (down_disabled and '_down_' in c)])
        labels={c:c.replace('reserve_duration_up_margin_', '增量上备用库存余量 ').replace('reserve_duration_down_margin_', '增量下备用库存余量 ').replace('mwh','MWh').replace('1e4m3','万m³')
                for c in ability if c.startswith('reserve_duration_')}
        ability=ability.rename(columns={**labels,'T_dur_h':'增量备用筛选时长 小时',
                                        'reserve_qualified':'由配置与工况推导的备用资格'})
        st.caption('增量库存余量只核验当前时段初库存对新增备用的支撑；不包含基准出力在全持续时长内的库存消耗，不保证基准出力＋备用持续可交付。')
    else:
        st.caption('历史资格为0时持续能力约束未参与，显示未记录（表中空值）；历史下界／上界字段保留其原有激活口径。')
    st.caption('筛选时长单位小时；功率余量单位 MW；电池能量余量 MWh、抽蓄库容余量万m³，不混合相加。空值表示未启用或未记录，并非0；非负余量只验证所列能力约束，不证明全网络或动态可交付。')
    st.dataframe(display_frame(ability),hide_index=True,width='stretch')
    df_download(export_ability, f'{uid}_reserve_capability.csv', label='下载所选主体备用能力诊断CSV', key=prefix+':capabilitycsv')


def operating_profit_dashboard(by_unit, by_period=None, *, prefix='operating_profit'):
    """Display independent fixed-cost accounting, never infer cost from bids."""
    st.subheader('逐主体模拟运营利润（不是企业净利润）')
    st.caption('固定单位成本 × 实际执行量，加在线和启动费用；报价与核算成本分离。利润不含资本、融资、税费。')
    with st.expander('核算口径与购买电量说明'):
        st.caption('固定的是单位成本参数，不是全天总费用。利润 = 净能量现金结算 − 可核算运营成本；'
                   '不计调度松弛罚值、机会成本、资本性支出、融资或税费。充电/抽水结算已在净结算内，不再重复扣减；'
                   '负电价下该项也可能为收入。库存变化单列且不货币化。')
    rows=_frame(by_unit)
    if rows.empty:
        st.info('未记录：此运行未保存独立固定成本核算结果；不能拿报价成本代替实际运营成本，不能反推利润。')
        return
    names={'unit':'主体','type':'类型','da_cash_revenue_yuan':'日前现金结算 元',
           'rt_deviation_cash_revenue_yuan':'实时偏差现金结算 元','cash_revenue_yuan':'净能量现金结算 元',
           'charging_settlement_yuan':'其中充电/抽水结算 元（已含）','variable_cost_yuan':'可变运营成本 元',
           'fixed_online_cost_yuan':'在线固定成本 元','startup_cost_yuan':'启动成本 元',
           'cash_operating_cost_yuan':'现金运营成本 元','simulated_operating_profit_yuan':'模拟运营利润 元',
           'cost_available':'成本参数完整','cost_missing_reason':'成本缺项原因',
           'inventory_start':'期初库存','inventory_end':'期末库存','inventory_change':'库存变化','inventory_unit':'库存单位'}
    # Incomplete evidence cannot turn into apparently numeric profits, even for legacy exports.
    monetary_costs=['variable_cost_yuan','fixed_online_cost_yuan','startup_cost_yuan',
                    'cash_operating_cost_yuan','simulated_operating_profit_yuan']
    complete = rows.get('cost_available',pd.Series(False,index=rows.index)).eq(True)
    rows.loc[~complete,[c for c in monetary_costs if c in rows]]=float('nan')
    if not complete.all():st.warning('存在缺失成本参数：对应成本与利润保持 未记录，不按零成本估算；现金结算仍可查看。')
    compact=[c for c in ('unit','type','cash_revenue_yuan','cash_operating_cost_yuan',
                             'simulated_operating_profit_yuan','cost_available','cost_missing_reason') if c in rows]
    st.dataframe(display_frame(rows[compact].rename(columns=names)),hide_index=True,width='stretch')
    if 'simulated_operating_profit_yuan' in rows:
        visible=rows[rows.simulated_operating_profit_yuan.notna()].copy()
        if len(visible):
            visible['模拟运营利润 · 万元']=visible.simulated_operating_profit_yuan/1e4
            visible['盈亏']=visible.simulated_operating_profit_yuan.map(lambda v:'亏损' if v<0 else '非负')
            fig=px.bar(visible,x='模拟运营利润 · 万元',y='unit',color='盈亏',orientation='h',
                       color_discrete_map={'亏损':'#b13e53','非负':'#168178'},title='逐主体运营盈亏 · 负值完整保留',
                       labels={'unit':'主体'})
            fig.update_layout(height=max(320,28*len(visible)),legend_title_text='收益符号（非调度阶段）')
            st.plotly_chart(localize_figure(fig),width='stretch',key=prefix+':profitplot')
    uid=st.selectbox('运营利润明细主体',list(rows.unit),key=prefix+':unit')
    selected_unit=rows[rows.unit==uid].iloc[0]
    def amount(key):
        value=selected_unit.get(key)
        return '未记录' if pd.isna(value) else f'{float(value)/1e4:,.2f}'
    cols=st.columns(4)
    for col,key,label in zip(cols,('da_cash_revenue_yuan','rt_deviation_cash_revenue_yuan','cash_operating_cost_yuan','simulated_operating_profit_yuan'),
                             ('日前现金结算 · 万元','实时偏差结算 · 万元','现金运营成本 · 万元','模拟运营利润 · 万元')):
        col.metric(label,amount(key))
    with st.expander('成本分项、购买电量与库存 · 所选主体',expanded=True):
        component=[c for c in names if c in rows]
        st.dataframe(display_frame(rows[rows.unit==uid][component].rename(columns=names)),hide_index=True,width='stretch')
        st.caption('购买电量列是净结算的组成项，不是再次计入运营成本的费用。库存变化使用库存单位列，非现金利润调整。')
    with st.expander('完整核算证据与下载'):
        st.dataframe(display_frame(rows.rename(columns=names)),hide_index=True,width='stretch')
        df_download(rows, 'operating_profit_by_unit.csv', label='下载逐主体运营利润CSV', key=prefix+':download')
        detail=_frame(by_period)
        if not detail.empty and 'unit' in detail:
            selected=detail[detail.unit==uid].copy()
            valid=selected.get('cost_available',pd.Series(False,index=selected.index)).eq(True)
            selected.loc[~valid,[c for c in monetary_costs if c in selected]]=float('nan')
            st.dataframe(display_frame(selected.rename(columns=names)),hide_index=True,width='stretch')
            df_download(selected, f'{uid}_operating_profit_by_period.csv', label='下载所选主体逐时核算CSV', key=prefix+':perioddownload')


def _saved_json(path):
    """Missing, partial or legacy evidence is unknown, never a passing check."""
    try:
        value=json.loads(Path(path).read_text())
        return value if isinstance(value,dict) else {}
    except (OSError, ValueError):
        return {}


def _saved_csv(path):
    try:return pd.read_csv(path)
    except (OSError, ValueError, pd.errors.ParserError):return pd.DataFrame()


def _quality_number(value):
    import math
    try:
        if isinstance(value,bool):return None
        result=float(value)
        return result if math.isfinite(result) else None
    except (TypeError,ValueError):return None


def result_quality_messages(summary, da_summary, *, directory, live=None):
    """Read saved formal dispatch evidence; do not recalculate or certify safety.

    RT reserve first-period shortages are separate from lookahead window maxima.
    Targets come ONLY from this run's frozen inputs, never today's defaults.
    """
    import yaml
    summary=summary or {};da_summary=da_summary or {};live=live or {}
    d=Path(directory);checks=summary.get('checks') or {};messages=[]
    def add(level,text):messages.append((level,text))
    base={};modes={}
    try:
        base=yaml.safe_load((d/'scenario_snapshot/config/base.yaml').read_text()) or {}
        modes=yaml.safe_load((d/'scenario_snapshot/config/modes.yaml').read_text()) or {}
    except (OSError,ValueError,yaml.YAMLError):pass
    base=base if isinstance(base,dict) else {};modes=modes if isinstance(modes,dict) else {}
    mode=modes.get(base.get('rule_set'),{}) or {}
    da_target=_quality_number((base.get('solver') or {}).get('mip_rel_gap'))
    rt_target=_quality_number((mode.get('realtime') or {}).get('mip_rel_gap'))
    diagnostics=_saved_json(d/'stage_diagnostics.json')
    da_stage=((diagnostics.get('da') or live.get('da_stages') or {}).get('scuc_preplan') or {}).get('summary') or {}
    gap=next((n for n in [_quality_number(da_summary.get('scuc_gap')),
                         _quality_number(da_stage.get('gap')),
                         _quality_number((summary.get('da') or {}).get('gap'))] if n is not None),None)
    termination=da_stage.get('termination')
    if gap is not None:
        suffix='；已达到计算时限' if termination=='maxTimeLimit' else ''
        if da_target is None:
            add('warning' if termination=='maxTimeLimit' else 'info',f'日前求解间隙 {gap:.3%}；冻结目标精度未记录，不能判断是否达标{suffix}。运行完成不等于全局最优。')
        elif gap>da_target+1e-8:
            add('warning',f'日前求解间隙 {gap:.3%}，超过本次冻结目标 {da_target:.3%}{suffix}；尚未达到目标精度。结果可供研究，不代表精度验收通过。')
        else:add('caption',f'日前求解间隙 {gap:.3%}，在本次冻结目标 {da_target:.3%} 内；不等于全局最优。')
    else:add('info','求解质量：日前混合整数相对间隙未记录，不能判断目标精度是否达到。')
    windows=diagnostics.get('rt_windows') or []
    rt_gaps=[_quality_number(((w.get('stages') or {}).get('scuc_preplan') or {}).get('summary',{}).get('gap')) for w in windows if isinstance(w,dict)]
    known=[g for g in rt_gaps if g is not None]
    if known and rt_target is not None:
        over=sum(g>rt_target+1e-8 for g in known)
        if over:add('warning',f'实时滚动求解有 {over}/{len(rt_gaps)} 个已记录窗口的间隙超过冻结目标 {rt_target:.3%}，最大 {max(known):.3%}；这是窗口求解精度，不是已执行物理偏差。')
        else:add('caption',f'实时滚动已记录 {len(known)}/{len(rt_gaps)} 个窗口间隙，最大 {max(known):.3%}，均在冻结目标 {rt_target:.3%} 内。')
    if rt_gaps and len(known)<len(rt_gaps):add('info','部分实时窗口缺少有限间隙记录，不能据其余窗口认证全部求解精度。')
    tol=_quality_number(checks.get('safety_tolerance_mw'))
    tol=tol if tol is not None and tol>=0 else 1e-5
    for stage,label in [('da','日前正式调度'),('rt','实时已执行')]:
        value=_quality_number(checks.get(stage+'_line_overload_max_mw'))
        count=checks.get(stage+'_line_overload_count','未记录')
        if value is None:
            # Compare only saved dispatch flow and saved limits, never current inputs.
            candidates=[d/'rt_lines.csv'] if stage=='rt' else [d/'da/da_line_flows.csv',d/'da_line_flows.csv']
            line_table=next((frame for frame in (_saved_csv(p) for p in candidates) if not frame.empty),pd.DataFrame())
            flow=next((c for c in ('flow_mw','flow') if c in line_table),None)
            rate=next((c for c in ('rate_mw','rate_eff') if c in line_table),None)
            if flow and rate:
                excess=pd.to_numeric(line_table[flow],errors='coerce').abs()-pd.to_numeric(line_table[rate],errors='coerce')
                value=_quality_number(excess.max());count=int((excess>tol).sum())
        if value is not None and value>tol:
            detail=''
            if stage=='rt':
                overloads=_saved_csv(d/'executed_line_overloads.csv')
                if {'line','period','overload_mw'}<=set(overloads):
                    flagged=overloads[pd.to_numeric(overloads.overload_mw,errors='coerce')>tol]
                    detail='；'+ '、'.join(f'{row.line}（时段序号 {row.period}）' for row in flagged.head(5).itertuples()) if not flagged.empty else ''
            add('error',f'{label}线路越限：最大 {value:.4f} MW，越限记录 {count} 条{detail}。线路软罚允许求解完成，但不能解释为网络物理可行。')
    if checks.get('network_feasible') is False and not any('线路越限：' in text for _,text in messages):
        add('error','已保存线路潮流检查未通过；越限幅度未记录，不能按零处理。')
    reserve=summary.get('reserve') or {}
    reserve_known=False
    for stage,label in [('da','日前正式调度'),('rt','实时已执行首时段')]:
        table=_saved_csv(d/f'{stage}_reserve_system.csv')
        if table.empty and stage=='rt':table=_frame((live.get('executed') or {}).get('system'))
        for direction,word in [('up','上'),('down','下')]:
            key='R_up_short' if direction=='up' else 'R_dn_short'
            values=pd.to_numeric(table[key],errors='coerce') if key in table else pd.Series(dtype=float)
            value=_quality_number(reserve.get(f'rt_executed_reserve_{direction}_short_max_mw')) if stage=='rt' else _quality_number((da_summary.get('slack') or {}).get('reserve_up_short_max' if direction=='up' else 'reserve_dn_short_max'))
            if value is None and values.notna().any():value=_quality_number(values.max())
            reserve_known=reserve_known or value is not None
            if value is not None and value>tol:
                periods=table.loc[values>tol,'period'].tolist() if 'period' in table and len(values) else []
                detail='；时段序号 '+ '、'.join(str(x) for x in periods[:8])+(' 等' if len(periods)>8 else '') if periods else ''
                add('error',f'{label}{word}备用缺额：最大 {value:.4f} MW{detail}。需求未全部满足，不以窗口前瞻最大值代替已执行缺额，也不是已发生备用激活。')
    if not reserve_known:add('info','安全备用缺额：未保存可用的正式需求缺额记录，不能按零或全部满足处理。')
    if checks.get('physical_feasible_checked_constraints') is False:
        add('error','已检查物理约束未全部通过：不能作为安全运行方案。运行完成仅表示计算流程结束，结算完成也不代表物理可行。')
    elif checks.get('physical_feasible_checked_constraints') is True:
        add('caption','已报告的物理检查通过；仅限保存的检查范围，不是所有动态/安全约束的认证。')
    else:add('info','物理检查完整性：未保存汇总认证，不能将缺失检查视为通过。')
    if checks.get('storage_pumped_hard_anchors_active'):
        verified = checks.get('storage_pumped_hard_anchors_satisfied')
        if verified is True:
            add('info', f"电池/抽蓄每2小时硬锚点已校核：已执行端点记录 {checks.get('hard_anchor_executed_count', 0)} 条；电池最大误差 {checks.get('storage_hard_anchor_max_err_mwh', 0):.3g} MWh，抽蓄 {checks.get('pumped_hard_anchor_max_err_1e4m3', 0):.3g} 万m³。仅覆盖已保存运行范围。")
        else:
            add('error', '电池/抽蓄每2小时硬锚点未通过或证据缺失；不能以零松弛宣称硬一致。')
    tracking=[]
    for key,label,unit in [('storage_anchor_slack_max_mwh','电池','MWh'),('pumped_anchor_slack_max_1e4m3','抽蓄','万m³'),('hydro_anchor_slack_max_1e4m3','水电','万m³')]:
        value=_quality_number(checks.get(key,(summary.get('realtime') or {}).get(key)))
        if value is not None and value>tol:tracking.append(f'{label} {value:.4f} {unit}')
    if tracking:add('info','另列窗口软跟踪偏差：'+ '；'.join(tracking)+'。这些是窗口计划软目标的偏差，软目标偏差不等于物理违规，不与线路越限或备用缺额合并；不同单位不相加。')
    elif checks.get('rules_satisfied_no_slack') is False:
        add('info','存在调度松弛或软跟踪偏差记录；软目标偏差不等于物理违规。具体类型或分单位幅度未完整记录，请查看原始诊断。')
    return messages


def result_health_dashboard(summary, da_summary, *, directory, live=None, messages=None):
    """Prominent read-only quality status, shared by run and result pages."""
    summary=summary or {};da_summary=da_summary or {}
    expected=summary.get('expected_periods');executed=summary.get('executed_periods',summary.get('periods'))
    scope=summary.get('completed_scope')
    if scope=='full_day' and expected is not None and executed==expected:
        st.caption(f"运行范围：已完成 {executed}/{expected} 时段 · 全天结算 · {summary.get('date','日期未记录')}")
    elif executed is not None:
        st.info(f"运行范围：仅展示已保存的 {executed} 时段；未据此认证完整日，金额均按该范围解释。")
    st.subheader('结果质量与未满足项')
    st.caption('正式调度数量与求解精度分开核对；完成、结算、精度达标和物理可行不是同一结论。')
    for level,message in (result_quality_messages(summary,da_summary,directory=directory,live=live) if messages is None else messages):
        getattr(st,level)(message)
    profit=summary.get('operating_profit',{})
    if profit and not profit.get('all_unit_costs_available',False):
        st.warning('原始结果中部分主体缺失独立成本参数，原始利润未记录。可在结算与收益中单独补算，不按零成本推断。')
    if profit.get('cost_profile_available'):
        st.caption('成本口径：冻结的模拟研究参数，非实测电厂成本；利润为现金运营利润，排除资本、融资、税费。')
