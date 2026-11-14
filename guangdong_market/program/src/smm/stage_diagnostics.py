"""Read-only stage snapshots. These never fix variables or change objectives."""
from __future__ import annotations
import pyomo.environ as pyo


def capture(m, stage, info=None):
    md=m._md;rows=[];offset=m._case.t0;enabled=getattr(m,'_agc',{}).get('enabled',False)
    val=lambda v:float(pyo.value(v))
    for t in m.T:
        def add(uid,kind,power,on=None,**kw):
            q=val(m.q_agc[uid,t]) if enabled and uid in m.AGC else None
            rows.append(dict(period=offset+int(t),unit=uid,type=kind,P_mw=power,on=on,q_reg_mw=q,E_mwh=None,V_up_1e4m3=None,**kw))
        for uid,d in md.thermal.items():add(uid,d['type'],val(m.P[uid,t]),int(round(val(m.u[uid,t]))))
        for uid,d in md.ren.items():add(uid,d['type'],val(m.Pw[uid,t]))
        for uid in md.hydro_units:add(uid,'hydro',val(m.Ph[uid,t]),int(sum(val(m.delta[j,z,t]) for j,z in m.JZ if j==uid)>0))
        for uid in md.storage:
            add(uid,'storage',val(m.Pd[uid,t]-m.Pc[uid,t]),int(round(val(m.u_dis[uid,t]+m.u_ch[uid,t]))))
            rows[-1].update(E_mwh=val(m.E[uid,t]),P_dis_mw=val(m.Pd[uid,t]),P_ch_mw=val(m.Pc[uid,t]))
        for uid in md.pumped:
            add(uid,'pumped',val(m.Pg[uid,t]-m.Pp[uid,t]),int(round(val(m.xg[uid,t]+m.xp[uid,t]))))
            rows[-1].update(V_up_1e4m3=val(m.Vu[uid,t]),P_gen_mw=val(m.Pg[uid,t]),P_pump_mw=val(m.Pp[uid,t]))
    summary=dict(info or {})
    # Do not turn the auxiliary objective (score) into actual cost/payment.
    summary['stage']=stage
    summary['status']='正式SCED（RT仅窗口首段执行）' if stage=='energy_sced' else '预计划/辅助筛选（不执行、不结算）'
    summary['objective_kind']='regulation_score_only_not_payment' if stage=='agc_auxiliary' else 'energy_objective_including_penalties'
    summary['prices_are_market_prices']=stage=='energy_sced'
    from .scuc import INT_VARS
    integer_values = [dict(variable=name, index=list(index) if isinstance(index, tuple) else index,
                           value=val(v), fixed=bool(v.fixed))
                      for name in INT_VARS for index, v in getattr(m, name).items()]
    return {'units':rows,'summary':summary,'integer_values':integer_values}
