"""Read saved daily hydro minimum audits; keep actual energy separate from plans."""
from __future__ import annotations

import json
import math
from numbers import Real
from pathlib import Path

import pandas as pd
import streamlit as st


def _number(value):
    return float(value) if isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value) else None


def _daily_audit(summary):
    if 'hydro_daily_minimum' in summary:
        return summary['hydro_daily_minimum']
    checks = summary.get('checks')
    if isinstance(checks, dict):
        fields = {key.removeprefix('hydro_daily_minimum_'): value for key, value in checks.items()
                  if key.startswith('hydro_daily_minimum_')}
        if fields:
            return fields
    return None


def actual_daily_minimum(summary):
    """A green final status requires explicit, consistent full-day saved evidence."""
    audit = _daily_audit(summary)
    if audit is None:
        return 'missing', pd.DataFrame(), []
    if not isinstance(audit, dict) or type(audit.get('active')) is not bool:
        return 'unverified', pd.DataFrame(), ['水电日电量下限审计格式无效或启用状态未记录。']
    if audit['active'] is False:
        return 'inactive', pd.DataFrame(), []
    rows = audit.get('records')
    errors = audit.get('errors')
    problems = [str(e) for e in errors] if isinstance(errors, list) else ['审计错误清单未记录或格式无效。']
    if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
        return 'unverified', pd.DataFrame(), problems + ['逐水电主体的实际日电量记录缺失或损坏。']
    labels = {'minimum_mwh': '已声明下限 MWh', 'executed_mwh': '实际已发 MWh', 'remaining_mwh': '距下限尚缺 MWh'}
    display = []
    seen = set()
    for row in rows:
        unit = row.get('unit')
        if not isinstance(unit, str) or not unit.strip() or unit in seen:
            problems.append('水电主体编号缺失或重复。')
        else:
            seen.add(unit)
        record = {'电站': unit if isinstance(unit, str) else '未记录'}
        values = {key: _number(row.get(key)) for key in labels}
        record.update({label: values[key] for key, label in labels.items()})
        record['实际执行范围'] = '全天' if row.get('full_day') is True else '已执行前缀' if row.get('full_day') is False else '未记录'
        display.append(record)
        if any(value is None or value < -1e-5 for value in values.values()):
            problems.append(f'{unit} 实际日电量必要数值缺失或无效。')
        elif abs(values['remaining_mwh'] - max(0., values['minimum_mwh'] - values['executed_mwh'])) > 1e-5:
            problems.append(f'{unit} 实际已发、下限与尚缺电量不一致。')
    table = pd.DataFrame(display)
    if audit.get('evidence_valid') is False or problems:
        return 'failed', table, problems or ['已保存的独立证据校验未通过。']
    if audit.get('evidence_valid') is not True:
        return 'unverified', table, ['独立证据校验结论未记录，不能判为通过。']
    if audit.get('full_day') is False:
        if audit.get('final_satisfied') is not None or any(row.get('full_day') is not False or row.get('final_satisfied') is not None for row in rows):
            return 'unverified', table, ['前缀与最终日电量结论不一致，不能按完整日解释。']
        return 'prefix', table, []
    expected, executed = _number(summary.get('expected_periods')), _number(summary.get('executed_periods'))
    full_scope = (summary.get('completed_scope') == 'full_day' and expected is not None
                  and expected > 0 and expected.is_integer() and executed == expected)
    if audit.get('full_day') is not True or not full_scope or any(row.get('full_day') is not True for row in rows):
        return 'unverified', table, ['实际完整日范围证据不一致或未记录，不能判为终日通过。']
    if audit.get('final_satisfied') is False or any(row.get('final_satisfied') is False for row in rows):
        return 'failed', table, ['已保存的实际终日下限审计未通过。']
    if audit.get('final_satisfied') is not True or any(row.get('final_satisfied') is not True for row in rows):
        return 'unverified', table, ['实际终日下限审计结论未记录。']
    if any(_number(row['executed_mwh']) < _number(row['minimum_mwh']) - 1e-5 for row in rows):
        return 'failed', table, ['实际已发低于已声明下限，与通过标记不一致。']
    return 'passed', table, []


