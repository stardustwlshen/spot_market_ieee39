"""Battery/pumped DA-state anchors on the operating day's Asia/Shanghai clock.

State array index k is the END of [k*dt, (k+1)*dt), not its start.
The daily horizon starts at 00:00; 24:00 uses DA[-1], never next-day DA[0].
"""
from __future__ import annotations

import math
from numbers import Real

ANCHOR_KINDS = ('two_hour_eq', 'hourly_eq', 'window_end_pct')


def _positive(value, name):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or value <= 0:
        raise ValueError(f'{name}须为有限正数')
    return float(value)


def _steps(hours, dt, name):
    steps = hours / dt
    if not math.isclose(steps, round(steps), rel_tol=0., abs_tol=1e-9):
        raise ValueError(f'{name}必须为模型步长dt_h的整数倍，不能四舍五入')
    return int(round(steps))


def validate_anchor_config(cfg):
    """Validate the new contract, retaining old snapshots' historical behavior."""
    rc = cfg.mode['realtime']
    sc = rc['storage']
    kind = sc.get('anchor', 'hourly_eq')
    if kind not in ANCHOR_KINDS:
        raise ValueError(f'未知锚点方式 {kind}')
    if kind != 'two_hour_eq' or sc.get('mode', 'sced_anchor') != 'sced_anchor':
        return
    dt = _positive(cfg.base['dt_h'], 'dt_h')
    interval = _positive(rc['interval_min'], 'realtime.interval_min')
    if not math.isclose(dt * 60, interval, rel_tol=0., abs_tol=1e-9):
        raise ValueError('两小时硬锚点要求realtime.interval_min与dt_h一致')
    _steps(2., dt, '两小时锚点间隔')
    lookahead = _positive(rc['lookahead_h'], 'realtime.lookahead_h')
    _steps(lookahead, dt, '实时前瞻窗口')
    if lookahead < 2.:
        raise ValueError('两小时硬锚点要求前瞻lookahead_h至少2小时；仅运行日末允许截短，不得无目标透支')
    if isinstance(rc.get('binding_periods', 1), bool) or rc.get('binding_periods', 1) != 1:
        raise ValueError('两小时硬锚点仅支持binding_periods=1，每轮只执行首时段')
    if cfg.T <= 0 or cfg.T * dt > 24. + 1e-9:
        raise ValueError('两小时硬锚点仅支持北京时间00:00起的单运行日，至24:00；跨日须提供新日前计划与连续实际初态')


def anchor_periods(cfg, t, t1):
    """Absolute end-state indices strictly after start, inclusive of window end.

    A short terminal window has only clock anchors it actually contains; an odd
    window end never gets an extra anchor. A truncated study before 02:00 may
    contain none and is not a full two-hour consistency certificate.
    """
    if not 0 <= t < t1 <= cfg.T:
        raise ValueError(f'锚点窗口[{t},{t1})超出日前范围[0,{cfg.T})')
    kind = cfg.mode['realtime']['storage'].get('anchor', 'hourly_eq')
    if kind == 'window_end_pct':
        return [t1-1]
    if kind == 'hourly_eq':
        return [k for k in range(t, t1) if (k+1) % 4 == 0]  # historical 15min semantics
    if kind != 'two_hour_eq':
        raise ValueError(f'未知锚点方式 {kind}')
    validate_anchor_config(cfg)
    stride = _steps(2., cfg.dt, '两小时锚点间隔')
    return [k for k in range(t, t1) if (k+1) % stride == 0]


def exact_target(series, unit, k, lower, upper):
    """Hard consistency must never silently clip or omit a DA target."""
    try:
        value = float(series[unit][k])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError(f'两小时硬锚点缺少日前状态 {unit} 第{k+1}段末') from exc
    if not math.isfinite(value) or not lower-1e-6 <= value <= upper+1e-6:
        raise ValueError(f'两小时硬锚点日前目标越界或非有限数：{unit} 第{k+1}段末={value}，物理范围[{lower},{upper}]；不裁剪目标')
    return value


def policy_metadata(cfg):
    storage = cfg.mode['realtime']['storage']
    active = storage.get('mode', 'sced_anchor') == 'sced_anchor'
    hard = active and storage.get('anchor', 'hourly_eq') == 'two_hour_eq'
    return dict(anchor=cfg.mode['realtime']['storage'].get('anchor', 'hourly_eq'),
                equality=('hard' if hard else 'soft') if active else 'not_applicable', timezone='Asia/Shanghai',
                state_time='period_end', operating_day_start='00:00',
                clock_hours=list(range(2,25,2)) if hard else None,
                reachability_scope=('remaining_day_battery_pumped_integer_witness;not_future_network_or_reserve' if hard else None),
                day_end='24:00 uses last DA end-state; no wrap to next-day index 0')


