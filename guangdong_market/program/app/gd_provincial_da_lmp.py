"""Independent DA pricing LP on the source-bound provincial dispatch matrix.

GD provincial 2026 §5.8.2.3: independent LMP, DA SCED reference bands,
primary reserve, network pricing slack, storage physics. This implementation
accepts 96 delivery quarters and two explicitly declared compressed representative
points. These never enter delivery settlement; formal_rule_certified remains False.
"""
from copy import deepcopy
import math,re

POWER=re.compile(r'power\[([^,]+),(\d+)\]')
BALANCE=re.compile(r"c_balance\['([^']+)',(\d+)\]")

def build(sced,names,response,*,delta,network_penalty_yuan_mwh,representative_periods=None):
    if response.get('status')!='optimal' or response.get('dual_valid') is not True:
        raise ValueError('accepted optimal source SCED required')
    if any(v['kind']!='C' for v in sced['variables']):raise ValueError('fixed-integer SCED LP required')
    if not math.isfinite(delta) or not 0<=delta<=1:raise ValueError('explicit finite research delta required')
    if not math.isfinite(network_penalty_yuan_mwh) or network_penalty_yuan_mwh<=0:raise ValueError('explicit positive network pricing penalty required')
    q=deepcopy(sced);n=deepcopy(names);x=response['primal'];idx={name:i for i,name in enumerate(n['variables'])}
    if len(x)!=len(q['variables']) or len(n['constraints'])!=len(q['rows']):raise ValueError('source dimensions differ')
    ticks={int(m[2]) for name in idx if (m:=POWER.fullmatch(name))}
    if ticks not in (set(range(96)),set(range(98))):raise ValueError('96 delivery cells plus optional two declared representative periods required')
    representative_periods=deepcopy(representative_periods or [])
    if len(ticks)==98:
        if {z.get('tick')for z in representative_periods}!={96,97}:raise ValueError('explicit two representative-period identities required')
        for z in representative_periods:
            if z.get('role')!='representative' or type(z.get('source_day_quarter'))is not int or not 0<=z['source_day_quarter']<96:raise ValueError('representative input origin required')
            if z.get('settle')is not False or not z.get('state_link_basis') or not z.get('weight_basis'):raise ValueError('nonsettling role and explicit state/weight assumptions required')
            if not math.isfinite(z.get('objective_duration_h',0)) or z['objective_duration_h']<=0:raise ValueError('positive representative objective weight required')
    elif representative_periods:raise ValueError('representative metadata without representative matrix rows')
    duration={t:.25 for t in ticks}
    duration.update({z['tick']:z['objective_duration_h']for z in representative_periods})
    thermal={m[1] for name in idx if (m:=POWER.fullmatch(name))}
    audit=[]
    # Restrict only dispatch MW columns. All state/integer values and all physical
    # equations remain source-bound; negative net injection is represented by its
    # positive charge/pump component, so bounds never reverse for charging.
    for name,i in idx.items():
        hit=POWER.fullmatch(name)
        applicable=name.startswith(('battery.charge[','battery.discharge[','pumped.gen[','pumped.pump[')) or hit
        if not applicable:continue
        v=q['variables'][i];ref=float(x[i])
        if ref < -1e-6:raise ValueError('unexpected signed component')
        ref=max(0.,ref);lo,hi=(1-delta)*ref,(1+delta)*ref;reason='eligible_research_resource'
        if hit:
            g,t=hit[1],hit[2]
            if x[idx[f'stable[{g},{t}]']] < .5:
                lo=hi=ref;reason='off_or_transition_nonpricing'
        low=max(lo,v['lb']) if v['lb'] is not None else lo
        high=min(hi,v['ub']) if v['ub'] is not None else hi
        if low>high+1e-6:raise ValueError('SCED reference excluded by physical bounds')
        # Relax only insignificant native rounding at a pre-existing fixed bound.
        if low>high:low=high=(low+high)/2
        v.update(lb=low,ub=high)
        audit.append(dict(variable=name,reference_mw=ref,lower_mw=low,upper_mw=high,classification=reason))
    # Provincial DA LMP contains primary reserve, not the dispatch's positive,
    # negative, and emergency-reserve procurement rows. Retain exactly primary
    # headroom, primary fraction cap and primary aggregate from known row layout.
    block=4*len(thermal)+4;keep=[];removed=[]
    reserve_names=[name for name in n['constraints'] if name.startswith('reserves[')]
    if len(reserve_names)!=len(ticks)*block:raise ValueError('unreviewed reserve row layout')
    for i,name in enumerate(n['constraints']):
        if name.startswith('reserves['):
            local=(int(name[9:-1])-1)%block
            is_primary=(2<=local<block-2 and (local-2)%4 in (0,2)) or local==block-2
            if not is_primary:removed.append(name);continue
        keep.append(i)
    q['rows']=[q['rows'][i] for i in keep];n['constraints']=[n['constraints'][i] for i in keep]
    c={}
    for i,a in q['objective']['terms']:c[i]=c.get(i,0.)+a
    for name,i in idx.items():
        if name.startswith(('line_over[','line_under[','section_over[','section_under[')):
            q['variables'][i].update(lb=0.,ub=None);c[i]=duration[int(name.rsplit(',',1)[1][:-1])]*network_penalty_yuan_mwh
        if name.startswith(('contingency[','extra_reserve.battery_up[','extra_reserve.pumped_up[')):q['variables'][i].update(lb=0.,ub=0.)
    q['objective']['terms']=[[i,c[i]] for i in sorted(c) if c[i]]
    q['dual']=True;q['warmstart']=False
    return q,n,dict(schema='gd.provincial.da_lmp_build.v1',grid_minutes=15,periods=len(ticks),settlement_periods=96,representative_periods=representative_periods,
       method='independent_DA_LMP_with_SCED_reference_band',reference='same_day_same_quarter_DA_SCED',
       delta=delta,delta_basis='research_assumption_not_verified_official_value',
       network_penalty_yuan_mwh=network_penalty_yuan_mwh,network_penalty_basis='explicit_research_parameter',
       bounds=audit,removed_nonpricing_reserve_rows=removed,
       scope_exclusions=(['D+1 peak/valley representative points not implemented'] if len(ticks)==96 else ['D+1 representative state links/weights are explicit research assumptions'])+['resource pricing eligibility is a declared research assumption','no reactive power or AC feasibility per user scope'],
       physical_constraints_preserved=True,formal_rule_certified=False)

