"""Two explicit synthetic AGC -> reserve segments for the IEEE39 workflow."""
from dataclasses import asdict
from decimal import Decimal as D, ROUND_CEILING

if __package__:
    from .sichuan_v5_agc import BOUNDS, AgcOffer, clear_regulation
    from .sichuan_v5_reserve_market import RegulationAward, ReserveOffer, clear_reserve
else:
    from sichuan_v5_agc import BOUNDS, AgcOffer, clear_regulation
    from sichuan_v5_reserve_market import RegulationAward, ReserveOffer, clear_reserve

AGC_SEGMENTS=((0,48,D(40)),(48,96,D(60)))
ROUNDING_TOL=D('1e-8')
SYNTHETIC_ELIGIBILITY=('C1','C2','H1','H2')


def reserve_offers(case):
    d=lambda x:D(str(x))
    return tuple(ReserveOffer(r.id,'hydro',d(r.capacity_mw)*D('.15'),
                d(45 if r.id=='H1' else 90),d(r.capacity_mw),d(r.minimum_mw),d(r.capacity_mw),
                d(r.capacity_mw),True,installed_mw=d(r.capacity_mw),seasonal_or_better=True)
                for r in case.resources if r.id in ('H1','H2'))


def _offers(case, committed, start, stop):
    out=[]
    for r in case.resources:
        if r.id not in SYNTHETIC_ELIGIBILITY:
            continue
        rows=committed[start:stop]
        online=all(row['online'].get(r.id,1)==1 for row in rows)
        minimum=max(r.minimum_mw,0.)
        maximum=min(case.available[t].get(r.id,r.capacity_mw) for t in range(start,stop))
        dispatch=[row['power'][r.id] for row in rows]
        up=max(0.,min(maximum-max(dispatch),r.ramp_mw_15min or maximum,50.))
        down=max(0.,min(min(dispatch)-minimum,r.ramp_mw_15min or maximum,50.))
        dec=lambda value:D(str(value))
        kind='hydro' if r.kind=='hydro' else 'coal'
        # Offers use the technology floor rounded UP to the whole-MW
        # submission tick, distinct from the potentially fractional award.
        submitted=(dec(maximum)*BOUNDS[kind][0]).to_integral_value(rounding=ROUND_CEILING) if online else D(0)
        out.append(AgcOffer(r.id,kind,submitted,D('2.00'),D('.80'),
                f'ieee39_synthetic_history_{r.id}_Kh_0.80',online,dec(r.capacity_mw),
                dec(maximum if online else 0.),dec(up if online else 0.),
                dec(down if online else 0.),dec(max(0.,maximum-minimum) if online else 0.),
                largest_hydro_machine_mw=D(100) if r.kind=='hydro' else None))
    return tuple(out)


def stages(case, committed, reserve_gross, *, publish_regulation, publish_reserve):
    """Save stage evidence before aborting an unmet segment; return energy holds only on success."""
    reg_segments=[]
    for start,stop,demand in AGC_SEGMENTS:
        offers=_offers(case,committed,start,stop)
        agc=clear_regulation(demand,offers,stage_evidence_id=f'synthetic_agc_q{start:02d}_{stop:02d}')
        reg_segments.append({'start':start,'stop':stop,'offers':tuple(asdict(o) for o in offers),
                             'result':asdict(agc)})
    publish_regulation(reg_segments)
    if any(seg['result']['unmet_mw']>ROUNDING_TOL for seg in reg_segments):
        raise ValueError('合成调频研究有容量缺额，已保留凭证；未运行备用或实时')
    reserve_segments=[]
    holds={}
    for seg in reg_segments:
        start,stop=seg['start'],seg['stop']
        agc=seg['result']
        reg=tuple(RegulationAward(unit,a['hold_up_mw'],a['hold_down_mw'],a['reserve_credit_mw'],a['evidence_id'])
                  for unit,a in agc['awarded'].items())
        offers=reserve_offers(case)
        res=clear_reserve(D(str(reserve_gross)),agc['system_regulation_credit_mw'],offers,reg,
                             duration_hours=D(stop-start)/4,regulation_stage_evidence_id=agc['stage_evidence_id'])
        reserve_segments.append({'start':start,'stop':stop,'offers':tuple(asdict(o) for o in offers),
                                 'result':asdict(res)})
        for t in range(start,stop):
            holds[t]=res.energy_holds
    publish_reserve(reserve_segments)
    if any(seg['result']['unmet_mw']>ROUNDING_TOL for seg in reserve_segments):
        raise ValueError('合成备用研究有容量缺额，已保留凭证；未运行实时')
    return holds