def planned_daily_minimum(evidence):
    """Flatten saved windows without summing overlapping plans or witness energy."""
    if not isinstance(evidence, dict):
        raise ValueError('水电窗口证据不是对象记录')
    snapshots = [('DA', evidence.get('da'))]
    for collection, stage in (('id_rounds', 'ID'), ('rt_windows', 'RT')):
        windows = evidence.get(collection, [])
        if not isinstance(windows, list) or any(not isinstance(window, dict) for window in windows):
            raise ValueError(f'{collection} 不是有效的窗口列表')
        snapshots.extend((stage, window.get('snapshot')) for window in windows)
    columns = {'daily_minimum_mwh': '已声明下限 MWh', 'executed_before_mwh': '窗口前实际已发 MWh',
               'remaining_minimum_mwh': '窗口前尚需 MWh', 'window_generation_mwh': '窗口预测发电 MWh',
               'witness_generation_mwh': '后缀见证发电 MWh（不执行）', 'minimum_margin_mwh': '窗口加后缀余量 MWh'}
    output = []
    for stage, snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        daily = snapshot.get('hydro_daily_energy')
        if not isinstance(daily, dict) or daily.get('active') is not True:
            continue
        rows = daily.get('rows')
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError(f'{stage} 日电量窗口记录损坏')
        suffix = daily.get('suffix')
        source = suffix.get('reference_metadata') if isinstance(suffix, dict) else None
        for row in rows:
            output.append({'阶段': {'DA': '日前计划', 'ID': '日内剩余日计划', 'RT': '实时前瞻'}[stage],
                           '窗口起点序号': daily.get('start_period'), '窗口末序号（不含）': daily.get('end_period_exclusive'),
                           '电站': row.get('unit', '未记录'),
                           **{label: _number(row.get(field)) for field, label in columns.items()},
                           '后缀参考来源': json.dumps(source, ensure_ascii=False, sort_keys=True) if source is not None else '未记录/不适用'})
    return pd.DataFrame(output)


def render_hydro_daily_energy(result):
    summary = result.get('summary') or {}
    status, actual, problems = actual_daily_minimum(summary)
    if status == 'missing':
        st.caption('水电日电量下限：此历史结果未记录全阶段审计，不能推断未设下限或最终达标。')
        return
    if status == 'inactive':
        st.caption('水电日电量下限：本次明确记录未启用非空下限，不表示已完成下限达标验收。未新增任何默认下限。')
        return
    st.subheader('水电日电量下限：实际执行与计划证据')
    messages = {
        'passed': ('success', '实际终日下限审计通过：只依据完整日正式执行首时段累计；不包含重叠前瞻或后缀见证发电。'),
        'prefix': ('info', '实际执行尚未覆盖完整日：下表仅为目前已发与尚缺，最终日电量下限未完成验收。即使目前已达到下限，也不显示终日通过。'),
        'failed': ('error', '水电日电量下限或其独立证据校验未通过，不能视为实际全天合规。'),
        'unverified': ('warning', '水电日电量下限审计未核实或记录不完整，不判为终日通过。'),
    }
    method, message = messages[status]
    getattr(st, method)(message)
    st.caption('这里只读取本次保存的审计结果。实际已发只累计正式实时首时段；计划与资产级续程见证不是实际发电，不产生后缀结算。')
    if not actual.empty:
        st.dataframe(actual, hide_index=True, width='stretch')
    for problem in problems:
        st.caption(problem)
    with st.expander('日前、日内与实时预测证据（不计入实际累计）', expanded=False):
        st.caption('每行是各自窗口的计划，不跨窗口累加。后缀仅是水电资产级续程见证，不证明未来网络、备用或全系统联合可行；仅正式实时首时段执行。时段序号从0起，缺失值不补零。')
        path = Path(result['dir']) / 'hydro_window_evidence.json'
        if not path.is_file():
            st.info('本次未保存水电窗口证据，预测与后缀数值保持未知。')
            return
        try:
            evidence = json.loads(path.read_text(encoding='utf-8'))
            planned = planned_daily_minimum(evidence)
        except (OSError, ValueError, TypeError, UnicodeError) as exc:
            st.warning('水电窗口证据无法读取，不能验证预测或续程数值；原文件不改写。' + str(exc))
            return
        if planned.empty:
            st.info('窗口文件未记录日电量下限计划或续程明细，不按零解释。')
        else:
            st.dataframe(planned, hide_index=True, width='stretch')
