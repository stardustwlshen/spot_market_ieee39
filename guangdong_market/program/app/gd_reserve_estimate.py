"""Guangdong two-rules reserve compensation research estimate, not auction/bill.
2025 articles 22/33/35 + appendix R6. Applicability in 2026 is not certified.
"""
from decimal import Decimal as D
from gd_unit_economics import execution_grid_minutes
RULE='https://nfj.nea.gov.cn/xwzx/tzgg/202502/P020250214585645069049.pdf'
def number(x):
    if isinstance(x,bool):raise ValueError('boolean reserve quantity')
    v=D(str(x))
    if not v.is_finite() or v<0:raise ValueError('invalid reserve quantity')
    return v

def calculate(hour,execution,case,parameters=None):
    p=dict(mode='gd_two_rules_research_estimate',compensation_pool_limit_mw=400,rate_CNY_per_MWh_service=30,unit_overrides={})
    p.update(parameters or {})
    if p['mode']=='auction':raise ValueError('auction settlement requires source-bound awards and auction prices; administrative estimate disabled')
    if p['mode']!='gd_two_rules_research_estimate':raise ValueError('unknown reserve settlement mode')
    cap=number(p['compensation_pool_limit_mw']);rate=number(p['rate_CNY_per_MWh_service']);dt=D(execution_grid_minutes(hour,execution))/60
    cells=execution['cells']; values=execution['values'];thermal=case['energy_inputs']['thermal']
    # Explicit old source-grid conversion; do not index a 5-min series by 15-min tick.
    horizon=case['energy_inputs']['horizon']; periods=horizon['periods'] if isinstance(horizon,dict) else horizon.periods
    source_dt=float(periods[0]['duration_h'] if isinstance(periods[0],dict) else periods[0].duration_h)
    ratio=round(float(dt)/source_dt)
    if ratio<1 or abs(ratio*source_dt-float(dt))>1e-9:raise ValueError('source grid does not align with executed grid')
    def upper_at(u,t):
        # Available upper limits use conservative minimum when source is finer.
        return min(u['max_mw'][t*ratio+j] for j in range(ratio))
    kinds={r['resource']:r['technology'] for r in hour['rows']};totals={r:D(0) for r in kinds};supplies={r:D(0) for r in kinds};details=[]
    if set(p['unit_overrides'])-set(kinds):raise ValueError('unknown reserve override resource')
    for t in cells:
        q={r:D(0) for r in kinds}
        for r,kind in kinds.items():
            if kind not in ('coal','gas','ccgt','gt'):continue
            if r not in thermal:raise ValueError('eligible reserve resource lacks physical inputs')
            u=thermal[r]; override=p['unit_overrides'].get(r,{})
            rated=number(override.get('rated_mw',max(u['max_mw'].values())))
            upper=number(override.get('declared_upper_mw',upper_at(u,t)))
            online=number(values[f'online[{r},{t}]']);power=number(values[f'power[{r},{t}]'])
            if abs(online-round(online))>D('0.000001'):raise ValueError('noninteger online state')
            if upper>rated or power>rated+D('.00001'):raise ValueError('reserve nameplate domain')
            for name in ('designated','qualified','own_cause_daily_failure'):
                if name in override and type(override[name]) is not bool:raise ValueError('reserve flags must be boolean')
            eligible=round(online)==1 and override.get('designated',True) and override.get('qualified',True) and not override.get('own_cause_daily_failure',False)
            if eligible:q[r]=max(D(0),upper-max(power,rated*D('.5') if kind=='coal' else D(0)))
        total=sum(q.values());pool=min(total,cap)*dt*rate
        for r in kinds:
            supplies[r]+=q[r]*dt
            totals[r]+=pool*q[r]/total if total else D(0)
        details.append(dict(tick=t,total_supply_MW=str(total),pool_CNY=str(pool)))
    return dict(mode=p['mode'],grade='research_compensation_estimate_not_verified_bill',rule_source=RULE,rule_articles=[22,33,35],
        parameters=p,assumptions=['2025 administrative formula used for research; 2026 applicability not certified','pool limit 400 MW is an independent editable research assumption, not asserted to equal dispatch reserve requirement','online thermal units designated/qualified, no own-cause failures; editable per unit','rated MW defaults to maximum source available capacity; override if different','pumped storage excluded by article 22; battery/VRE/import no compensation modeled'],
        source_interval_minutes=int(dt*60),periods=details,rows=[dict(resource=r,amount_CNY=str(totals[r]),service_MWh=str(supplies[r]),zero_basis='excluded_resource_type' if kinds[r] not in ('coal','gas','ccgt','gt') else None)for r in kinds],total_CNY=str(sum(totals.values())),formal_bill=None,native_calls=0)