def extract(q,n,r,*,representative_periods=None):
    if r.get('status')!='optimal' or not r.get('dual_valid'):raise ValueError('independent optimal pricing LP required')
    representative_periods=representative_periods or []
    ticks={int(m[2]) for name in n['variables'] if (m:=POWER.fullmatch(name))}
    if ticks not in (set(range(96)),set(range(98))):raise ValueError('known DA grid required')
    if ticks==set(range(98)) and {z.get('tick')for z in representative_periods}!={96,97}:raise ValueError('representative timing metadata required')
    for z in representative_periods:
        if z.get('settle')is not False or z.get('role')!='representative' or not math.isfinite(z.get('objective_duration_h',0)) or z['objective_duration_h']<=0:raise ValueError('nonsettling representative and positive objective weight required')
    duration={t:.25 for t in ticks};duration.update({z['tick']:z['objective_duration_h'] for z in representative_periods})
    prices={t:{} for t in sorted(ticks)};orient={}
    for i,name in enumerate(n['constraints']):
        hit=BALANCE.fullmatch(name)
        if not hit:continue
        bus,t=hit[1],int(hit[2]);row=q['rows'][i]
        a=[c for j,c in row['terms'] if n['variables'][j].startswith(('power[','renewable_power['))]
        if not a or abs(abs(a[0])-1)>1e-10 or any(abs(v-a[0])>1e-10 for v in a):raise ValueError('unknown balance orientation')
        prices[t][bus]=a[0]*r['dual'][i]/duration[t];orient[name]=a[0]
    buses=set(prices[0])
    if not buses or any(set(z)!=buses for z in prices.values()):raise ValueError('incomplete nodal grid')
    return dict(schema='gd.provincial.da_lmp_prices.v1',grid_minutes=15,raw_prices_by_tick={t:prices[t] for t in range(96)},representative_prices_by_tick={t:prices[t]for t in sorted(ticks)if t>=96},representative_periods=representative_periods,
                hourly_prices={h:{b:math.fsum(prices[t][b] for t in range(4*h,4*h+4))/4 for b in sorted(buses)}for h in range(24)},
                units='yuan/MWh',source='independent_DA_LMP_not_dispatch_SCED_duals',
                dual_conversion='verified injection coefficient times RHS dual divided by declared objective duration; representative prices never enter delivery settlement',
                balance_orientation=orient,formal_rule_certified=False)
