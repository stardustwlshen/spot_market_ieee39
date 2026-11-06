"""Fail closed on impossible technical parameters and missing rolling actual state."""
import math
import re
from numbers import Real


def number(value, label, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(f'{label} 必须为有限' + ('正数' if positive else '数值'))
    return float(value)



def validate_resource_enabled(units):
    """Validate flags before filtering, so strings/integers cannot change the fleet."""
    for family in ('thermal', 'hydro', 'pumped', 'storage', 'renewable', 'tie'):
        for index, spec in enumerate(units.get(family, []) or []):
            if not isinstance(spec.get('enabled', True), bool):
                raise ValueError(f'{family}[{index}].enabled须为布尔值，禁止以字符串或数字决定启停用')


def _validate_resource_ids(cfg):
    """Bids and model dictionaries share one namespace across active resources."""
    validate_resource_enabled(cfg.units)
    seen = {}
    for family in ('thermal', 'hydro', 'pumped', 'storage', 'renewable', 'tie'):
        for index, spec in enumerate(cfg.units.get(family, [])):
            if not spec.get('enabled', True):
                continue
            label = f'{family}[{index}].id'
            uid = spec.get('id')
            if not isinstance(uid, str) or not uid.strip():
                raise ValueError(f'{label}须为非空字符串')
            if uid in seen:
                raise ValueError(f'资源id {uid!r}重复: {seen[uid]} 与 {label}；启用资源id须全局唯一')
            seen[uid] = label
    # Exported cash accounting uses generated nodal-load and storage legs in
    # the same entity namespace as resource IDs. Reject collisions before solve.
    generated = {'平衡松弛(过剩)'}
    for family, suffixes in (('storage', ('_放电', '_充电')), ('pumped', ('_发电', '_抽水'))):
        for spec in cfg.units.get(family, []):
            if spec.get('enabled', True):
                generated.update(spec['id'] + suffix for suffix in suffixes)
    for uid in seen:
        if re.fullmatch(r'L\d+', uid) or uid in generated:
            raise ValueError(f'资源id {uid!r}与内部结算主体编号冲突，请使用其他编号')


def _validate_hydro_topology(cfg):
    """Full-flow serial cascades only; no aliases, cycles, or undeclared splits."""
    hydro = [h for h in cfg.units.get('hydro', []) if h.get('enabled', True)]
    active_ids = {h['id'] for h in hydro}
    upstream = {}
    for h in hydro:
        uid = h['id']
        lag = number(h.get('lag_periods', 0), f'{uid}.lag_periods')
        if lag < 0 or not lag.is_integer():
            raise ValueError(f'{uid}.lag_periods须为有限非负整数')
        up = h.get('upstream')
        if up is None:
            continue
        if not isinstance(up, str) or up not in active_ids:
            raise ValueError(f'{uid}.upstream={up!r}必须引用启用且已建模的水电站')
        if up == uid:
            raise ValueError(f'{uid}.upstream不能引用自身（水电拓扑环路）')
        upstream[uid] = up
    # Every downstream receives its upstream's complete release in both the
    # market model and the daily-energy suffix. Multiple children would copy
    # that water unless an explicit flow-allocation model were implemented.
    downstream = {}
    for child, parent in upstream.items():
        if parent in downstream:
            raise ValueError(
                f'水电上游 {parent} 对应多个下游 {downstream[parent]}、{child}；'
                '当前仅支持全额出流的串联梯级，分叉须显式分流建模，禁止复制上游水量')
        downstream[parent] = child
    # Each station has at most one upstream and downstream. Walk each chain once.
    done = set()
    for uid in active_ids:
        path = []
        visiting = set()
        node = uid
        while node not in done:
            if node in visiting:
                cycle = path[path.index(node):] + [node]
                raise ValueError('水电upstream拓扑存在环路: ' + ' -> '.join(cycle))
            visiting.add(node)
            path.append(node)
            if node not in upstream:
                break
            node = upstream[node]
        done.update(path)


def _nonnegative(value, label):
    value = number(value, label)
    if value < 0:
        raise ValueError(f'{label}须为有限非负数')
    return value


def _integer(value, label, *, positive=False):
    value = number(value, label, positive=positive)
    if value < 0 or not value.is_integer():
        raise ValueError(f'{label}须为有限' + ('正整数' if positive else '非负整数'))
    return int(value)


def _active(cfg, family):
    return [s for s in cfg.units.get(family, []) if s.get('enabled', True)]


def _boolean(value, label):
    if not isinstance(value, bool):
        raise ValueError(f'{label}须为布尔值，禁止将字符串或数值当作开关')
    return value


def validate_reserve_configuration(cfg):
    """Reject malformed rules before truthiness or coefficients alter procurement."""
    reserve = cfg.mode.get('reserve')
    if not isinstance(reserve, dict):
        raise ValueError('reserve须为配置对象')
    for key, default in (('include_ctg', None), ('storage_reserve', False), ('down_enabled', True)):
        _boolean(reserve.get(key, default), f'reserve.{key}')
    for key in ('rho_L', 'rho_RE', 'rho_L_dn', 'rho_RE_dn'):
        _nonnegative(reserve.get(key), f'reserve.{key}')
    confidence = number(reserve.get('mu_RE', 0.), 'reserve.mu_RE')
    if not 0. <= confidence <= 1.:
        raise ValueError('reserve.mu_RE置信因子须在[0,1]内，不能放大可用新能源余量')
    providers = reserve.get('providers')
    allowed = {'coal', 'gas_ccgt', 'gas_gt', 'hydro'} | {
        spec.get('type') for spec in _active(cfg, 'thermal')
        if isinstance(spec.get('type'), str)}
    if (not isinstance(providers, (list, tuple))
            or any(not isinstance(kind, str) or kind not in allowed for kind in providers)
            or len(providers) != len(set(providers))):
        raise ValueError('reserve.providers须为不重复的受支持常规资源类型列表')
    if (reserve.get('storage_reserve', False)
            or ('hydro' in providers and bool(_active(cfg, 'hydro')))):
        number(reserve.get('T_dur_h'), 'reserve.T_dur_h', positive=True)
    n1 = cfg.base.get('n1_loss_mw')
    if n1 is not None:
        _nonnegative(n1, 'n1_loss_mw')


def validate_operation_configuration(cfg):
    """Configuration flags must retain the explicit meanings selected by users."""
    validate_reserve_configuration(cfg)
    solver = cfg.base.get('solver', {})
    for key, default in (('storage_binary', True), ('tighten', False), ('warm_start', False)):
        _boolean(solver.get(key, default), f'solver.{key}')
    _boolean(cfg.mode.get('intraday', {}).get('enabled', False), 'intraday.enabled')
    _boolean(cfg.mode.get('realtime', {}).get('first_period_actual', True),
             'realtime.first_period_actual')
    _integer((cfg.mode.get('pumped') or {}).get('mode_step_periods', 1),
             'pumped.mode_step_periods', positive=True)


def _thermal_state(spec, state, label):
    if not isinstance(state, dict) or not {'online', 'p0', 'hours'} <= set(state):
        raise ValueError(f'{label}须含online/p0/hours')
    online = state['online']
    if not isinstance(online, (bool, int)) or online not in (0, 1):
        raise ValueError(f'{label}.online须为布尔或0/1')
    power = number(state['p0'], f'{label}.p0')
    _nonnegative(state['hours'], f'{label}.hours')
    low = spec['pmin'] if online else 0.
    high = spec['pmax'] if online else 0.
    if not low - 1e-6 <= power <= high + 1e-6:
        raise ValueError(f'{label}在线状态/出力不一致，p0须在[{low}, {high}] MW')


def hydro_aggregate_power_intervals(zones, online):
    """Exact attainable total MW for identical online units in stable zones.

    Each online unit may occupy a different zone. Repeated interval sums and
    union merging preserve those combinations without assuming equal output.
    Numeric acceptance tolerance belongs only at the final membership check;
    merging must not progressively fill real gaps between attainable totals.
    The caller validates zones and the nonnegative integer online count.
    """
    intervals = [(0., 0.)]
    for _ in range(online):
        candidates = sorted((lower + lo, upper + hi)
                            for lower, upper in intervals for lo, hi in zones)
        merged = []
        for lower, upper in candidates:
            if merged and lower <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], upper))
            else:
                merged.append((lower, upper))
        intervals = merged
    return intervals


