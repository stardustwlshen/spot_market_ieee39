"""Remaining-day asset feasibility witness, not an extended market-clearing horizon.

The formal rolling window still clears only its configured forecast horizon.
This zero-cost suffix connects its final stocks AND pumped integer state to every
remaining hard DA clock target, including 24:00. It certifies asset feasibility
conditional on future power being deliverable; it is NOT a future network,
thermal, hydro, reserve or forecast-uncertainty certificate. No suffix quantity
is dispatched, settled or fed into the next state.
"""
from __future__ import annotations

import math
import pyomo.environ as pyo

INTEGER_NAMES = ('u_ch','u_dis','xg','xp','ngen','npump')
SCOPE = 'battery_and_pumped_assets_only;remaining_day_hard_targets;not_future_network_or_reserve'


def attach(m):
    term=m._case.terminal or {}
    spec=term.get('asset_reachability')
    if spec is None:
        return
    md=m._md;case=m._case;T=md.T;dt=md.dt
    if md.storage and not case.cfg.base['solver'].get('storage_binary',True):
        raise ValueError('资产整数可达性保护要求storage_binary=true；LP松弛对照请使用历史策略')
    if case.stage!='RT' or not term.get('storage_anchor_hard'):
        raise ValueError('资产可达性后缀仅用于实时两小时硬锚点')
    if spec.get('start_period')!=case.t0+T or spec.get('end_period_exclusive')!=case.cfg.T:
        raise ValueError('资产可达性后缀须从当前窗口末连续延伸到运行日末')
    end=case.cfg.T-case.t0
    if end<=T:
        raise ValueError('资产可达性后缀必须非空；日末窗口无需额外后缀')
    b=m.asset_reachability=pyo.Block(concrete=True)
    b._integer_names=INTEGER_NAMES
    b.F=pyo.Set(initialize=range(T,end),ordered=True)
    b.E=pyo.Var(m.S,b.F,domain=pyo.NonNegativeReals)
    b.Pd=pyo.Var(m.S,b.F,domain=pyo.NonNegativeReals)
    b.Pc=pyo.Var(m.S,b.F,domain=pyo.NonNegativeReals)
    b.u_ch=pyo.Var(m.S,b.F,domain=pyo.Binary)
    b.u_dis=pyo.Var(m.S,b.F,domain=pyo.Binary)
    b.Vu=pyo.Var(m.PS,b.F,domain=pyo.NonNegativeReals)
    b.Pg=pyo.Var(m.PS,b.F,domain=pyo.NonNegativeReals)
    b.Pp=pyo.Var(m.PS,b.F,domain=pyo.NonNegativeReals)
    b.xg=pyo.Var(m.PS,b.F,domain=pyo.Binary)
    b.xp=pyo.Var(m.PS,b.F,domain=pyo.Binary)
    b.ngen=pyo.Var(m.PS,b.F,domain=pyo.NonNegativeIntegers)
    b.npump=pyo.Var(m.PS,b.F,domain=pyo.NonNegativeIntegers)
    b.sg=pyo.Var(m.PS,b.F,domain=pyo.NonNegativeReals)
    b.sp=pyo.Var(m.PS,b.F,domain=pyo.NonNegativeReals)
    b.constraints=pyo.ConstraintList()
    b.anchors=pyo.ConstraintList()
    c=b.constraints.add
    def previous(name,unit,t):
        if t>=T:return getattr(b,name)[unit,t]
        if t>=0:return getattr(m,name)[unit,t]
        if name in ('xg','xp'):
            absolute=case.t0+t
            if t==-1:return md.pumped[unit]['init'][name]
            if absolute<0:return int(md.pumped[unit].get('init_mode','idle')==('gen' if name=='xg' else 'pump'))
            hist=(case.init or {}).get('pumped_hist',{}).get(unit,{}).get(name,[])
            if absolute>=len(hist) or not math.isfinite(float(hist[absolute])) or float(hist[absolute]) not in (0.,1.):
                raise ValueError(f'资产可达性后缀缺少抽蓄已执行工况历史 {unit}/{name}[{absolute}]')
            return int(hist[absolute])
        raise ValueError(f'无效后缀前态 {name}[{t}]')
    for s,d in md.storage.items():
        for t in b.F:
            c(b.E[s,t]==previous('E',s,t-1)+(d['eta_c']*b.Pc[s,t]-b.Pd[s,t]/d['eta_d'])*dt)
            c(pyo.inequality(d['soc_min']*d['ecap'],b.E[s,t],d['soc_max']*d['ecap']))
            c(b.u_ch[s,t]+b.u_dis[s,t]<=1)
            c(b.Pd[s,t]<=min(d['pmax'],sum(d['Ld']))*b.u_dis[s,t])
            c(b.Pc[s,t]<=min(d['pmax'],sum(d['Lc']))*b.u_ch[s,t])
            if d['mode']=='self_schedule':c(b.Pd[s,t]-b.Pc[s,t]==float(d['sched'][case.t0+t]))
    for p,d in md.pumped.items():
        for t in b.F:
            c(b.Vu[p,t]==previous('Vu',p,t-1)+(b.Pp[p,t]/d['e_pump']-b.Pg[p,t]/d['e_gen'])*dt)
            c(pyo.inequality(d['v_lo'],b.Vu[p,t],d['v_hi']))
            c(b.xg[p,t]+b.xp[p,t]<=1)
            for n,x in (('ngen','xg'),('npump','xp')):
                c(getattr(b,n)[p,t]<=d['N']*getattr(b,x)[p,t]);c(getattr(b,n)[p,t]>=getattr(b,x)[p,t])
            c(b.Pg[p,t]>=d['unit_gen_min']*b.ngen[p,t]);c(b.Pg[p,t]<=d['unit_gen_max']*b.ngen[p,t])
            c(b.Pg[p,t]<=sum(d['Lg']))
            pump_min=d['unit_pump_min'] if d.get('variable_speed') else d['unit_pump']
            c(b.Pp[p,t]>=pump_min*b.npump[p,t]);c(b.Pp[p,t]<=d['unit_pump']*b.npump[p,t])
            c(b.Pp[p,t]<=sum(d['Lp']))
            c(b.sg[p,t]>=b.ngen[p,t]-previous('ngen',p,t-1))
            c(b.sp[p,t]>=b.npump[p,t]-previous('npump',p,t-1))
            for lag in range(1,int(d.get('switch_gap',1))+1):
                c(previous('xg',p,t-lag)+b.xp[p,t]<=1)
                c(previous('xp',p,t-lag)+b.xg[p,t]<=1)
        # ONE combined budget: current-window starts + suffix starts <= actual remainder.
        # Current constraints already include the transition from actual initial counts.
        for var,kind in (('sg','gen'),('sp','pump')):
            c(sum(getattr(m,var)[p,t] for t in m.T)+sum(getattr(b,var)[p,t] for t in b.F)
              <=m._pumped_remaining_starts[p][kind])
    b._targets=[]
    for kind,units,key in (('E',md.storage,'storage_targets'),('Vu',md.pumped,'pumped_targets')):
        targets=spec.get(key,{})
        if set(targets)!=set(units):raise ValueError(f'资产可达性后缀缺少完整{kind}目标')
        stride=int(round(2./dt))
        expected={t-T for t in b.F if (case.t0+t+1)%stride==0}
        for unit,rows in targets.items():
            actual=[int(row[0]) for row in rows]
            if set(actual)!=expected or len(actual)!=len(expected):
                raise ValueError('资产可达性后缀必须完整覆盖每个剩余两小时硬锚点')
            for relative,lo,hi in rows:
                t=T+int(relative)
                if t not in b.F or lo!=hi or not math.isfinite(float(lo)):
                    raise ValueError('资产可达性后缀目标时标/硬等式非法')
                b.anchors.add(getattr(b,kind)[unit,t]==float(lo))
                b._targets.append((kind,unit,t,float(lo)))
    b._scope=SCOPE


def diagnostics(m):
    b=getattr(m,'asset_reachability',None)
    if b is None:return dict(enabled=False,scope=SCOPE,reason='no_remaining_suffix_or_legacy_policy')
    val=lambda x:float(pyo.value(x))
    rows=[]
    for kind,unit,t,target in b._targets:
        actual=val(getattr(b,kind)[unit,t])
        rows.append(dict(kind=kind,unit=unit,period=m._case.t0+t,state_end_hour=(m._case.t0+t+1)*m._md.dt,
                         target=target,witness=actual,error=actual-target))
    budgets={p:{kind:dict(remaining=m._pumped_remaining_starts[p][kind],
                    window=val(sum(getattr(m,var)[p,t] for t in m.T)),
                    suffix=val(sum(getattr(b,var)[p,t] for t in b.F)))
                for var,kind in (('sg','gen'),('sp','pump'))} for p in m.PS}
    return dict(enabled=bool(b.active),scope=SCOPE,quantities_execute=False,zero_objective_weight=True,
                suffix_start=m._case.t0+m._md.T,suffix_end_exclusive=m._case.cfg.T,
                future_anchor_targets=rows,pumped_start_budget=budgets,
                max_target_error=max((abs(r['error']) for r in rows),default=0.))
