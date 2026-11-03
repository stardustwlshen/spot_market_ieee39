"""Fail closed on impossible technical parameters and missing rolling actual state."""
import math
from numbers import Real


def number(value, label, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(f'{label} 必须为有限' + ('正数' if positive else '数值'))
    return float(value)



def _validate_resource_ids(cfg):
    """Bids and model dictionaries share one namespace across active resources."""
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


def _validate_hydro_topology(cfg):
    """Only active modeled stations can supply upstream releases; no external aliases."""
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
    # Each station has at most one upstream reference. Walk each chain once.
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


def validate_physical_inputs(cfg, init=None):
    _validate_resource_ids(cfg)
    dt=number(cfg.base['dt_h'], 'dt_h', positive=True)
    T=cfg.base['periods']
    if isinstance(T,bool) or not isinstance(T,Real) or T != int(T) or T <= 0:raise ValueError('periods须为正整数')
    for s in cfg.units.get('storage', []):
        uid=s['id']
        for k in ('eta_c','eta_d'):
            x=number(s[k],f'{uid}.{k}',positive=True)
            if x>1:raise ValueError(f'{uid}.{k} 效率不得超过1')
        for k in ('ecap','pmax'):number(s[k],f'{uid}.{k}',positive=True)
        lo,hi,v=(number(s[k],f'{uid}.{k}') for k in ('soc_min','soc_max','soc0'))
        if not 0<=lo<=v<=hi<=1:raise ValueError(f'{uid} SOC技术边界/初值非法')
    for p in cfg.units.get('pumped', []):
        uid=p['id'];eg=number(p['e_gen'],f'{uid}.e_gen',positive=True);ep=number(p['e_pump'],f'{uid}.e_pump',positive=True)
        if eg>ep:raise ValueError(f'{uid} 抽蓄综合效率e_gen/e_pump不得超过1')
    _validate_hydro_topology(cfg)
    for h in cfg.units.get('hydro', []):
        number(h['water_rate'],f"{h['id']}.water_rate",positive=True)
        if h.get('qout_max') is not None and number(h['qout_max'],f"{h['id']}.qout_max") < 0:
            raise ValueError(f"{h['id']}.qout_max须非负或None")
    if init is None:return
    if not isinstance(init,dict):raise ValueError('滚动实际状态必须为对象')
    th=init.get('thermal',{})
    if not isinstance(th,dict) or set(th)!={x['id'] for x in cfg.units.get('thermal',[])}:
        raise ValueError('滚动实际thermal状态必须完整，不能退回技术初值')
    for spec in cfg.units.get('thermal',[]):
        uid=spec['id'];v=th[uid]
        if not isinstance(v,dict) or not {'online','p0','hours'}<=set(v):raise ValueError(f'{uid}实际thermal状态须含online/p0/hours')
        if not isinstance(v['online'],(bool,int)) or v['online'] not in (0,1):raise ValueError(f'{uid}.online须为布尔或0/1')
        p=number(v['p0'],f'{uid}.p0');hours=number(v['hours'],f'{uid}.hours')
        if hours<0 or p < -1e-6 or p>spec['pmax']+1e-6 or (not v['online'] and abs(p)>1e-6):raise ValueError(f'{uid}实际在线状态/出力/持续时间不一致')
    for family in ('storage','pumped'):
        expected={x['id'] for x in cfg.units.get(family,[])}
        states=init.get(family,{})
        if not isinstance(states,dict) or set(states)!=expected:
            raise ValueError(f'滚动实际{family}状态必须完整包含所有启用资源，不能退回技术初值')
        for spec in cfg.units.get(family,[]):
            uid=spec['id'];state=states[uid]
            if family=='storage':
                e=number(state,f'{uid}实际E')
                if not spec['soc_min']*spec['ecap']-1e-6<=e<=spec['soc_max']*spec['ecap']+1e-6:
                    raise ValueError(f'{uid}实际E超出技术SOC边界')
            else:
                if not isinstance(state,dict) or not {'V','ng','np','xg','xp'}<=set(state):raise ValueError(f'{uid}实际抽蓄状态须完整V/ng/np/xg/xp')
                vals={k:number(state[k],f'{uid}.{k}') for k in ('V','ng','np','xg','xp')}
                lo=max(spec['vup_min'],spec['v_total']-spec['vdn_max']);hi=min(spec['vup_max'],spec['v_total']-spec['vdn_min'])
                if not lo-1e-6<=vals['V']<=hi+1e-6:raise ValueError(f'{uid}实际上库库容超出上下库技术边界')
                if any(vals[k]!=round(vals[k]) for k in ('ng','np','xg','xp')) or vals['xg'] not in (0,1) or vals['xp'] not in (0,1) or vals['xg']+vals['xp']>1:
                    raise ValueError(f'{uid}实际工况/台数非法')
                if not (vals['xg']<=vals['ng']<=spec['n_units']*vals['xg'] and vals['xp']<=vals['np']<=spec['n_units']*vals['xp']):raise ValueError(f'{uid}实际工况与台数不一致')
