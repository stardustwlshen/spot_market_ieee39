"""Identified hydro minimum times, exact clocks, and executed-prefix state.

Legacy aggregate stations remain explicit legacy. A physical daily-energy suffix
shares this block; an otherwise pure commitment suffix is projected away.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_FLOOR
from hashlib import sha256
import json
from numbers import Integral, Real
import re
from zoneinfo import ZoneInfo

VERSION = 1
MODE = 'unit_duration_v1'
SOURCES = {'research_assumption', 'executed', 'forecast'}
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def number(value, label):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f'{label}须为有限非负数')
    out = Decimal(str(value))
    if not out.is_finite() or out < 0:
        raise ValueError(f'{label}须为有限非负数')
    return out


def seconds(stamp):
    if not isinstance(stamp, str):
        raise ValueError('水电state_at/required_until须为带时区的ISO时间')
    match = re.search(r'(\.\d+)(?=Z$|[+-]\d\d:\d\d$)', stamp)
    fraction = Decimal(match.group()) if match else Decimal(0)
    plain = stamp[:match.start()] + stamp[match.end():] if match else stamp
    try:
        value = datetime.fromisoformat(plain.replace('Z', '+00:00'))
        if value.tzinfo is None:
            raise ValueError('missing timezone')
        delta = value.astimezone(timezone.utc) - _EPOCH
        return Decimal(delta.days * 86400 + delta.seconds) + fraction
    except (ValueError, OverflowError) as exc:
        raise ValueError('水电state_at/required_until须为带时区的ISO时间') from exc


def stamp(value):
    value = Decimal(value)
    whole = int(value.to_integral_value(rounding=ROUND_FLOOR))
    fraction = value - whole
    tail = format(fraction, 'f')[1:].rstrip('0') if fraction else ''
    return (_EPOCH + timedelta(seconds=whole)).strftime('%Y-%m-%dT%H:%M:%S') + tail + 'Z'


def window_time(cfg, t0=0):
    if isinstance(t0, bool) or not isinstance(t0, Integral) or not 0 <= t0 <= cfg.T:
        raise ValueError('水电边界索引须为本日0至T的整数')
    try:
        day = datetime.fromisoformat(str(cfg.base['date'])).replace(tzinfo=ZoneInfo(cfg.base.get('simulation_timezone', 'UTC')))
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError('水电初态需要有效运行日期/模拟时区') from exc
    return seconds(day.isoformat()) + number(cfg.dt, 'dt_h') * Decimal(3600) * int(t0)


def enabled(spec):
    c = spec.get('commitment')
    return isinstance(c, dict) and c.get('mode') == MODE


def specs(cfg):
    return {h['id']: h for h in cfg.units.get('hydro', []) if h.get('enabled', True) and enabled(h)}


def research_initial(spec, *, elapsed_h=1.):
    """Explicit UI/template assumption, never inferred execution history."""
    return dict(schema_version=VERSION, mode=MODE, initial_source='research_assumption',
                initial_units=[dict(unit_id=f"{spec['id']}_U{i+1:02d}", online=i<int(spec['init_units_on']),
                                    elapsed_h=float(elapsed_h), elapsed_kind='exact')
                               for i in range(int(spec['n_units']))])


def rule_hash(spec):
    data = {k: spec[k] for k in ('id', 'n_units', 'ut', 'dt')}
    data['unit_ids'] = [r['unit_id'] for r in spec['commitment']['initial_units']]
    return sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate_configuration(cfg):
    for h in cfg.units.get('hydro', []):
        if not h.get('enabled', True):
            continue
        c = h.get('commitment')
        if c is None:
            continue
        if not isinstance(c, dict) or c.get('mode') not in ('legacy_aggregate', MODE):
            raise ValueError(f"水电{h['id']} commitment.mode非法")
        if c.get('mode') == 'legacy_aggregate':
            continue
        if type(c.get('schema_version')) is not int or c['schema_version'] != VERSION:
            raise ValueError(f"水电{h['id']} commitment.schema_version不支持")
        if c.get('initial_source') != 'research_assumption':
            raise ValueError('配置初态只能为明确研究假设；实际接续须通过case.init，不可覆盖历史')
        for key in ('ut', 'dt'):
            number(h.get(key), f"{h['id']}.{key}")
        rows = c.get('initial_units')
        if not isinstance(rows, list) or len(rows) != h['n_units']:
            raise ValueError(f"水电{h['id']}逐台初态条数须等于n_units，禁止自动补年龄")
        ids = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('水电逐台初态须为对象')
            uid = row.get('unit_id')
            if not isinstance(uid, str) or not uid.strip() or uid != uid.strip():
                raise ValueError('水电逐台unit_id须为非空稳定编号')
            ids.append(uid)
            _status(row, f"{h['id']}.{uid}")
        if len(ids) != len(set(ids)):
            raise ValueError(f"水电{h['id']}逐台unit_id重复")
        if sum(r['online'] for r in rows) != h['init_units_on']:
            raise ValueError(f"水电{h['id']}逐台online合计与init_units_on不一致")


def _status(row, label):
    if type(row.get('online')) is not bool:
        raise ValueError(f'{label}.online须为布尔值')
    number(row.get('elapsed_h'), f'{label}.elapsed_h')
    if row.get('elapsed_kind') not in ('exact', 'lower_bound'):
        raise ValueError(f'{label}.elapsed_kind须为exact或lower_bound，未知时长不可假填')


def initial_state(cfg):
    validate_configuration(cfg)
    active = specs(cfg)
    if not active:
        return {}
    now = window_time(cfg)
    out = dict(schema_version=VERSION, state_at=stamp(now), source='research_assumption', stations={})
    for h, spec in active.items():
        rows = {}
        for supplied in spec['commitment']['initial_units']:
            row = deepcopy(supplied)
            uid = row.pop('unit_id')
            minimum = number(spec['ut' if row['online'] else 'dt'], f'{h}.minimum')
            row.update(required_until=stamp(now + max(Decimal(0), minimum-number(row['elapsed_h'], 'elapsed_h'))*3600),
                       origin_rule_hash=rule_hash(spec), origin_minimum_h=float(minimum))
            rows[uid] = row
        out['stations'][h] = dict(units=rows)
    return out


def normalize_state(cfg, supplied, t0=0, *, online_counts=None):
    validate_configuration(cfg)
    active = specs(cfg)
    if not active:
        if supplied:
            raise ValueError('未启用逐台约束却提供hydro_commitment实际状态')
        return {}
    if not isinstance(supplied, dict) or type(supplied.get('schema_version')) is not int or supplied.get('schema_version') != VERSION:
        raise ValueError('缺少完整hydro_commitment初态，不能从总在线台数恢复逐台年龄')
    out = deepcopy(supplied)
    now = seconds(out.get('state_at'))
    if now != window_time(cfg, t0):
        raise ValueError('hydro_commitment.state_at与本窗口开始时刻不一致')
    if out.get('source') not in SOURCES:
        raise ValueError('hydro_commitment.source须明确research_assumption/executed/forecast')
    if out['source'] == 'forecast':
        if seconds(out.get('issued_at')) > now:
            raise ValueError('预测初态issued_at不能晚于state_at')
    if not isinstance(out.get('stations'), dict) or set(out['stations']) != set(active):
        raise ValueError('hydro_commitment须完整且仅包含启用逐台约束的水电站')
    for h, spec in active.items():
        station = out['stations'][h]
        if not isinstance(station, dict):
            raise ValueError(f'水电{h}状态须为对象')
        rows = station.get('units')
        expected = {r['unit_id'] for r in spec['commitment']['initial_units']}
        if not isinstance(rows, dict) or set(rows) != expected:
            raise ValueError(f'水电{h}逐台身份缺失/变更，禁止排序猜测或重用历史')
        for uid, row in rows.items():
            if not isinstance(row, dict):
                raise ValueError('水电逐台状态须为对象')
            _status(row, f'{h}.{uid}')
            old_until = seconds(row.get('required_until'))
            if not isinstance(row.get('origin_rule_hash'), str) or not row['origin_rule_hash']:
                raise ValueError(f'{h}.{uid}缺少已有承诺来源版本')
            origin = number(row.get('origin_minimum_h'), f'{h}.{uid}.origin_minimum_h')
            origin_remaining = origin - number(row['elapsed_h'], 'elapsed_h')
            if origin_remaining > 0 and old_until < now + origin_remaining * 3600:
                raise ValueError(f'{h}.{uid}已有承诺截止早于原始规则残余时长，禁止静默清空')
            minimum = number(spec['ut' if row['online'] else 'dt'], f'{h}.minimum')
            remaining = minimum - number(row['elapsed_h'], 'elapsed_h')
            new_until = now + remaining*3600
            if remaining > 0 and new_until > old_until:
                row.update(required_until=stamp(new_until), origin_rule_hash=rule_hash(spec),
                           origin_minimum_h=float(minimum))
        if online_counts is not None and sum(r['online'] for r in rows.values()) != online_counts.get(h):
            raise ValueError(f'水电{h}逐台online合计与实际hydro_online不一致')
    return out


def attach(m):
    import pyomo.environ as pyo
    cfg, case = m._case.cfg, m._case
    active = specs(cfg)
    if not active:
        return
    supplied = initial_state(cfg) if case.init is None else case.init.get('hydro_commitment')
    initial = normalize_state(cfg, supplied, case.t0,
                              online_counts=None if case.init is None else case.init.get('hydro_online'))
    suffix = getattr(m, 'hydro_daily_energy_suffix', None)
    end = max(m._md.T, max(suffix.F)+1) if suffix is not None and len(suffix.F) else m._md.T
    b = m.hydro_commitment = pyo.Block(concrete=True)
    b.I = pyo.Set(dimen=2, initialize=[(h, uid) for h in active for uid in initial['stations'][h]['units']])
    b.K = pyo.RangeSet(0, end-1)
    b.u = pyo.Var(b.I, b.K, domain=pyo.Binary)
    b.start = pyo.Var(b.I, b.K, domain=pyo.Binary)
    b.stop = pyo.Var(b.I, b.K, domain=pyo.Binary)
    b._integer_names = ('u', 'start', 'stop')
    b._initial_state = initial
    b._representation = 'physical_suffix_shared_commitment' if suffix is not None else 'window_only_constant_witness'
    step, now = number(cfg.dt, 'dt_h'), seconds(initial['state_at'])
    rows = initial['stations']
    def previous(h, uid, k):
        return int(rows[h]['units'][uid]['online']) if k == 0 else b.u[h, uid, k-1]
    b.transition = pyo.Constraint(b.I, b.K, rule=lambda b,h,i,k: b.u[h,i,k]-previous(h,i,k)==b.start[h,i,k]-b.stop[h,i,k])
    b.exclusive = pyo.Constraint(b.I, b.K, rule=lambda b,h,i,k: b.start[h,i,k]+b.stop[h,i,k]<=1)
    def duration(b,h,i,k,kind):
        minimum = number(active[h]['ut' if kind == 'start' else 'dt'], f'{h}.minimum')
        events = getattr(b,kind)
        return sum(events[h,i,j] for j in range(k+1) if Decimal(k-j)*step < minimum) <= (b.u[h,i,k] if kind=='start' else 1-b.u[h,i,k])
    b.min_up = pyo.Constraint(b.I,b.K,rule=lambda b,h,i,k:duration(b,h,i,k,'start'))
    b.min_down = pyo.Constraint(b.I,b.K,rule=lambda b,h,i,k:duration(b,h,i,k,'stop'))
    def residual(b,h,i,k):
        row=rows[h]['units'][i]
        return b.u[h,i,k]==int(row['online']) if now+Decimal(k)*step*3600 < seconds(row['required_until']) else pyo.Constraint.Skip
    b.initial_residual = pyo.Constraint(b.I,b.K,rule=residual)
    b.counts = pyo.ConstraintList()
    for h in active:
        for k in b.K:
            owner=m if k < m._md.T else suffix
            b.counts.add(sum(b.u[h,i,k] for i in rows[h]['units']) == sum(owner.delta[h,z,k] for z in range(len(active[h]['zones']))))
    # Open day endpoint: never add a midnight cutoff or declare pending fulfilled.


def snapshot(m):
    import numpy as np
    import pyomo.environ as pyo
    b=getattr(m,'hydro_commitment',None)
    if b is None:
        return {}
    values={key:{(h,i):np.array([int(round(pyo.value(getattr(b,name)[h,i,k]))) for k in m.T])
                 for h,i in b.I} for key,name in (('hydro_u','u'),('hydro_start','start'),('hydro_stop','stop'))}
    values['hydro_commitment']=dict(schema_version=VERSION,initial_state=deepcopy(b._initial_state),
        representation=b._representation,commitment_periods=len(b.K),window_periods=len(m.T),
        terminal_policy='carry_pending',quantity_source=getattr(m,'_hydro_quantity_source','unverified'),
        market_stage=m._case.stage,pure_witness_executes=False)
    projected = deepcopy(b._initial_state)
    for k in m.T:
        projected = _advance(m._case.cfg, projected, values, k)
    values['hydro_commitment']['pending_window_end'] = pending(projected)
    values['hydro_commitment']['pending_window_end_executes'] = False
    return values


def _binary(value, label):
    v=number(value,label)
    integer=int(round(v))
    if integer not in (0,1) or abs(v-integer)>Decimal('0.000001'):
        raise ValueError(f'{label}须为二元值')
    return integer


def _advance(cfg, state, snap, k=0):
    out=deepcopy(state)
    now=seconds(state['state_at']);step=number(cfg.dt,'dt_h')
    for h,spec in specs(cfg).items():
        for uid,row in out['stations'][h]['units'].items():
            key=h,uid
            try:
                on=_binary(snap['hydro_u'][key][k],f'{key}.u')
                start=_binary(snap['hydro_start'][key][k],f'{key}.start')
                stop=_binary(snap['hydro_stop'][key][k],f'{key}.stop')
            except (KeyError,IndexError,TypeError) as exc:
                raise ValueError(f'{key}缺少完整逐台执行u/start/stop') from exc
            previous=int(row['online'])
            if on-previous!=start-stop or start+stop>1:
                raise ValueError(f'{key}执行启停逻辑不一致')
            if on!=previous:
                if now < seconds(row['required_until']):
                    raise ValueError(f"{key}不得早于{row['required_until']}反向切换")
                minimum=number(spec['ut' if on else 'dt'],'minimum')
                row.update(online=bool(on),elapsed_h=float(step),elapsed_kind='exact',
                           required_until=stamp(now+minimum*3600),origin_minimum_h=float(minimum),origin_rule_hash=rule_hash(spec))
            else:
                row['elapsed_h']=float(number(row['elapsed_h'],'elapsed_h')+step)
    out['state_at']=stamp(now+step*3600)
    return out


def advance(cfg, supplied, snap, t0):
    if not specs(cfg):
        return {}
    if isinstance(t0, bool) or not isinstance(t0, Integral) or not 0 <= t0 < cfg.T:
        raise ValueError('水电执行首段索引须为本日0至T-1的整数')
    state=normalize_state(cfg,supplied,t0)
    evidence=snap.get('hydro_commitment',{})
    if state['source']=='forecast':
        raise ValueError('预测hydro_commitment初态不得推进实际执行链')
    if evidence.get('quantity_source')!='energy_sced' or evidence.get('market_stage')!='RT':
        raise ValueError('仅RT正式energy_sced首段可推进水电实际承诺')
    if evidence.get('initial_state') != state:
        raise ValueError('水电执行快照初态与真实承诺不一致')
    out=_advance(cfg,state,snap)
    for h, spec in specs(cfg).items():
        count=sum(r['online'] for r in out['stations'][h]['units'].values())
        keys={(h,z) for z in range(len(spec['zones']))}
        if {key for key in snap.get('delta',{}) if key[0]==h} != keys:
            raise ValueError(f'{h}执行稳定区台数覆盖不完整')
        total=0
        for key in keys:
            value=number(snap['delta'][key][0],f'{h}.delta')
            integer=int(round(value))
            if abs(value-integer)>Decimal('0.000001') or integer>spec['n_units']:
                raise ValueError(f'{h}执行稳定区台数须为合法整数')
            total+=integer
        if count!=total:
            raise ValueError(f'{h}逐台执行合计与稳定区台数不一致')
    out['source']='executed'
    return out


def state_from_plan(cfg, plan, t):
    if not specs(cfg):
        return {}
    if isinstance(t, bool) or not isinstance(t, Integral) or not 0 <= t <= cfg.T:
        raise ValueError('水电计划重放长度须为本日0至T的整数')
    if 'hydro_commitment' not in plan:
        raise ValueError('计划没有逐台承诺，不能从总台数推回历史')
    state=normalize_state(cfg,plan['hydro_commitment']['initial_state'],0)
    for k in range(t):
        state=_advance(cfg,state,plan,k)
    state.update(source='research_assumption',assumption='plan_executed_not_observed')
    return state


def pending(state):
    if not state:
        return []
    now=seconds(state['state_at'])
    return [dict(station=h,unit_id=i,online=r['online'],required_until=r['required_until'],
                 remaining_h=float(max(Decimal(0),seconds(r['required_until'])-now)/3600),elapsed_kind=r['elapsed_kind'])
            for h,s in state['stations'].items() for i,r in s['units'].items()]


def audit_execution(cfg, initial, records):
    """Replay official first periods, independently of model constraint bodies."""
    state = normalize_state(cfg, initial, 0)
    if not specs(cfg):
        return dict(active=False, valid=True, terminal_state={}, pending=[])
    if state['source'] == 'forecast':
        raise ValueError('预测初态不是真实执行审计起点')
    if not isinstance(records, (list, tuple)) or len(records) > cfg.T:
        raise ValueError('水电执行记录须为不超过本日T段的记录序列')
    for expected_t, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError('水电执行记录须为对象')
        if type(record.get('t')) is not int or record['t'] != expected_t:
            raise ValueError('水电执行记录须为无重复/缺段的连续前缀')
        state = advance(cfg, state, record['snap'], expected_t)
    return dict(active=True, valid=True, executed_periods=len(records),
                terminal_state=state, pending=pending(state),
                future_fulfillment_verified=False)