def _hydro_initial_power(spec, power, online):
    uid = spec['id']
    count = _integer(online, f'{uid}实际hydro_online')
    if count > spec['n_units']:
        raise ValueError(f'{uid}实际hydro_online超过电站机组台数')
    power = number(power, f'{uid}实际hydro_p')
    intervals = hydro_aggregate_power_intervals(spec['zones'], count)
    if not any(lo - 1e-6 <= power <= hi + 1e-6 for lo, hi in intervals):
        raise ValueError(f'{uid}实际hydro_p={power} MW不在{count}台在线机组的稳定区聚合可达域内')


def hydro_volume_bounds(spec, season):
    """The same reservoir/pondage envelope used by the optimization model."""
    seasonal = spec['seasons'][season]
    if spec.get('regulation', 'reservoir') == 'run_of_river':
        upper = seasonal['v0']
        return upper - (spec.get('pondage', 0) or 0), upper
    return seasonal['vmin'], seasonal['vmax']


def _validate_technical_parameters(cfg, init=None):
    for family in ('thermal', 'hydro', 'pumped', 'storage', 'renewable', 'tie'):
        for spec in _active(cfg, family):
            _integer(spec.get('bus'), f"{spec['id']}.bus", positive=True)
    for g in _active(cfg, 'thermal'):
        uid = g['id']
        maximum = number(g['pmax'], f'{uid}.pmax', positive=True)
        minimum = _nonnegative(g['pmin'], f'{uid}.pmin')
        if minimum > maximum:
            raise ValueError(f'{uid}.pmin不得超过pmax')
        number(g['ramp'], f'{uid}.ramp', positive=True)
        for key in ('ut', 'dt', 'noload'):
            _nonnegative(g[key], f'{uid}.{key}')
        for key, value in g['startup'].items():
            _nonnegative(value, f'{uid}.startup.{key}')
        if init is None:
            _thermal_state(g, g.get('init'), f'{uid}.init')
    for family, key in (('renewable', 'cap'), ('tie', 'pmax')):
        for spec in _active(cfg, family):
            number(spec[key], f"{spec['id']}.{key}", positive=True)
    for s in _active(cfg, 'storage'):
        uid = s['id']
        for key in ('eta_c', 'eta_d'):
            value = number(s[key], f'{uid}.{key}', positive=True)
            if value > 1:
                raise ValueError(f'{uid}.{key} 效率不得超过1')
        for key in ('ecap', 'pmax'):
            number(s[key], f'{uid}.{key}', positive=True)
        lo, hi, initial = (number(s[k], f'{uid}.{k}') for k in ('soc_min', 'soc_max', 'soc0'))
        if not 0 <= lo <= initial <= hi <= 1:
            raise ValueError(f'{uid} SOC技术边界/初值非法')
        if s.get('soc_end_bid') is not None:
            terminal = number(s['soc_end_bid'], f'{uid}.soc_end_bid')
            if not lo <= terminal <= hi:
                raise ValueError(f'{uid}.soc_end_bid超出技术SOC边界')
    for p in _active(cfg, 'pumped'):
        uid = p['id']
        _integer(p['n_units'], f'{uid}.n_units', positive=True)
        eg = number(p['e_gen'], f'{uid}.e_gen', positive=True)
        ep = number(p['e_pump'], f'{uid}.e_pump', positive=True)
        if eg > ep:
            raise ValueError(f'{uid} 抽蓄综合效率e_gen/e_pump不得超过1')
        gmax = number(p['unit_gen_max'], f'{uid}.unit_gen_max', positive=True)
        gmin = _nonnegative(p['unit_gen_min'], f'{uid}.unit_gen_min')
        pmax = number(p['unit_pump'], f'{uid}.unit_pump', positive=True)
        if gmin > gmax:
            raise ValueError(f'{uid}.unit_gen_min不得超过unit_gen_max')
        if not isinstance(p.get('variable_speed', False), bool):
            raise ValueError(f'{uid}.variable_speed须为布尔值')
        if p.get('variable_speed', False):
            pmin = number(p['unit_pump_min'], f'{uid}.unit_pump_min', positive=True)
            if pmin > pmax:
                raise ValueError(f'{uid}.unit_pump_min不得超过unit_pump')
        total = number(p['v_total'], f'{uid}.v_total', positive=True)
        volumes = {key: _nonnegative(p[key], f'{uid}.{key}')
                   for key in ('vup_min', 'vup_max', 'vdn_min', 'vdn_max', 'vup0')}
        if volumes['vup_min'] > volumes['vup_max'] or volumes['vdn_min'] > volumes['vdn_max']:
            raise ValueError(f'{uid}上下库最小库容不得超过最大库容')
        lo = max(volumes['vup_min'], total - volumes['vdn_max'])
        hi = min(volumes['vup_max'], total - volumes['vdn_min'])
        if lo > hi or (init is None and not lo <= volumes['vup0'] <= hi):
            raise ValueError(f'{uid}.vup0超出上下库技术边界或上下库边界不相容')
        for key in ('vup_end', 'vup_end_bid'):
            if p.get(key) is not None and not lo <= number(p[key], f'{uid}.{key}') <= hi:
                raise ValueError(f'{uid}.{key}超出上下库技术边界')
        for key in ('switch_gap', 'max_gen_starts', 'max_pump_starts'):
            _integer(p.get(key, 1) if key == 'switch_gap' else p[key], f'{uid}.{key}')
        for key in ('start_cost_gen', 'start_cost_pump'):
            _nonnegative(p[key], f'{uid}.{key}')
        if p.get('init_mode', 'idle') not in ('idle', 'gen', 'pump'):
            raise ValueError(f'{uid}.init_mode须为idle/gen/pump')
    for h in _active(cfg, 'hydro'):
        uid = h['id']
        count = _integer(h['n_units'], f'{uid}.n_units', positive=True)
        maximum = number(h['unit_pmax'], f'{uid}.unit_pmax', positive=True)
        number(h['water_rate'], f'{uid}.water_rate', positive=True)
        number(h.get('ramp'), f'{uid}.ramp', positive=True)
        online = _integer(h.get('init_units_on'), f'{uid}.init_units_on')
        initial_power = _nonnegative(h.get('init_p_unit'), f'{uid}.init_p_unit')
        if online > count or initial_power > maximum:
            raise ValueError(f'{uid}初始出力/在线台数超出技术边界')
        zones = h.get('zones')
        if not isinstance(zones, (list, tuple)) or not zones:
            raise ValueError(f'{uid}.zones须为非空稳定运行区列表')
        previous = -1.
        for i, zone in enumerate(zones):
            if not isinstance(zone, (list, tuple)) or len(zone) != 2:
                raise ValueError(f'{uid}.zones[{i}]须为[下限,上限]')
            low, high = (number(x, f'{uid}.zones[{i}]') for x in zone)
            if not 0 <= low <= high <= maximum or low < previous:
                raise ValueError(f'{uid}.zones须有序不重叠且位于[0,unit_pmax]')
            previous = high
        if online and not any(low - 1e-6 <= initial_power <= high + 1e-6 for low, high in zones):
            raise ValueError(f'{uid}.init_p_unit不在声明稳定运行区内')
        regulation = h.get('regulation', 'reservoir')
        if regulation not in ('reservoir', 'run_of_river'):
            raise ValueError(f'{uid}.regulation须为reservoir/run_of_river')
        qout = h.get('qout_max')
        if qout is not None:
            _nonnegative(qout, f'{uid}.qout_max')
        if h.get('e_day_min') is not None:
            _nonnegative(h['e_day_min'], f'{uid}.e_day_min')
        seasons = h.get('seasons')
        if not isinstance(seasons, dict) or cfg.base['season'] not in seasons:
            raise ValueError(f'{uid}.seasons缺少所选季节{cfg.base["season"]}')
        for season, seasonal in seasons.items():
            label = f'{uid}.seasons.{season}'
            if not isinstance(seasonal, dict):
                raise ValueError(f'{label}须为季节参数对象')
            for key in ('qin', 'v0', 'vmin', 'vmax', 'v_end', 'qeco'):
                _nonnegative(seasonal.get(key), f'{label}.{key}')
            if not seasonal['vmin'] <= seasonal['v0'] <= seasonal['vmax']:
                raise ValueError(f'{label}.v0超出水库技术边界')
            if not seasonal['vmin'] <= seasonal['v_end'] <= seasonal['vmax']:
                raise ValueError(f'{label}.v_end超出水库技术边界')
            if qout is not None and seasonal['qeco'] > qout:
                raise ValueError(f'{label}.qeco超过qout_max')
            if seasonal.get('e_day') is not None:
                upper = _nonnegative(seasonal['e_day'], f'{label}.e_day')
                if regulation == 'reservoir' and h.get('e_day_min') is not None and h['e_day_min'] > upper:
                    raise ValueError(f'{label}.e_day小于e_day_min')
            if regulation == 'run_of_river':
                pondage = _nonnegative(h.get('pondage', 0) or 0, f'{uid}.pondage')
                if pondage > seasonal['v0']:
                    raise ValueError(f'{uid}.pondage不得超过初始库容')


