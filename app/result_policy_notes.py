"""Read-only policy and optional-archive presentation; never infer current defaults."""
from __future__ import annotations

import json
from math import isfinite
from pathlib import Path

import pandas as pd


def saved_object(path):
    path = Path(path)
    if not path.exists():
        return {}, None
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError('顶层应为对象记录')
        return value, None
    except (OSError, ValueError, UnicodeError) as exc:
        return {}, f'{path.name} 不能读取：{exc}'


def saved_stage_diagnostics(directory):
    data, error = saved_object(Path(directory) / 'stage_diagnostics.json')
    if error:
        return {}, error
    da, windows = data.get('da'), data.get('rt_windows')
    if da is not None and not isinstance(da, dict):
        return {}, 'stage_diagnostics.json 日前阶段应为对象记录'
    if windows is not None and (not isinstance(windows, list) or any(
            not isinstance(row, dict) or not isinstance(row.get('t'), (int, float))
            or not isinstance(row.get('stages'), dict) for row in windows)):
        return {}, 'stage_diagnostics.json 实时窗口记录格式无效'
    rounds = data.get('id_rounds')
    if rounds is not None and (not isinstance(rounds, list) or any(
            not isinstance(row, dict) or not isinstance(row.get('tau'), (int,float))
            or not isinstance(row.get('stages'), dict) for row in rounds)):
        return {k:v for k,v in data.items() if k != 'id_rounds'}, 'stage_diagnostics.json 日内阶段记录格式无效'
    partial = data.get('id_failed_stages')
    if partial is not None and (not isinstance(partial,list) or any(
            not isinstance(row,dict) or not isinstance(row.get('tau'),(int,float))
            or not isinstance(row.get('stages'),dict) for row in partial)):
        return {k:v for k,v in data.items() if k != 'id_failed_stages'}, 'stage_diagnostics.json 日内失败前阶段记录格式无效'
    failures = data.get('id_failures')
    if failures is not None and (not isinstance(failures, list) or any(not isinstance(r,dict) for r in failures)):
        return {k:v for k,v in data.items() if k != 'id_failures'}, 'stage_diagnostics.json 日内失败记录格式无效'
    return data, None


def storage_policy_note(result, frozen_mode=None):
    """Use this run's explicit summary/config, not today's model defaults."""
    summary = result.get('summary') or {}
    rt = summary.get('realtime') or {}
    frozen_rt = (frozen_mode or {}).get('realtime') or {}
    mode = rt.get('storage_mode')
    anchor = rt.get('anchor')
    if mode == 'fixed_da':
        return '实时储能/抽蓄执行日前充放电曲线；只展示本次已保存的执行记录。'
    if mode == 'sced_anchor':
        if anchor == 'two_hour_eq':
            return ('实时储能/抽蓄采用两小时日前状态硬锚点；明确不可行时的软化以本次记录为准，'
                    '非锚点时段不强制一致。')
        if anchor == 'hourly_eq':
            return '历史实时储能/抽蓄采用每小时日前状态带罚软锚点目标；不是两小时硬锚点。'
        if anchor == 'window_end_pct':
            return '实时储能/抽蓄采用窗口末日前状态带罚软区间；实际范围与软化以本次保存记录为准。'
        return '本次记录为锚点模式，但锚点类型未识别；不按当前默认策略解释。'
    if mode != 'value':
        return '本次未明确记录储能/抽蓄实时模式，不按当前默认策略推断。'
    horizon = rt.get('lookahead_h')
    if isinstance(horizon, (int, float)) and not isinstance(horizon, bool) and isfinite(horizon) and horizon > 0:
        window = f'配置前瞻 {horizon:g} 小时，日末按剩余时段截断'
    else:
        window = '前瞻时长未记录'
    note = (f'实时价值法：{window}，每个窗口仅执行首时段。窗口末价值和中间偏差带的参考，'
            '按逐资源、逐窗口保存的轨迹来源与价值来源核对；可能来自日前、日内或日前回退，'
            '不能仅凭启用日内推断。中间参考带围绕所选参考状态，不统一认定为日前。')
    policies = []
    explicit = rt.get('terminal_policy_mode')
    if explicit is not None:
        policies.append(explicit)
    for source in (rt, frozen_rt):
        vm = source.get('value_method') or {}
        policy = vm.get('terminal_policy') or {}
        if isinstance(policy, dict) and policy.get('mode') is not None:
            policies.append(policy['mode'])
    rows = result.get('rt_value_windows')
    if isinstance(rows, pd.DataFrame) and 'terminal_policy' in rows:
        policies.extend(rows.terminal_policy.dropna().unique().tolist())
    if policies and all(p == 'soft' for p in policies):
        return note + ' 本次日末采用计划软目标，偏差按保存罚价计入优化目标；不要求末态硬等于日前，也不是结算付款或应急兜底。'
    if policies and all(p == 'hard' for p in policies):
        return note + ' 本次日末采用硬目标政策；不能用新软政策重新解释此存档。'
    if policies:
        return note + ' 日末软/硬政策记录不一致或未识别，请核对原始记录，不推断。'
    return note + ' 日末软/硬政策未明确记录，不根据当前默认配置或软化标记推断。'


def storage_endpoint_rows(result, label):
    """Optional DA reference can be unknown without hiding valid RT endpoints."""
    rt, da = result.get('rt_storage'), result.get('da_storage')
    if rt is None or rt.empty:
        return []
    rows = []
    for unit, group in rt.groupby('unit'):
        period = group.period.max()
        actual = group.loc[group.period == period, 'E_mwh']
        reference = (da.loc[(da.unit == unit) & (da.period == period), 'E_mwh']
                     if isinstance(da, pd.DataFrame) and {'unit', 'period', 'E_mwh'} <= set(da) else pd.Series(dtype=float))
        rows.append({'结果': label, '储能': unit,
                     '日前电量@末时段': float(reference.iloc[0]) if len(reference) == 1 else float('nan'),
                     '实时电量@末时段': float(actual.iloc[0]) if len(actual) == 1 else float('nan')})
    return rows
