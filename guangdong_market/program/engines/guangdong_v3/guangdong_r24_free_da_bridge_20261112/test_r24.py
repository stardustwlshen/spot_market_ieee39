from copy import deepcopy
from datetime import datetime,timedelta
import pytest
import r24_bridge as b

def fixture():
    n=576;ts=range(n);series=lambda v:{j:v for j in ts};day=datetime.fromisoformat('2026-11-11T00:00:00+08:00');dates=['2026-11-11','2026-11-12']
    thermal=dict(bus='1',initial_power_mw=40.,min_mw=series(20.),max_mw=series(100.),ramp_up_mw_per_h=series(100.),ramp_down_mw_per_h=series(100.),bid_base_mw=20.,base_bid_yuan_per_h=series(600.),segments=[dict(width_mw=80.,price_yuan_per_mwh=series(30.))])
    battery=dict(bus='1',energy_capacity_mwh=100.,roundtrip_efficiency=.81,initial_energy_mwh=50.,energy_min_mwh=10.,energy_max_mwh=90.,terminal_energy_mwh=50.,terminal_at='2026-11-12T00:00:00+08:00',charge_max_mw=20.,discharge_max_mw=20.,initial_power_mw=0.,ramp_up_mw_per_h=240.,ramp_down_mw_per_h=240.,charge_curve=dict(breakpoints_mw=[0.,20.],prices_yuan_per_mwh=[20.]),discharge_curve=dict(breakpoints_mw=[0.,20.],prices_yuan_per_mwh=[40.]),cycle_budget={d:dict(limit_cycles=1.,used_internal_mwh=0.)for d in dates})
    case=dict(energy_inputs=dict(horizon={},thermal={'G':thermal},buses=['1'],load_mw={('1',j):40. for j in ts},renewable_mw={('1',j):0. for j in ts},physical_tie_mw={('1',j):0. for j in ts},lines={},sections={}),batteries={'B':battery},reserve=dict(positive_mw=series(400.),negative_mw=series(0.),primary_mw=series(0.),contingency_mw=series(400.),primary_fraction={'G':.06},contingency_eligible={'G':True}),curves={},contract=dict(economic_policy='continuous48h_research',day_start=day.isoformat(),cutoff='2026-11-10T12:30:00+08:00',publication_at='2026-11-10T20:30:00+08:00',representatives=[dict(physical_index=363),dict(physical_index=498)]))
    report=dict(events=[],physical_cells=[dict(index=j,thermal={'G':dict(phase='stable',stable=True,mean_mw=40.,end_mw=40.,curve_step=None)},battery={'B':dict(charge_mw=0.,discharge_mw=0.,energy_mwh=50.)})for j in ts],terminal_obligations={'G':{'phase':'stable','state_age_h':48.}})
    vals={}
    for j in ts:
        vals.update({f'stable[G,{j}]':1.,f'off[G,{j}]':0.,f'power[G,{j}]':40.,f'battery.charge_mode[B,{j}]':0.,f'battery.discharge_mode[B,{j}]':0.,f'battery.idle_mode[B,{j}]':1.,f'battery.charge[B,{j}]':0.,f'battery.discharge[B,{j}]':0.,f'battery.energy[B,{j}]':50.})
    receipt=dict(schema=b.SCHEMA+'.source_receipt',stage='fixed_SCED',status='Optimal',run_id='synthetic-new-SCED',scuc_run_id='synthetic-new-SCUC',case_sha256=b.case_digest(case),physical_report_sha256=b.digest(report),expanded_values_sha256=b.digest(vals),source_kind='synthetic_pure_test_NOT_native',logical_available_at='2026-11-10T20:00:00+08:00',generated_at='2026-11-12T09:00:00+08:00',SCUC_accepted=True,diagnostic_only=False,publication_allowed=True)
    spec=dict(mode='research',source='synthetic explicit R24 test; no actual feasible SCED asserted',available_at='2026-11-10T12:00:00+08:00',delta=.1,delta_source='research .1',representatives=[dict(kind='valley',physical_index=363,objective_weight_h=.25,mapping_source='synthetic explicit valley'),dict(kind='peak',physical_index=498,objective_weight_h=.25,mapping_source='synthetic explicit peak')],thermal_price_eligible={'G':True},eligibility_source='synthetic eligibility only',network={},rule_source='regional2026V1 pure schema',terminal_source='explicit synthetic D96 target research')
    return case,report,vals,receipt,spec

def rebind(x):
    c,r,v,receipt,s=x;receipt.update(case_sha256=b.case_digest(c),physical_report_sha256=b.digest(r),expanded_values_sha256=b.digest(v));return x

def test_new_dto_98_576_no_old_identity_and_pending_publish():
    x=fixture();out=b.build_price_dto(*x);d=out['DTO']
    assert len(d['grid'])==98 and len(d['physical_lift']['cells'])==576
    assert d['physical_lift']['market_to_physical'][96:]==[[363,364,365],[498,499,500]]
    assert d['resources']['G']['rows'][96]['sced_mw']==40 and d['grid'][96]['ramp_elapsed_h']==6.5
    assert d['resources']['B']['terminal_targets']==[dict(index=95,energy_mwh=50.)]
    assert d['source_binding']['R24_receipt']['run_id']=='synthetic-new-SCED'and not out['legacy_R14_authority_reused']
    assert all(z['positive_mw']==z['accident_mw']==400 for z in d['reserve'])
    pub=b.publish_candidate(out,available_at='2026-11-10T20:30:00+08:00');assert len(pub['rows'])==96 and pub['rows'][0]['prices']is None and not pub['execution_authority']