def intraday_lead_periods(cfg):
    """Only a nonnegative integral forecast issue lead is meaningful."""
    return _integer(cfg.mode.get('intraday', {}).get('lead_issue_periods', 1),
                    'intraday.lead_issue_periods')


def validate_renewable_profiles(cfg):
    """The same profile/type choices enforced by the unit editor apply to APIs."""
    allowed = {'wind': {'wind_a', 'wind_b'}, 'pv': {'pv'}}
    for spec in _active(cfg, 'renewable'):
        if spec.get('type') not in allowed or spec.get('profile') not in allowed[spec['type']]:
            raise ValueError(f"{spec['id']}.profile 与新能源类型不匹配或不受支持")


def validate_time_grid(cfg):
    """Reject inconsistent clocks instead of rounding a different market window."""
    dt = number(cfg.base['dt_h'], 'dt_h', positive=True)
    rc = cfg.mode.get('realtime', {})
    interval = number(rc.get('interval_min'), 'realtime.interval_min', positive=True)
    if not math.isclose(interval, 60. * dt, rel_tol=0., abs_tol=1e-9):
        raise ValueError('realtime.interval_min必须与60×dt_h一致，不能改变窗口实际时长')
    def steps(value, label):
        count = number(value, label, positive=True) / dt
        if not math.isclose(count, round(count), rel_tol=0., abs_tol=1e-9) or round(count) < 1:
            raise ValueError(f'{label}必须为dt_h的正整数倍，不能四舍五入')
        return int(round(count))
    steps(rc.get('lookahead_h'), 'realtime.lookahead_h')
    binding = rc.get('binding_periods', 1)
    if isinstance(binding, bool) or binding != 1:
        raise ValueError('realtime.binding_periods仅支持1，每轮只执行首时段')
    ic = cfg.mode.get('intraday', {})
    if ic.get('enabled', False):
        steps(ic.get('interval_h'), 'intraday.interval_h')
        intraday_lead_periods(cfg)