def validate_actual_state(cfg, plan, state, t, tolerance=1e-5):
    """No retroactive reset when a rolling window starts on an already due anchor."""
    if cfg.mode['realtime']['storage'].get('anchor') != 'two_hour_eq':
        return
    for kind, group, actual in (('E', 'storage', state.storage), ('Vu', 'pumped', state.pumped)):
        expected = {d['id'] for d in cfg.units.get(group, [])}
        if set(actual) != expected:
            raise ValueError(f'两小时硬锚点缺少完整实际{group}状态，不能退回申报初值')
        for unit in expected:
            value = actual[unit] if kind == 'E' else actual[unit].get('V')
            if value is None or not math.isfinite(float(value)):
                raise ValueError(f'两小时硬锚点实际状态非有限数：{unit}')
            if t > 0 and t % _steps(2., cfg.dt, '两小时锚点间隔') == 0:
                target = float(plan[kind][unit][t-1])
                if not math.isfinite(target) or abs(float(value)-target) > tolerance:
                    raise ValueError(f'两小时硬锚点起点{t*cfg.dt:02g}:00已有偏差：{unit} 实际={value}，日前={target}；不得重置实际库存')


def hard_anchor_checks(cfg, plan, rounds, tolerance=1e-5):
    """Audit detached formal windows AND their executed first-period endpoints."""
    active = (cfg.mode['realtime']['storage'].get('mode', 'sced_anchor') == 'sced_anchor'
              and cfg.mode['realtime']['storage'].get('anchor') == 'two_hour_eq')
    result = dict(storage_pumped_hard_anchors_active=active,
                  storage_pumped_hard_anchors_satisfied=None, hard_anchor_evidence_complete=None,
                  hard_anchor_window_count=0, hard_anchor_executed_count=0,
                  storage_hard_anchor_max_err_mwh=0., pumped_hard_anchor_max_err_1e4m3=0.,
                  hard_anchor_scope='formal_RT_windows_and_executed_clock_endpoints_only;not_full_day_if_truncated')
    if not active:
        return result
    complete = True
    for r in rounds:
        start = int(r['t']); sn = r['snap']
        # Every enabled storage resource has the same window length.
        lengths = [len(a) for key in ('E','Vu','P') for a in sn.get(key, {}).values()]
        if not lengths or len(set(lengths)) != 1:
            complete = False
            continue
        end = start + lengths[0]
        periods = anchor_periods(cfg, start, end)
        expected = {(kind, d['id'], k-start) for kind, group in (('E','storage'),('Vu','pumped'))
                    for d in cfg.units.get(group, []) for k in periods}
        rows = [a for a in sn.get('anchors', []) if a.get('kind') in ('E','Vu')]
        keys = [(a['kind'],a['unit'],a['period']) for a in rows]
        if set(keys) != expected or len(keys) != len(expected):
            complete = False
        for a in rows:
            kind, unit, local = a['kind'], a['unit'], a['period']
            if (kind, unit, local) not in expected or not a.get('hard'):
                complete = False
                continue
            target = float(plan[kind][unit][start+local])
            value = float(sn[kind][unit][local])
            evidence = [target, value, a['value'], a['lower'], a['upper'], a['slack_up'], a['slack_dn']]
            if not all(math.isfinite(x) for x in evidence):
                complete = False
                continue
            error = max(abs(value-target), abs(a['value']-value), abs(a['lower']-target),
                        abs(a['upper']-target), abs(a['slack_up']), abs(a['slack_dn']))
            key = 'storage_hard_anchor_max_err_mwh' if kind == 'E' else 'pumped_hard_anchor_max_err_1e4m3'
            result[key] = max(result[key], error)
            result['hard_anchor_window_count'] += 1
            result['hard_anchor_executed_count'] += int(local == 0)
    result['hard_anchor_evidence_complete'] = complete
    result['storage_pumped_hard_anchors_satisfied'] = bool(complete and
        max(result['storage_hard_anchor_max_err_mwh'], result['pumped_hard_anchor_max_err_1e4m3']) <= tolerance)
    return result
