"""Pure R23 original numerical cases plus explicit new research assumptions."""
from copy import deepcopy
from datetime import datetime,timedelta
from hashlib import sha256
from pathlib import Path
import json
from .gd_october_job_codec import loads
from .gd_horizon import EnergyHorizon,EnergyPeriod
from .gd_r23_scuc_contract import SCHEMA,validate_contract
DEFAULT_CORE=Path('/workspace/scratch/42c5fe32dad0/guangdong_r14_core95_20261112')
def _raw(x):return json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def _horizon(n):return EnergyHorizon(tuple(EnergyPeriod(t,1/12,1/12,'delivery',True)for t in range(n)),'day_ahead_research_5min'if n>=288 else 'component','R23 continuous five-minute physical chain; DA98 projection only')
def _contract(n=576):
    c=dict(schema=SCHEMA,mode='research',day_start='2026-11-11T00:00:00+08:00',cutoff='2026-11-10T12:30:00+08:00',publication_at='2026-11-10T20:30:00+08:00',economic_policy='continuous48h_research',representatives=[dict(start='2026-11-12T06:15:00+08:00',physical_index=363),dict(start='2026-11-12T17:30:00+08:00',physical_index=498)],declarations=[dict(subject='R23_research_case',version='r23_case_v1',source='Explicit R23 research declaration, not operator input',available_at='2026-11-10T12:00:00+08:00',source_sha256=sha256(b'R23 research case v1').hexdigest(),basis='research_assumption')],evidence={},research_assumptions={
    'declaration_versions':'New synthetic R23 12:00 vintage from original prior; not backdated old19:30 archive',
    'thermal_curves':'Coal linear180/60min new research curves; gas original60/30,30/15; no confirmed typical curves',
    'qualification':'Original thermal engineering research eligibility; no formal AGC qualification',
    'terminal_targets':'Original D96 50percent SOC research target; self-declaration absent; no official default asserted',
    'representative_mapping':'D+1 06:15/17:30 actual indices363/498 three-cell means',
    'economic_mapping':'Every48h physical cell costs dt; every event fee once; DA98 projection only'})
    if n!=576:c.update(diagnostic_count=n,diagnostic_reason='bounded synthetic free-event component case',representatives=[])
    return c

def _curve(pmin,startup_minutes,shutdown_minutes,no_load,first_bid,source):
    def steps(minutes,startup):
        n=minutes//5;rows=[]
        for j in range(n):
            a,b=pmin*j/n,pmin*(j+1)/n
            if not startup:a,b=pmin-a,pmin-b
            mean=(a+b)/2;rows.append(dict(begin=a,mean=mean,end=b,cost=no_load+first_bid*mean))
        return rows
    return dict(startup=steps(startup_minutes,True),shutdown=steps(shutdown_minutes,False),notice_h=.5,available_at='2026-11-10T12:00:00+08:00',source=source,basis='research')