def validate_production_day(cfg):
    """Profile preparation models one calendar day; debug runs use periods prefix."""
    validate_time_grid(cfg)
    count = _integer(cfg.base['periods'], 'periods', positive=True)
    dt = number(cfg.base['dt_h'], 'dt_h', positive=True)
    if not math.isclose(count * dt, 24., rel_tol=0., abs_tol=1e-9):
        raise ValueError('完整生产日要求periods×dt_h=24；短段调试请使用运行periods参数')


def validate_clearing_limits(limits):
    lo = number(limits.get('clear_min'), 'price_limits.clear_min')
    hi = number(limits.get('clear_max'), 'price_limits.clear_max')
    if lo > hi:
        raise ValueError('price_limits.clear_min不得大于clear_max')
    return lo, hi


def validate_solver_scheduler(cfg):
    """The HiGHS global worker pool must agree between MIP and pricing LP."""
    solver=cfg.base.get('solver',{})
    if not isinstance(solver,dict):raise ValueError('solver must be a configuration object')
    raw_extra=solver.get('highs_extra')
    if raw_extra is not None and not isinstance(raw_extra,dict):
        raise ValueError('solver.highs_extra must be an object or null')
    threads=solver.get('threads',4)
    if isinstance(threads,bool) or not isinstance(threads,int) or not 1<=threads<=2147483647:
        raise ValueError('solver.threads must be a positive integer within 1..2147483647; automatic pool sizing is not supported')
    extra=solver.get('highs_extra') or {}
    mip_threads=extra.get('threads',threads)
    if type(mip_threads) is not int or mip_threads!=threads:
        raise ValueError('HiGHS MIP and LP thread budgets must agree; highs_extra.threads cannot override solver.threads')
    return threads


