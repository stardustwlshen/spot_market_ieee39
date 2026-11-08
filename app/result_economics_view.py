"""Business income/cost/profit view from frozen evidence, without writing reports."""
from __future__ import annotations
import numpy as np
import pandas as pd
import streamlit as st
from common import TYPE_CN, show_df, df_download, period_labels
from result_economics import build_unit_economics, summarize_unit_economics
from smm.time_grid import result_time_grid

PHASE = {'da': '日前计划', 'rt': '实际结算（日前＋实时偏差）'}
LABELS = {
    'unit': '机组', 'type': '类型', 'phase': '核算阶段',
    'generation_mwh': '发电/放电电量 MWh', 'purchase_mwh': '充电/抽水电量 MWh',
    'gross_generation_cash_yuan': '售电收入 元', 'purchase_cost_yuan': '充电/抽水电费 元',
    'operating_cost_yuan': '发电及启停成本 元', 'total_cost_yuan': '总成本 元',
    'profit_yuan': '利润 元', 'net_cash_yuan': '净电费结算 元',
    'da_cash_yuan': '日前净电费 元', 'rt_deviation_cash_yuan': '实时偏差净电费 元',
    'variable_cost_yuan': '发电成本 元', 'fixed_online_cost_yuan': '在线成本 元',
    'startup_cost_yuan': '启动成本 元', 'shutdown_cost_yuan': '停机成本 元',
    'cost_available': '成本/利润状态', 'period_count': '时段数',
    'cost_missing_reason': '不可用原因',
}


def business_frame(table, fields):
    out = table[[c for c in fields if c in table]].copy()
    if 'type' in out:
        out['type'] = out.type.map(lambda t: TYPE_CN.get(t, t))
    if 'phase' in out:
        out['phase'] = out.phase.map(PHASE)
    if 'cost_available' in out:
        out['cost_available'] = out.cost_available.map({True: '已核算', False: '不可用'})
    return out.rename(columns=LABELS)


def paired_summary(summary):
    """One row per unit, six comparable financial columns, same scope."""
    identity = summary[['unit', 'type']].drop_duplicates().set_index('unit')
    out = identity.copy()
    for phase, label in [('da', '日前'), ('rt', '实际')]:
        part = summary[summary.phase.eq(phase)].set_index('unit')
        if part.empty:
            continue
        for field, title in [('gross_generation_cash_yuan', '收入'), ('total_cost_yuan', '成本'), ('profit_yuan', '利润')]:
            out[label + title + ' 元'] = part[field]
        out[label + '成本/利润状态'] = part.cost_available.map({True: '已核算', False: '不可用'})
    out['type'] = out.type.map(lambda t: TYPE_CN.get(t, t))
    return out.reset_index().rename(columns={'unit': '机组', 'type': '类型'})


def prepare_economics(result):
    """Reuse within one page render only; never cache across evidence changes."""
    try:
        return {'detail': build_unit_economics(result), 'error': None}
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return {'detail': pd.DataFrame(), 'error': str(exc)}