def ieee39_case(core=DEFAULT_CORE,*,boundary_kind='original'):
    if boundary_kind not in ('original','accepted_prepared_research'):raise ValueError('explicit original or independently prepared research boundary required')
    root=Path(core);op=root/'input/historical_da_only/source_reconstructed_day_inputs.json';dp=root/'ieee39_native_v4b/reports/sced/input.json'
    expected=('cec7e26b76756885c640218dea024451351415f6012210463782aef3436a1a3b','0522c162bc27a5dc05b29902c6265288e7c7bee4ab746bcaf31336bd7e029e58')
    if tuple(sha256(p.read_bytes()).hexdigest()for p in (op,dp))!=expected:raise ValueError('original accepted IEEE39 source bytes changed')
    old=loads(op.read_bytes());archive=json.loads(dp.read_bytes());x=archive['physical_lift'];n=576;assert len(x['cells'])==n
    c=_contract();e=deepcopy(old['energy_inputs']);e['horizon']=_horizon(n);ts=tuple(range(n));series=lambda v:{t:v for t in ts}
    units={u['id']:u for u in old['static']['units']['thermal']};curves={};no_load={}
    for g,d in e['thermal'].items():
        u=units[g]
        for key,value in list(d.items()):
            if type(value)is dict and set(value)==set(range(288)):
                assert len({repr(z)for z in value.values()})==1,(g,key,'unexpected varying thermal series')
                d[key]=series(value[0])
        for seg in d['segments']:
            assert len(set(seg['price_yuan_per_mwh'].values()))==1;seg['price_yuan_per_mwh']=series(seg['price_yuan_per_mwh'][0])
        d.update(initial_online=int(u['init']['online']),initial_power_mw=float(u['init']['p0']),initial_state_age_h=float(u['init']['hours']),initial_state_kind='stable',max_starts=None,max_stops=None,status=series('free'),fixed_power_mw=series(None))
        minutes=(180,60)if u['type']=='coal'else (60,30)if g=='CCGT'else (30,15);no_load[g]=float(u['noload'])
        curves[g]=_curve(float(u['pmin']),*minutes,no_load[g],float(old['static']['bids']['units'][g]['segments'][0]['price']),'R23 linear coal research curve'if u['type']=='coal'else 'original gas linear research curve; not formal')
    from .gd_r13_source_recipe import source_cells
    static_raw={k[7:]:v['artifact']for k,v in old['contract']['sources'].items()if k.startswith('static:')}
    forecast=source_cells(static_raw=static_raw,count=n);B=e['buses'];zero={(b,t):0. for b in B for t in ts}
    e['load_mw']={(b,t):x['load_mw'][b][t]for b in B for t in ts}
    e['renewable_mw']={(b,t):forecast['cells'][t]['values']['wind_mw'][b]+forecast['cells'][t]['values']['solar_mw'][b]for b in B for t in ts}
    e['physical_tie_mw']={(b,t):forecast['cells'][t]['values']['boundary_tie_mw'][b]for b in B for t in ts}
    for b in B:
        for t in ts:
            assert abs(e['load_mw'][b,t]-forecast['cells'][t]['values']['load_mw'][b])<1e-8
            assert abs(e['renewable_mw'][b,t]+e['physical_tie_mw'][b,t]-x['fixed_injection_mw'][b][t])<1e-8
    for line,d in e['lines'].items():
        d['lower_mw']={t:x['network'][line]['lower_mw'][t]for t in ts};d['upper_mw']={t:x['network'][line]['upper_mw'][t]for t in ts}
    assert not e['sections'],'unexpected source section inventory'
    e.update(renewable_bid_yuan_per_mwh=deepcopy(zero),renewable_curtailment_penalty_yuan_per_mwh=deepcopy(zero),balance_policy='hard')
    e['source_notes']['boundary_basis']='Original static init, not prepared or R21 executed state'
    e['source_notes']['exogenous']='Original synthetic prior, exact retained576 load/fixed injection; new research12:00 vintage, no old19:30 backdating'
    reserve=deepcopy(old['reserve'])
    for key in ('positive_mw','negative_mw','primary_mw','contingency_mw'):reserve[key]=series(reserve[key][0])
    assert set(reserve['positive_mw'].values())=={400.}and set(reserve['contingency_mw'].values())=={400.}
    batteries=deepcopy(old['batteries']);bc=deepcopy(old['contract']);bc.update(experiment_id='R23-original-static-free-SCUC',boundary_kind='original_D00_research',boundary_at=c['day_start'],day_start=c['day_start'],cutoff=c['cutoff'],planned_publication_at=c['publication_at'])
    dates=[(datetime.fromisoformat(c['day_start'])+timedelta(days=i)).date().isoformat()for i in range(2)]
    for uid,b in batteries.items():
        b.update(availability=series(True),notice_available_at=c['publication_at'],initial_power_mw=0.,initial_mode='idle',initial_hour_direction=0)
        b['cycle_budget']={date:dict(limit_cycles=1.,used_internal_mwh=0.)for date in dates}
        b['terminal_at']=(datetime.fromisoformat(c['day_start'])+timedelta(days=1)).isoformat()
    sid=bc['storage']['battery_parameters_source_id'];artifact=_raw(dict(batteries=batteries,pumped_storage=bc['storage']['pumped_storage']))
    bc['sources']={k:deepcopy(v)for k,v in old['contract']['sources'].items()if k.startswith('static:')}
    bc['sources'][sid]=dict(artifact=artifact,sha256=sha256(artifact).hexdigest(),available_at='2026-11-10T12:00:00+08:00',kind='research_storage_assumption')
    c['source_bindings']={str(p.resolve()):sha256(p.read_bytes()).hexdigest()for p in (op,dp)};c['static_source_bindings']={k:sha256(v).hexdigest()for k,v in static_raw.items()}
    c['case_assumptions']=dict(original_static_boundary=True,prepared_boundary_used=False,storage_self_declaration=None,official_default_condition_satisfied=None,coal_curve_minutes=dict(startup=180,shutdown=60),startup_fee='original uniform resolved fee, not thermal-age classification',no_load='original static independent noload; curve cost includes it once',scenario_numbers_unchanged=True,unqualified_incremental_BESS_wind_solar_reserve=0,AGC_awards=0)
    if boundary_kind=='accepted_prepared_research':
        prepared=old['prepared_boundary'];assert prepared['output_at']==c['day_start']and prepared['equilibrium_verified']and prepared['finite_preparation_reachability']
        assert prepared['original_predecessor_reachable']is False and prepared['historical_telemetry']is False
        for g,d in e['thermal'].items():
            z=prepared['thermal'][g]
            for key in ('initial_online','initial_power_mw','initial_state_age_h','initial_state_kind'):d[key]=z[key]
        e['source_notes']['boundary_basis']='Separate numerically verified prepared_D00 research hypothesis, not original initial state, telemetry or live capability'
        c['case_assumptions'].update(original_static_boundary=False,prepared_boundary_used=True,prepared_certificate_sha256=old['contract']['sources']['prepared_boundary_certificate']['sha256'],prepared_evidence_kind=prepared['evidence_kind'],prior_native_capability_restored=False,original_predecessor_reachable=False,actual_telemetry=False)
        bc.update(experiment_id='R23-separate-prepared-research-free-SCUC',boundary_kind='prepared_D00_research')
    c['case_assumptions']['boundary_kind']=boundary_kind;validate_contract(c,n)
    from .gd_da_battery_candidate import validate_batteries
    validate_batteries(batteries,bc,e['horizon'],B)
    return dict(energy_inputs=e,reserve=reserve,curves=curves,batteries=batteries,battery_contract=bc,contract=c,no_load_cost=no_load)