def validate_physical_inputs(cfg, init=None):
    validate_solver_scheduler(cfg)
    _validate_resource_ids(cfg)
    validate_operation_configuration(cfg)
    validate_clearing_limits(cfg.mode.get("price_limits", {}))
    validate_renewable_profiles(cfg)
    validate_time_grid(cfg)
    _integer(cfg.base['periods'], 'periods', positive=True)
    _validate_hydro_topology(cfg)
    _validate_technical_parameters(cfg, init=init)
    from .hydro_commitment import validate_configuration as validate_hydro_commitment
    validate_hydro_commitment(cfg)
    if init is None:
        return
    if not isinstance(init, dict):
        raise ValueError('滚动实际状态必须为对象')
    thermal = init.get('thermal', {})
    if not isinstance(thermal, dict) or set(thermal) != {x['id'] for x in _active(cfg, 'thermal')}:
        raise ValueError('滚动实际thermal状态必须完整，不能退回技术初值')
    for spec in _active(cfg, 'thermal'):
        _thermal_state(spec, thermal[spec['id']], f"{spec['id']}实际thermal状态")
    hydro = _active(cfg, 'hydro')
    expected_hydro = {spec['id'] for spec in hydro}
    for name in ('hydro_p', 'hydro_online'):
        values = init.get(name, {})
        if not isinstance(values, dict) or set(values) != expected_hydro:
            raise ValueError(f'滚动实际{name}必须完整包含所有启用水电，不能退回技术初值')
    for spec in hydro:
        _hydro_initial_power(spec, init['hydro_p'][spec['id']], init['hydro_online'][spec['id']])
    for family in ('storage', 'pumped', 'hydro'):
        expected = {x['id'] for x in _active(cfg, family)}
        states = init.get(family, {})
        if not isinstance(states, dict) or set(states) != expected:
            raise ValueError(f'滚动实际{family}状态必须完整包含所有启用资源，不能退回技术初值')
        for spec in _active(cfg, family):
            uid, state = spec['id'], states[spec['id']]
            if family == 'storage':
                energy = number(state, f'{uid}实际E')
                if not spec['soc_min'] * spec['ecap'] - 1e-6 <= energy <= spec['soc_max'] * spec['ecap'] + 1e-6:
                    raise ValueError(f'{uid}实际E超出技术SOC边界')
            elif family == 'hydro':
                volume = number(state, f'{uid}实际水库库容')
                lo, hi = hydro_volume_bounds(spec, cfg.base['season'])
                if not lo - 1e-6 <= volume <= hi + 1e-6:
                    raise ValueError(f'{uid}实际水库库容超出技术边界')
            else:
                if not isinstance(state, dict) or not {'V', 'ng', 'np', 'xg', 'xp'} <= set(state):
                    raise ValueError(f'{uid}实际抽蓄状态须完整V/ng/np/xg/xp')
                vals = {k: number(state[k], f'{uid}.{k}') for k in ('V', 'ng', 'np', 'xg', 'xp')}
                lo = max(spec['vup_min'], spec['v_total'] - spec['vdn_max'])
                hi = min(spec['vup_max'], spec['v_total'] - spec['vdn_min'])
                if not lo - 1e-6 <= vals['V'] <= hi + 1e-6:
                    raise ValueError(f'{uid}实际上库库容超出上下库技术边界')
                if (any(vals[k] != round(vals[k]) for k in ('ng', 'np', 'xg', 'xp'))
                        or vals['xg'] not in (0, 1) or vals['xp'] not in (0, 1) or vals['xg'] + vals['xp'] > 1):
                    raise ValueError(f'{uid}实际工况/台数非法')
                if not (vals['xg'] <= vals['ng'] <= spec['n_units'] * vals['xg']
                        and vals['xp'] <= vals['np'] <= spec['n_units'] * vals['xp']):
                    raise ValueError(f'{uid}实际工况与台数不一致')


