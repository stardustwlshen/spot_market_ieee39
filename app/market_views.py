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
        st.download_button('下载当前主体筛选CSV',u.to_csv(index=False).encode('utf-8-sig'),file_name=f'{uid}_{"indicative" if indicative else "executed"}.csv',key=prefix+':download')
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