def render_economics(result, *, uid=None, prefix='economics', prepared=None):
    prepared = prepare_economics(result) if prepared is None else prepared
    try:
        if prepared['error']:
            raise ValueError(prepared['error'])
        detail = prepared['detail'].copy()
        if uid is not None and not detail.empty:
            detail = detail[detail.unit.eq(uid)].copy()
        summary = summarize_unit_economics(detail)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        st.warning('收入、成本与利润不可用：本次冻结证据未通过核验。' + str(exc))
        return pd.DataFrame()
    if summary.empty:
        st.info('本次未提供可核算的机组收入、成本与利润。')
        return summary
    st.subheader('收入、成本与利润' if uid else '机组收入、成本与利润')
    has_rt = detail.phase.eq('rt').any()
    n, dt = result_time_grid(result)
    count = detail.period.nunique()
    scope = '全天' if count == n and (not has_rt or (result.get('summary') or {}).get('completed_scope') == 'full_day') else '已保存范围'
    st.caption(f'{scope} {count}/{n} 段，每段 {dt * 60:g} 分钟。' +
               ('日前计划与实际结算按同一时段范围比较。' if has_rt else '仅日前计划，未发生实时偏差结算。'))
    st.caption('成本采用本次冻结的现货成本研究值，非实测；不使用申报价。利润＝售电收入−充电/抽水电费−发电及启停成本。')
    if uid:
        fields = ['phase', 'gross_generation_cash_yuan', 'purchase_cost_yuan', 'operating_cost_yuan',
                  'total_cost_yuan', 'profit_yuan', 'cost_available']
        shown = business_frame(summary, fields)
    else:
        shown = paired_summary(summary)
    show_df(shown, '%.2f', height=min(660, 38 + 35 * len(shown)))
    incomplete = summary[~summary.cost_available.eq(True)]
    if not incomplete.empty:
        st.warning('部分成本或初始状态缺失，相应成本与利润不可用；收入仍按已保存证据展示。')
        show_df(business_frame(incomplete, ['unit', 'phase', 'cost_missing_reason']))
    st.caption('实际售电收入和充电/抽水电费均采用日前＋实时偏差两部制；购电费只扣一次。日前计划成本不再计入实际成本。日内参考不结算。')
    with st.expander('逐时收入、成本与利润' if uid else '各机组逐时核算', expanded=False):
        selected = uid
        if selected is None:
            selected = st.selectbox('核算机组', list(summary.unit.drop_duplicates()), key=prefix + ':unit')
        phases = [p for p in ('da', 'rt') if detail.phase.eq(p).any()]
        phase = st.radio('逐时核算阶段', phases, format_func=PHASE.get, horizontal=True, key=prefix + ':phase')
        rows = detail[detail.unit.eq(selected) & detail.phase.eq(phase)].copy()
        rows.insert(0, '时刻', rows.period.map(dict(enumerate(period_labels(n, dt)))))
        fields = ['时刻', 'gross_generation_cash_yuan', 'purchase_cost_yuan', 'operating_cost_yuan',
                  'total_cost_yuan', 'profit_yuan', 'cost_available']
        show_df(business_frame(rows, fields), '%.2f', height=360)
        st.caption('净电费与成本组成（同一机组、同一阶段）')
        show_df(business_frame(rows, ['时刻', 'da_cash_yuan', 'rt_deviation_cash_yuan', 'net_cash_yuan',
                 'variable_cost_yuan', 'fixed_online_cost_yuan', 'startup_cost_yuan', 'shutdown_cost_yuan']), '%.2f', height=300)
        st.caption('净电费＝日前净电费＋实时偏差净电费＝售电收入−购电费。负购电费是净返款，不强制归零。按存档精度计算，显示保留两位。库存变化不折现，不含折旧、运维、融资、税费和补贴。')
    with st.expander('本次成本口径与参数', expanded=False):
        profile = detail.attrs.get('cost_profile')
        if not profile:
            st.caption('未提供本次冻结现货成本，不能以申报价或当前配置代替。')
        else:
            names = {
                'fuel_cost_yuan_per_mwh': '发电燃料成本 元/MWh',
                'online_cost_yuan_per_h': '在线成本 元/h',
                'startup_cost_yuan_per_start': '启动成本 元/次',
                'shutdown_cost_yuan_per_stop': '停机成本 元/次',
                'gen_start_yuan_per_unit_start': '发电启动成本 元/台次',
                'pump_start_yuan_per_unit_start': '抽水启动成本 元/台次',
                'gen_stop_yuan_per_unit_stop': '发电停机成本 元/台次',
                'pump_stop_yuan_per_unit_stop': '抽水停机成本 元/台次',
            }
            records = []
            for unit, params in profile.get('units', {}).items():
                if uid is not None and unit != uid:
                    continue
                costs = [(k, v) for k, v in params.items() if k in names]
                if not costs:
                    records.append({'机组': unit, '项目': '本口径无燃料/独立启停成本', '参数值': 0.0})
                for key, value in costs:
                    records.append({'机组': unit, '项目': names[key], '参数值': value})
            show_df(pd.DataFrame(records), '%.4f')
            st.caption('冻结文件：' + str(profile.get('input_file', '未记录')) + '；校验值：' + str(profile.get('sha256', '未记录')))
            st.caption('按已保存出力与节点价精度回算；与求解时未舍入明细可能有分厘差异。未重算出清，未改历史存档。')
    return summary