def validate_network_inputs(cfg, net):
    """Reject detached resources/loads before network comprehensions can omit them."""
    import numpy as np

    buses = list(net.buses)
    if not buses or len(buses) != len(set(buses)):
        raise ValueError('网络母线编号须非空且唯一')
    for bus in buses:
        _integer(bus, 'network.bus', positive=True)
    if net.ref_bus not in buses:
        raise ValueError('网络参考母线不存在')
    branches = net.branches
    if not {'branch_id', 'rate_mw'} <= set(branches.columns):
        raise ValueError('支路须包含branch_id/rate_mw')
    ids = list(branches.branch_id)
    if (len(ids) != len(set(ids))
            or any(not isinstance(uid, str) or not uid.strip() for uid in ids)):
        raise ValueError('网络支路branch_id须为唯一非空字符串')
    for uid, rate in zip(ids, branches.rate_mw):
        number(rate, f'{uid}.rate_mw', positive=True)
    matrix = np.asarray(net.ptdf)
    if (matrix.shape != (len(ids), len(buses)) or not np.issubdtype(matrix.dtype, np.number)
            or np.issubdtype(matrix.dtype, np.complexfloating) or not np.isfinite(matrix).all()):
        raise ValueError('PTDF须为支路数×母线数的有限数值矩阵')
    if not np.allclose(matrix[:, buses.index(net.ref_bus)], 0., atol=1e-10, rtol=0.):
        raise ValueError('PTDF参考母线列须为0')
    if not net.load_share.index.is_unique or set(net.load_share.index) != set(buses):
        raise ValueError('冻结基准负荷占比须完整对应唯一网络母线')
    _series(net.load_share.to_numpy(), len(buses), 'network.load_share')
    from .fixed_load import allocation_weights
    allocation_weights(net)
    number(net.base_load_mw, 'network.base_load_mw', positive=True)
    for family in ('thermal', 'hydro', 'pumped', 'storage', 'renewable', 'tie'):
        for spec in _active(cfg, family):
            if spec['bus'] not in buses:
                raise ValueError(f"{spec['id']}.bus={spec['bus']}不在网络母线中，禁止静默忽略资源")