@pytest.mark.parametrize('mutation',[
    lambda x:x[3].update(status='TimeLimit'),
    lambda x:x[3].update(schema='gd.r14.accepted'),
    lambda x:x[3].update(run_id=x[3]['scuc_run_id']),
    lambda x:x[3].update(case_sha256='0'*64),
    lambda x:x[3].update(logical_available_at='2026-11-11T00:00:00+08:00'),
    lambda x:x[4].update(available_at='2026-11-10T18:00:00+08:00'),
    lambda x:x[4]['representatives'][0].update(kind='peak'),
    lambda x:x[4]['representatives'][0].update(physical_index=360),
])
def test_stale_identity_future_mapping_reject(mutation):
    x=fixture();mutation(x)
    with pytest.raises(ValueError):b.build_price_dto(*x)

def test_zero_battery_power_does_not_imply_idle():
    x=fixture();del x[2]['battery.charge_mode[B,0]'];rebind(x)
    with pytest.raises(ValueError,match='discrete witness missing'):b.build_price_dto(*x)

def test_SOC_not_reset_or_terminal_moved():
    x=fixture();x[1]['physical_cells'][289]['battery']['B']['energy_mwh']=51.;rebind(x)
    with pytest.raises(ValueError,match='SOC'):b.build_price_dto(*x)
    x=fixture();x[0]['batteries']['B']['terminal_at']='2026-11-12T17:45:00+08:00';rebind(x)
    with pytest.raises(ValueError,match='D96'):b.build_price_dto(*x)

def test_formal_fail_closed_and_diagnostic_not_publishable():
    x=fixture()
    with pytest.raises(ValueError,match='formal blocked'):b.build_price_dto(*x,formal_strict=True)
    x[3].update(SCUC_accepted=False,diagnostic_only=True,publication_allowed=False)
    z=b.build_price_dto(*x);assert z['diagnostic_only']and not z['publication_allowed']
    with pytest.raises(ValueError,match='publication blocked'):b.publish_candidate(z,available_at='2026-11-10T20:30:00+08:00')

def test_actual_labels_need_new_certificate_and_raw_hashes():
    x=fixture();x[3].update(source_kind='native_highs_sced',new_fixed_sced_primal_KKT_verified=False,SCUC_fixed_discrete_match_verified=True)
    with pytest.raises(ValueError):b.build_price_dto(*x)

def test_no_sced_dual_price_and_distinct_new_price_run():
    z=b.build_price_dto(*fixture());p=dict(DTO_sha256=z['DTO_sha256'],run_id='new-PRICE',status='Optimal',independent_price_KKT_verified=True,logical_available_at='2026-11-10T20:20:00+08:00',prices={'1':[30.]*98})
    pub=b.publish_candidate(z,price_result=p,available_at='2026-11-10T20:30:00+08:00');assert len(pub['rows'])==96 and pub['rows'][95]['prices']=={'1':30.}
    p['run_id']=z['source_receipt']['run_id']
    with pytest.raises(ValueError):b.publish_candidate(z,price_result=p,available_at='2026-11-10T20:30:00+08:00')

def test_input_immutable():
    x=fixture();before=b.case_digest(x[0]);r=b.digest(x[1]);b.build_price_dto(*x);assert b.case_digest(x[0])==before and b.digest(x[1])==r

def test_permission_at_zero_is_not_executed_charge():
    x=fixture()
    for j in range(576):
        x[2][f'battery.charge_mode[B,{j}]']=1.;x[2][f'battery.idle_mode[B,{j}]']=0.
    rebind(x);z=b.build_price_dto(*x);row=z['DTO']['physical_lift']['resources']['B']['rows'][0]
    assert row['mode']=='charge'and row['actual_direction']=='idle'and row['min_mw']==-20.

def test_unsupported_groups_and_varying_quote_not_silent():
    x=fixture();x[0]['energy_inputs']['groups']=['unmapped'];rebind(x)
    with pytest.raises(ValueError,match='group mapping'):b.build_price_dto(*x)
    x=fixture();x[0]['energy_inputs']['thermal']['G']['base_bid_yuan_per_h'][1]=601.;rebind(x)
    with pytest.raises(ValueError,match='within-quarter bid'):b.build_price_dto(*x)

def test_dto_builds_pure_independent_price_model():
    from smm.gd_r14_da_lmp import build_model
    d=b.build_price_dto(*fixture())['DTO'];m=build_model(d)
    assert m is not None

def test_idle_expression_derived_from_two_discrete_permissions_not_power():
    x=fixture()
    for j in range(576):del x[2][f'battery.idle_mode[B,{j}]']
    rebind(x);z=b.build_price_dto(*x)
    assert z['DTO']['physical_lift']['resources']['B']['rows'][0]['mode']=='idle'

def test_native_event_wrong_request_or_response_rejected(tmp_path):
    import json,hashlib
    from r24_provenance import bind_scuc_event
    write=lambda n,v:(tmp_path/n).write_text(json.dumps(v))
    write('request.json',{});write('response.json',{})
    write('IDENTITY.json',dict(request_sha256='0'*64));write('launch.json',{});write('CONFIRMED_NATIVE_RESULT.json',{});write('PURE_PREPARATION.json',{})
    with pytest.raises(ValueError,match='request/event'):bind_scuc_event(tmp_path,tmp_path,tmp_path/'post','0'*64,'0'*64)
    write('IDENTITY.json',dict(request_sha256=hashlib.sha256((tmp_path/'request.json').read_bytes()).hexdigest()));write('CONFIRMED_NATIVE_RESULT.json',dict(response_sha256='0'*64))
    with pytest.raises(ValueError,match='response/event'):bind_scuc_event(tmp_path,tmp_path,tmp_path/'post','0'*64,'0'*64)
