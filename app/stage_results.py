"""Read-only views of actual saved stages, including incomplete historical evidence."""
from __future__ import annotations
import pandas as pd
import streamlit as st

STAGE_NAMES = {'scuc_preplan':'SCUC 启停预计划', 'energy_sced':'正式 SCED',
               'auxiliary_pricing':'辅助定价', 'agc_auxiliary':'历史调频辅助筛选'}


def execution_rows(rows, stage, market_stage, window_start=None):
    frame = pd.DataFrame(rows).copy()
    if frame.empty:
        return frame
    if stage != 'energy_sced':
        frame['结果范围'] = '不执行的预计划/辅助数量'
    elif market_stage == 'RT':
        frame['结果范围'] = '前瞻计划（未执行）'
        if window_start is not None and 'period' in frame:
            frame.loc[frame.period == int(window_start), '结果范围'] = '本窗口已执行首段'
    else:
        frame['结果范围'] = '日前中标计划（非实时执行）' if market_stage == 'DA' else '日内参考计划（不执行、不结算）'
    return frame


def stage_result_view(stages, *, prefix, market_stage='DA', window_start=None):
    from common import display_frame, df_download
    if not stages:
        st.info('未保存各求解阶段结果。不能从最终计划反推 SCUC、SCED 或辅助模型。')
        return
    valid={k:v for k,v in stages.items() if isinstance(v,dict)}
    if not valid:
        st.warning('阶段记录格式无效。');return
    name=st.selectbox('查看求解阶段', list(valid), format_func=lambda x:STAGE_NAMES.get(x,x), key=prefix+':result_stage')
    data=valid[name];summary=data.get('summary') or {}
    st.caption('日内所有数量均为参考计划，所有价格均为指示价，不执行、不结算。' if market_stage=='ID' else
               '实时仅正式 SCED 的窗口首段已执行；其余时段为前瞻计划，SCUC/辅助模型数量不执行。' if market_stage=='RT' else
               '日前正式 SCED 形成中标计划；SCUC 为启停预计划；辅助定价只提供价格，不替代正式数量。')
    selected=summary.get('selected_price_source',summary.get('prices_are_market_prices'))
    st.caption(('本阶段价格为所选指示价来源，不结算。' if market_stage=='ID' else '本阶段价格为所选结算价格规则来源，不代表已付款。') if selected else
               '本阶段价格不作为所选结算价。' if selected is False else '价格是否选用未记录，不推断。')
    facts={k:v for k,v in summary.items() if not isinstance(v,(dict,list))}
    if facts:st.dataframe(display_frame(pd.DataFrame([facts])),hide_index=True,width='stretch')
    units=execution_rows(data.get('units',[]),name,market_stage,window_start)
    if units.empty:
        st.info('本阶段未保存逐主体出力/状态。')
    else:
        st.dataframe(display_frame(units),hide_index=True,width='stretch',height=300)
        pass  # Result download controls removed.
    prices=pd.DataFrame(data.get('prices',[]))
    if prices.empty:
        st.caption('本阶段未保存独立节点价格记录；SCUC 不据整数模型目标推算节点电价。')
    else:
        st.markdown('**本阶段节点价格：原始对偶与限价后值**')
        st.dataframe(display_frame(prices),hide_index=True,width='stretch',height=220)
        pass  # Result download controls removed.
    hydro=pd.DataFrame(data.get('hydro_states',[]))
    if not hydro.empty:
        st.markdown('**本阶段水电库容与出库流量计划**')
        st.dataframe(display_frame(execution_rows(hydro.to_dict('records'),name,market_stage,window_start)),hide_index=True,width='stretch',height=220)


def intraday_inventory(summary, table=None, diagnostics=None):
    """Union of real successes/failures; absence is not synthesized as success."""
    records={}
    if isinstance(table,pd.DataFrame) and 'tau' in table:
        for row in table.to_dict('records'):
            tau=int(row['tau']);records[tau]=dict(row,status=('完成：可行计划，未达目标精度' if row.get('gap_target_met') is False else '成功：参考计划，不执行'), success=True)
    for row in (diagnostics or {}).get('id_rounds',[]):
        tau=int(row['tau'])
        records.setdefault(tau,dict(tau=tau,status='成功：阶段证据已保存',success=True))
    failures=(summary or {}).get('failed_rounds') or (diagnostics or {}).get('id_failures') or []
    for row in failures:
        if isinstance(row,dict) and row.get('tau') is not None:
            records[int(row['tau'])]={**row,'tau':int(row['tau']),'status':'失败：回退日前参考','success':False}
    return pd.DataFrame([records[t] for t in sorted(records)])


def intraday_state_rows(evidence, tau):
    rows=[]
    for r in (evidence or {}).get('id_rounds',[]):
        if r.get('tau') != tau:continue
        for key,label,unit in [('E','储能电量','MWh'),('Vu','抽蓄上库库容','万m³'),('Vh','水电库容','万m³'),('Qout','水电出库流量','m³/s')]:
            for asset,values in (r.get('snapshot',{}).get(key) or {}).items():
                for i,value in enumerate(values):rows.append(dict(period=tau+i,unit=asset,指标=label,数值=value,单位=unit,结果范围='日内计划（未执行）'))
    return pd.DataFrame(rows)


def live_intraday_view(live, *, prefix):
    history=live.get('id_stage_history') or {};failures=live.get('id_failures') or []
    rounds=live.get('id_rounds') or []
    if not (history or failures or rounds):return
    st.subheader('日内滚动：已完成轮次与失败回退')
    st.caption(f'成功 {len(rounds)} 轮，失败 {len(failures)} 轮。日内进度不增加实时已执行段数，不生成结算。')
    if failures:st.warning('存在日内失败轮，实时按记录回退日前参考。');st.dataframe(pd.DataFrame(failures),hide_index=True,width='stretch')
    misses=[r for r in rounds if r.get('solution_quality',{}).get('gap_target_met') is False]
    if misses:st.warning(f'日内 {len(misses)} 轮已取得可行计划，但未达到目标 MIP 精度；不等于物理不可行回退。')
    all_stages={**history, **(live.get('id_failed_stage_history') or {})}
    taus=sorted({str(r['tau']) for r in rounds+failures if r.get('tau') is not None} | set(all_stages),key=int)
    if taus:
        tau=st.selectbox('日内已尝试轮次起点（含失败）',taus,key=prefix+':id_tau')
        stage_result_view(all_stages.get(tau),prefix=prefix+':id',market_stage='ID',window_start=int(tau))