def _series(values, length, label, *, maximum=None):
    """Do not coerce strings/booleans or truncate surplus periods."""
    import numpy as np

    array = np.asarray(values)
    if (array.shape != (length,) or not np.issubdtype(array.dtype, np.number)
            or np.issubdtype(array.dtype, np.complexfloating)
            or not np.isfinite(array).all() or (array < 0).any()):
        raise ValueError(f'{label}须为{length}段有限非负数值（禁止布尔/字符串/缺失/多余时段）')
    if maximum is not None and (array > maximum + 1e-6).any():
        raise ValueError(f'{label}超出技术容量{maximum} MW，禁止自动截断')
    return array


def validate_case_inputs(case):
    """Every mutable direct/rolling case is checked at each model construction."""
    from .fixed_load import tie_mode

    cfg, net = case.cfg, case.net
    if getattr(case,'actual_state_required',False) and not isinstance(case.init,dict):
        raise ValueError('Continuous DA/ID/RT model requires its explicit window initial state; configuration fallback is prohibited')
    for family in ('thermal', 'hydro', 'pumped', 'storage', 'renewable', 'tie'):
        if any(not spec.get('enabled', True) for spec in cfg.units.get(family, [])):
            raise ValueError(f'case.cfg.units.{family}包含停用资源；请先通过filter_enabled过滤，禁止模型继续调度停用资源')
    validate_physical_inputs(cfg, case.init)
    validate_network_inputs(cfg, net)
    horizon = cfg.T if case.T is None else _integer(case.T, 'case.T', positive=True)
    _integer(case.t0, 'case.t0')
    # Standalone synthetic windows may carry an absolute clock independently of
    # the profile length. Consumers of day-wide arrays validate their own indices.
    for name in ('load_bus', 'ren_fc', 'tie', 'fixed_load', 'fixed'):
        if not isinstance(getattr(case, name), dict):
            raise ValueError(f'case.{name}须为映射对象')
    if case.terminal is not None and not isinstance(case.terminal, dict):
        raise ValueError('case.terminal须为对象或None')
    for bus, values in case.load_bus.items():
        _integer(bus, 'load_bus.bus', positive=True)
        if bus not in net.buses:
            raise ValueError(f'load_bus[{bus}]不在网络母线中，禁止静默丢弃负荷')
        _series(values, horizon, f'load_bus[{bus}]')
    renewable = {r['id']: r for r in _active(cfg, 'renewable')}
    if set(case.ren_fc) != set(renewable):
        raise ValueError('ren_fc须完整且仅包含启用新能源资源')
    for uid, values in case.ren_fc.items():
        _series(values, horizon, f'ren_fc[{uid}]', maximum=renewable[uid]['cap'])
    for name in ('R_up', 'R_dn'):
        _series(getattr(case, name), horizon, name)
    expected_ties = {r['id']: r for r in _active(cfg, 'tie')} if tie_mode(cfg) == 'supply' else {}
    if set(case.tie) != set(expected_ties):
        raise ValueError('tie须完整对应区外来电资源；固定负荷模式不得再次注入')
    for uid, value in case.tie.items():
        if not isinstance(value, (tuple, list)) or len(value) != 2:
            raise ValueError(f'tie[{uid}]须为(母线,曲线)')
        bus, values = value
        if bus != expected_ties[uid]['bus']:
            raise ValueError(f'tie[{uid}]母线与资源配置不一致')
        _series(values, horizon, f'tie[{uid}]', maximum=expected_ties[uid]['pmax'])
    for uid, value in case.fixed_load.items():
        if not isinstance(value, (tuple, list)) or len(value) != 2 or value[0] not in net.buses:
            raise ValueError(f'fixed_load[{uid}]须为(有效母线,曲线)')
        _series(value[1], horizon, f'fixed_load[{uid}]')