def small_case(count=48):
    """Two-unit middle peak forces initially-off backup to start freely."""
    if count not in (24,48):raise ValueError('small diagnostic supports24/48cells')
    ts=tuple(range(count));s=lambda v:{t:v for t in ts};c=_contract(count);thermal={}
    for name,online,power,cost in [('BASE',1,40.,10.),('PEAK',0,0.,30.)]:
        thermal[name]=dict(bus='A',bid_base_mw=20.,base_bid_yuan_per_h=s(20*cost),segments=({'key':'above_min','width_mw':40.,'price_yuan_per_mwh':s(cost)},),min_mw=s(20.),max_mw=s(60.),ramp_up_mw_per_h=s(600.),ramp_down_mw_per_h=s(600.),startup_output_limit_mw=s(20.),shutdown_output_limit_mw=s(20.),startup_yuan=s(100.),shutdown_yuan=s(0.),min_up_h=.5,min_down_h=.25,initial_online=online,initial_power_mw=power,initial_state_age_h=12.,initial_state_kind='stable',status=s('free'),fixed_power_mw= s(None),max_starts=None,max_stops=None,transition_model='instant_with_output_limits',terminal_policy='carry_out',source='R23 synthetic diagnostic')
    zero={('A',t):0. for t in ts};load={('A',t):100. if count//3<=t<2*count//3 else 40. for t in ts}
    e=dict(horizon=_horizon(count),buses=('A',),thermal=thermal,load_mw=load,renewable_mw=deepcopy(zero),physical_tie_mw=deepcopy(zero),lines={},sections={},reference_buses=('A',),penalties_yuan_per_mwh=dict(line=1000.,section=1000.,balance_shortfall=10000.,balance_surplus=10000.),source_notes={k:'R23 synthetic diagnostic'for k in ('thermal','transition','network','penalties','exogenous','boundary_basis','balance_policy','shortfall_cap_policy','renewable_policy')},balance_policy='hard',shortfall_cap_policy='unbounded_slack',renewable_mode='fixed_exogenous',renewable_bid_yuan_per_mwh=deepcopy(zero),renewable_curtailment_penalty_yuan_per_mwh=deepcopy(zero))
    reserve=dict(positive_mw=s(0.),negative_mw=s(0.),primary_mw=s(0.),contingency_mw=s(0.),primary_fraction={g:0. for g in thermal},contingency_eligible={g:False for g in thermal})
    curves={g:_curve(20.,10,10,0.,10. if g=='BASE'else 30.,'R23 synthetic10min curve')for g in thermal};validate_contract(c,count)
    return dict(energy_inputs=e,reserve=reserve,curves=curves,batteries={},battery_contract=None,contract=c,no_load_cost={g:0. for g in thermal})

def necessary_prefix(case):
    """Optimistic FIRST-cell necessary bounds; not a feasible primal witness.
    Ignore network, battery direction/ramp/preparation and shared allocation,
    hence negative margin rejects but nonnegative margins prove no feasibility.
    """
    import math
    e=case['energy_inputs'];r=case['reserve'];c=case['contract'];dt=1/12
    at=datetime.fromisoformat(c['day_start']);publication=datetime.fromisoformat(c['publication_at'])
    thermal={};energy_upper=capacity_upper=contingency_upper=0.
    for g,d in e['thermal'].items():
        if d['initial_online']:
            p=min(d['max_mw'][0],d['initial_power_mw']+dt*d['ramp_up_mw_per_h'][0]);cap=d['max_mw'][0]
            reserve=min(d['max_mw'][0]-d['min_mw'][0],d['ramp_up_mw_per_h'][0]/6)if r['contingency_eligible'][g]else 0.
        else:
            ready=d['initial_state_age_h']+1e-9>=d['min_down_h']and at>=publication+timedelta(hours=case['curves'][g]['notice_h'])
            p=case['curves'][g]['startup'][0]['mean']if ready else 0.;cap=p;reserve=0.
        thermal[g]=dict(first_cell_power_upper_mw=p,positive_capacity_upper_mw=cap,contingency_upper_mw=reserve)
        energy_upper+=p;capacity_upper+=cap;contingency_upper+=reserve
    batteries={}
    for uid,b in (case.get('batteries')or{}).items():
        p=min(b['discharge_max_mw'],max(0.,b['initial_energy_mwh']-b['energy_min_mwh'])*math.sqrt(b['roundtrip_efficiency'])/dt)if b['availability'][0]else 0.
        batteries[uid]=p;energy_upper+=p;capacity_upper+=p
    load=sum(e['load_mw'][b,0]for b in e['buses']);fixed=sum(e['renewable_mw'][b,0]+e['physical_tie_mw'][b,0]for b in e['buses'])
    margins=dict(energy_mw=energy_upper+fixed-load,positive_mw=capacity_upper+fixed-load-r['positive_mw'][0],contingency_mw=contingency_upper-r['contingency_mw'][0])
    return dict(schema='gd.research.r23.necessary_first_cell.v1',boundary_kind=c.get('case_assumptions',{}).get('boundary_kind','synthetic_diagnostic'),at=c['day_start'],duration_h=dt,load_mw=load,fixed_injection_mw=fixed,thermal=thermal,battery_discharge_optimistic_upper_mw=batteries,margins=margins,necessarily_infeasible=any(x < -1e-8 for x in margins.values()),negative_conditions=[k for k,v in margins.items()if v < -1e-8],feasible=False,feasibility_proven=False,solver_called=False,scope='first-cell optimistic necessary screen only; no network, no battery incremental reserves, no relaxation accepted as solution')
