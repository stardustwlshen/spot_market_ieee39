"""Shared research dashboard widgets; all missing stage/eligibility data stays N/A."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import plotly.express as px
import streamlit as st


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
        u=u.merge(prices[['period','bus','lmp','lmp_raw']],on=['period','bus'],how='left')
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
    c1.metric('主体 / 类型',f"{uid} / {u.type.iloc[0] if 'type' in u else 'N/A'}")
    c2.metric('最新净出力 MW',f"{u.P_mw.iloc[-1]:.2f}")
    c3.metric('最新启停/工况',str(u.on.iloc[-1]) if 'on' in u else 'N/A')
    tab_bid,tab_state,tab_revenue=st.tabs(['电能量报价','逐时状态','能量结算（非利润）'])
    with tab_bid:
        entry=(bids or {}).get('units',{}).get(uid)
        if entry is None:st.info('N/A：该历史结果未保存可核对的报价副本。')
        else:
            st.json(entry)
            curves=[]
            for kind in ('segments','dis_segments','ch_segments','gen_segments','pump_segments'):
                for k,segment in enumerate(entry.get(kind) or []):curves.append({'curve':kind,'segment':k+1,**segment})
            if curves:st.dataframe(pd.DataFrame(curves),hide_index=True,width='stretch')
    with tab_state:
        st.plotly_chart(px.line(u,x='period',y=[c for c in ('P_mw',) if c in u],title='该阶段净出力 MW'),width='stretch',key=prefix+':power')
        if 'soc' in u:st.plotly_chart(px.line(u,x='period',y='soc',title='储能SOC（比例）'),width='stretch',key=prefix+':soc')
        if 'E_mwh' in u:st.plotly_chart(px.line(u,x='period',y='E_mwh',title='储能能量 MWh'),width='stretch',key=prefix+':energy')
        st.dataframe(u,hide_index=True,width='stretch')
        st.download_button('下载当前主体筛选CSV',unit_export.to_csv(index=False).encode('utf-8-sig'),file_name=f'{uid}_{"indicative" if indicative else "executed"}.csv',key=prefix+':download')
    with tab_revenue:
        q=_frame(settlement)
        idcol=next((c for c in ('unit','entity') if c in q),None)
        selected=q[q[idcol].astype(str).eq(uid) | q[idcol].astype(str).str.startswith(uid+'_')] if idcol else pd.DataFrame()
        if selected.empty:st.info('电能量结算：N/A（尚未结算或该主体明细未保存）。运行中不把现价×功率冒充完整日收益。')
        else:st.dataframe(selected,hide_index=True,width='stretch')

        st.caption('电能量结算是转移支付，不等于物理成本或利润；不含调频、容量电费。')


def stages_dashboard(stages, *, prefix='stages'):
    st.subheader('窗口 × 阶段对照（诊断，不覆盖正式执行）')
    if not stages:
        st.info('N/A：此运行未保存SCUC预计划完整逐主体快照，不能从最终SCED反推或冒充。正式结果仍可在上方查看。')
        return
    names={'scuc_preplan':'SCUC预计划（未直接执行）','agc_auxiliary':'调频辅助LP（能量仅用于可行筛选）','energy_sced':'正式能量SCED（DA为计划/RT首段执行）'}
    historical='agc_auxiliary' in stages
    if not historical:names.pop('agc_auxiliary')
    else:st.warning('历史调频实验阶段，非当前纯电能量流程。')
    missing=[label for stage,label in names.items() if not stages.get(stage)]
    if missing:st.caption('未启用或未记录阶段（N/A）：'+'、'.join(missing))
    st.dataframe(pd.DataFrame([{'stage':names.get(stage,stage),**(data or {}).get('summary',{})} for stage,data in stages.items()]),hide_index=True,width='stretch')
    rows=[]
    for stage,data in stages.items():
        for row in (data or {}).get('units',[]):rows.append({'stage':names.get(stage,stage),**row})
    if not rows:
        st.json(stages);return
    frame=pd.DataFrame(rows)
    if not historical:frame=frame.drop(columns=['q_reg_mw'],errors='ignore')
    uid=st.selectbox('阶段对照主体',sorted(frame.unit.unique()),key=prefix+':unit')
    chosen=frame[frame.unit==uid]
    st.caption('当前纯电能量SCED固定SCUC整数，沿用相同目标、数据与约束；连续变量可因退化或限时近优方案而变化，正式量价同源。' if not historical else '历史实验辅助LP不执行；此三阶段不属于当前纯电能量范围。')
    st.dataframe(chosen,hide_index=True,width='stretch')
    if 'P_mw' in chosen:
        st.plotly_chart(px.line(chosen,x='period',y='P_mw',color='stage',markers=True),width='stretch',key=prefix+':plot')
    st.download_button('下载当前阶段对照CSV',chosen.to_csv(index=False).encode('utf-8-sig'),file_name=f'{uid}_stage_diagnostics.csv',key=prefix+':download')
    with st.expander('完整整数决策与诊断JSON（用于重建核验）'):
        stage_name=st.selectbox('整数决策阶段',list(stages),format_func=lambda x:names.get(x,x),key=prefix+':integer_stage')
        integers=(stages[stage_name] or {}).get('integer_values')
        if integers is None:st.info('N/A：此旧样例未保存全部整数变量，不能由启停摘要重建。')
        else:
            integer_table=pd.DataFrame(integers)
            if 'index' in integer_table:integer_table['index']=integer_table['index'].map(lambda x:json.dumps(x,ensure_ascii=False))
            st.dataframe(integer_table,hide_index=True,width='stretch')
        st.download_button('下载该窗口全部阶段诊断JSON',json.dumps(stages,ensure_ascii=False,default=str).encode('utf-8'),
                           file_name='stage_diagnostics.json',mime='application/json',key=prefix+':json')



def system_metrics(tables):
    system=_frame(tables.get('system'));units=_frame(tables.get('units'))
    if units.empty:return
    period=int(units.period.max());last=units[units.period==period]
    row=system[system.period==period].iloc[0] if not system.empty and 'period' in system else None
    c=st.columns(4)
    c[0].metric('所选结果末时段',period)
    c[1].metric('负荷 MW',f"{row['load_mw']:.2f}" if row is not None and 'load_mw' in row else 'N/A')
    c[2].metric('报价主体净出力 MW',f'{last.P_mw.sum():.2f}')
    prices=_frame(tables.get('prices'));last_price=prices[prices.period==period] if not prices.empty else prices
    c[3].metric('节点算术平均结算价 元/MWh',f'{last_price.lmp.mean():.2f}' if not last_price.empty else 'N/A')
    st.caption('主体净出力包含储能/抽蓄充电负值；固定区外来电另在边界计划中，此合计不是含联络线的全部供给。')


def hydro_window_dashboard(history, *, prefix='hydro_window'):
    """Display solved lookahead targets, never label their endpoints as execution."""
    st.subheader('水电窗口末硬库容目标：前瞻诊断')
    if not history:
        st.info('N/A：该结果未保存水电硬目标窗口诊断；旧固定日前结果不据此反推新策略目标。')
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
    st.caption('当前采用实际库容/已执行功率接续，固定日前稳定区台数δ但功率可调。下面的“计划末值”属于前瞻窗口末，绝不是当前已执行库容。末目标为硬等式，无软追踪罚值。')
    metrics=st.columns(3)
    metrics[0].metric('窗口起点实际库容 万m³',f"{row['current_V_1e4m3']:.6f}")
    metrics[1].metric('窗口前已执行功率 MW',f"{row['current_P_mw']:.6f}")
    metrics[2].metric('窗口范围（末端为exclusive）',f"{int(start)} → {int(row['window_end_exclusive'])}")
    metrics=st.columns(3)
    fmt=lambda value: 'N/A' if pd.isna(value) else f'{float(value):.6f}'
    metrics[0].metric('日前同钟点目标 万m³',fmt(row['target_V_1e4m3']))
    metrics[1].metric('求解计划末库容 万m³',f"{row['actual_terminal_V_1e4m3']:.6f}")
    metrics[2].metric('计划末值−目标 万m³',fmt(row['signed_terminal_error_1e4m3']))
    if pd.isna(row['target_V_1e4m3']):st.info('此记录未启用窗口末硬目标（例如历史fixed_da对照）；目标与偏差按N/A展示，不当作硬目标验证。')
    if pd.notna(row['terminal_error_1e4m3']) and abs(row['terminal_error_1e4m3'])>1e-5:
        st.error('水电硬末目标诊断偏差超出1e-5万m³显示阈值，请检查求解与数据，不得当作软松弛接受。')
    if row.get('ramp_violation_max_mw',0)>1e-5:
        st.error('聚合端点爬坡诊断存在超限；此表不是逐台动态可行性证明。')
    st.dataframe(selected,hide_index=True,width='stretch')
    st.download_button('下载所选电站水电窗口诊断CSV',selected.to_csv(index=False).encode('utf-8-sig'),
                       file_name=f'{uid}_hydro_window_targets.csv',key=prefix+':download')


def reserve_dashboard(tables, *, prefix='reserve', indicative=False):
    """Display recorded awards only; missing historical measurements stay missing."""
    st.subheader('能量出清安全备用（非独立交易市场）')
    st.caption('这里是电能量模型的安全备用约束，不是新增备用交易或调频市场；不新增备用报价或付款。'
               + ('日前数据为计划。' if indicative else '实时数据只含各滚动窗口实际执行的首时段；不使用前瞻窗口最大短缺替代首段。'))
    system=_frame(tables.get('system'))
    down_flag=next((c for c in ('reserve_down_enabled','down_enabled') if c in system),None)
    down_disabled=bool(down_flag and not system.empty and system[down_flag].notna().all() and system[down_flag].eq(False).all())
    if down_disabled:
        st.info('本场景仅安排上备用：下备用需求与分配已停用。物理SOC/库容上下界仍是上备用激活能力约束，不代表下备用安排。')
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
        st.plotly_chart(px.line(selected,x='period',y=list(selected.columns[1:]),title='系统安全备用：需求、分配与短缺'),width='stretch',key=prefix+':systemplot')
        st.dataframe(selected,hide_index=True,width='stretch')
        st.download_button('下载系统安全备用CSV',system[[c for c in system if c=='period' or c.startswith('R_') or c in ('reserve_down_enabled','down_enabled')]].to_csv(index=False).encode('utf-8-sig'),file_name='safety_reserve_system.csv',key=prefix+':systemcsv')
    units=_frame(tables.get('units'))
    if units.empty or 'unit' not in units:
        st.info('逐主体安全备用：未记录。');return
    uid=st.selectbox('安全备用主体',sorted(units.unit.unique()),key=prefix+':unit')
    selected=units[units.unit==uid].sort_values('period')
    columns=[c for c in (('r_up',) if down_disabled else ('r_up','r_dn')) if c in selected and selected[c].notna().any()]
    if not columns:
        st.info('该主体上/下备用未记录；不能从能量出力或装机容量反推实际备用。')
        return
    display=selected[[c for c in ('period','unit','type','P_mw','on','reserve_eligible','reserve_qualified',*columns) if c in selected]].copy()
    st.plotly_chart(px.line(display,x='period',y=columns,title=f'{uid} 安排的备用容量 MW'),width='stretch',key=prefix+':unitplot')
    st.caption(('本场景仅展示r_up上备用；r_dn已停用。' if down_disabled else '')+'r_up / r_dn 是优化安排的预留容量，不代表已发生备用激活。reserve_eligible为设计/工况允许参与；reserve_qualified为本次参与资格二元z，z=1也可能分配0MW，不能当作供给。缺失值保持空缺，备用未设独立采购成本，供给约束为≥需求，可能存在超配/退化；不自动证明全网络可交付。')
    st.dataframe(display,hide_index=True,width='stretch')
    export_awards=selected[[c for c in ('period','unit','type','P_mw','on','reserve_eligible','reserve_qualified','r_up','r_dn') if c in selected]].copy()
    if down_flag:export_awards=export_awards.merge(system[['period',down_flag]],on='period',how='left')
    st.download_button('下载所选主体安全备用CSV',export_awards.to_csv(index=False).encode('utf-8-sig'),file_name=f'{uid}_safety_reserve.csv',key=prefix+':unitcsv')

    resource_kind=selected['type'].iloc[0] if 'type' in selected else None
    if resource_kind not in ('storage','pumped'):return
    st.subheader('储能/抽蓄备用能力诊断（不是已激活轨迹）')
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
    if 'reserve_qualified' in ability:
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
    keep=[c for c in ('period','unit','mode','reserve_eligible','reserve_qualified','E_start','E_mwh','V_start','V_up',*fields) if c in ability]
    ability=ability[keep]
    export_ability=ability.copy()
    if down_flag:export_ability=export_ability.merge(system[['period',down_flag]],on='period',how='left')
    if down_disabled:
        # Lower/upper are BOTH physical bounds for the up-activation Pu=P+ru;
        # they are not up/down reserve products. Prefer unambiguous new names.
        old_duration=[c for c in ability if c.startswith('reserve_duration_up_') and any(x.startswith('reserve_duration_lower_') for x in ability)]
        ability=ability.drop(columns=[c for c in ability if '_down_' in c]+old_duration)
        st.caption('上备用持续激活功率 Pu=P+r_up：lower/upper 分别是同一次上备用激活的能量/库容下界与上界余量，两侧都要核验，不是下备用商品。')
    st.caption('T_dur_h单位小时；power余量单位MW；电池duration余量MWh、抽蓄duration余量万m³，不混合相加。z=0时持续能力未参与，显示N/A（表中空值）；其他空值表示未启用或未记录，并非0；非负余量只验证所列能力约束，不证明全网络或动态可交付。')
    st.dataframe(ability,hide_index=True,width='stretch')
    st.download_button('下载所选主体备用能力诊断CSV',export_ability.to_csv(index=False).encode('utf-8-sig'),file_name=f'{uid}_reserve_capability.csv',key=prefix+':capabilitycsv')