def read_input_csv(path, **kwargs):
    """Inspect original CSV structure before pandas can rename duplicate headers.

    Validate and parse the same bytes, including row widths: pandas otherwise
    can interpret an extra leading field as an index and shift boundary values.
    """
    import csv
    import io
    from pathlib import Path
    import pandas as pd

    text = Path(path).read_text(encoding='utf-8-sig')
    try:
        rows = csv.reader(io.StringIO(text), strict=True)
        header = next(rows, [])
        if not header or any(not name.strip() for name in header):
            raise ValueError(f'{path}: CSV表头须为非空列名')
        if len(header) != len(set(header)):
            raise ValueError(f'{path}: CSV原始表头列名重复，禁止自动重命名')
        for row in rows:
            if row and len(row) != len(header):
                raise ValueError(f'{path}: CSV第{rows.line_num}行列数与表头不一致，禁止自动移动索引')
    except csv.Error as exc:
        raise ValueError(f'{path}: CSV结构非法: {exc}') from exc
    return pd.read_csv(io.StringIO(text), **kwargs)


def validate_requested_periods(periods):
    """Reject malformed debug horizons before any expensive solve or write."""
    from numbers import Integral
    if periods is not None and (isinstance(periods, bool) or not isinstance(periods, Integral) or periods <= 0):
        raise ValueError('periods必须为正整数或None，不能为布尔、零、负数或小数')
