from copy import deepcopy
import json
import numpy as np
from smm import series,state_bridge as bridge,scuc
from smm.case import build_da_case
from multiday_fixture import micro_day
from multiday_replay import replay


def active_day(date,second=False):
    day,state=micro_day(date,second_day=second)
    day.cfg.units['pumped'][0].update(start_cost_gen=0.,start_cost_pump=0.)
    for h in day.cfg.units['hydro']:
        h['zones']=[[1.,5.]];h['ut']=4.
    # Existing research declaration remains explicit; refresh its longer obligation.
    state=bridge.research_initial_state(day.cfg,acknowledge_constant_prehistory=True)
    state.pumped['PS1']['V']=115.1234567890123;state.storage['ES1']=12.3456789012345
    state.hydro.update(H1=101.1234567890123,H2=102.2345678901234);state.continuation['day_initial_hydro']=deepcopy(state.hydro)
    return day,state


def test_real_two_day_nonzero_pumped_and_hydro_pending(tmp_path):
    one,state=active_day('2026-07-15');two,_=active_day('2026-07-16',True)
    result=series.run_series([one,two],tmp_path/'active',initial_actual_state=state,log=lambda *a:None)
    first,second=result['new_results'];end=first['rr'].states[-1];start=second['rr'].states[0]
    assert end.pumped_used['PS1']['gen']>0 and end.pumped_used['PS1']['pump']>0
    assert any(end.pumped_hist['PS1']['xg'][-3:]) or any(end.pumped_hist['PS1']['xp'][-3:])
    assert start.pumped==end.pumped and start.pumped_used['PS1']==dict(gen=0.,pump=0.)
    assert start.pumped_pre_history['PS1']['values']['xg']==end.pumped_hist['PS1']['xg'][-3:].tolist()
    assert any(p['kind']=='hydro_unit_duration' for p in result['checkpoints'][0]['pending'])
    for r in (first,second):assert r['checks']['pumped_cross_day_history_and_start_budget_valid'] and r['checks']['hydro_identified_commitment_execution_valid']
    reports=[replay(tmp_path/'active'/('day_'+d)) for d in ('2026-07-15','2026-07-16')]
    # The next-day DA consumes the actual prior mode, not cfg idle.
    assert second['da'].model._md.pumped['PS1']['init']==end.pumped['PS1']
    (tmp_path/'active_history_test_report.json').write_text(json.dumps(dict(checkpoints=result['checkpoints'],replay=reports),ensure_ascii=False,indent=2))
    print('ACTIVE_HISTORY_EVIDENCE',tmp_path)


def test_more_than_one_day_history_retained_from_explicit_prehistory(tmp_path):
    # Pure state bridge counterexample, no assertion of a solved long-lag market.
    one,state=active_day('2026-07-15');two,_=active_day('2026-07-16',True)
    for day in (one,two):
        day.cfg.units['hydro'][1]['lag_periods']=30
        day.cfg.units['pumped'][0]['switch_gap']=30
    state=bridge.research_initial_state(one.cfg,acknowledge_constant_prehistory=True)
    state.t=24;state.hydro_hist={h:np.arange(24,dtype=float) for h in state.hydro}
    state.hydro_pre_history['H1']['values']=list(range(30));state.hydro_pre_history['H1']['sources']=['executed']*30
    state.pumped_hist={'PS1':{'xg':np.zeros(24),'xp':np.zeros(24)}}
    # Clock/age block remains an exact state boundary, independent from these history values.
    from smm import hydro_commitment as hc
    state.hydro_commitment['state_at']=hc.stamp(hc.window_time(one.cfg,24))
    for station in state.hydro_commitment['stations'].values():
        for unit in station['units'].values():unit['elapsed_h']+=24
    state.storage_dispatch={'ES1':dict(P_ch=0.,P_dis=0.,u_ch=0,u_dis=0,source='executed')}
    state.pumped_dispatch={'PS1':dict(P_gen=0.,P_pump=0.,xg=0,xp=0,source='executed')}
    payload=dict(schema_version=1,kind='completed_actual_day',state_at=bridge.at(one.cfg,24),state=bridge.pack_state(one.cfg,state),
        source_cfg=dict(base=one.cfg.base,units=one.cfg.units,mode=one.cfg.mode),physical_identity=bridge.physical_identity(one.cfg,one.net))
    checkpoint=dict(payload,checkpoint_hash=bridge.digest(payload));nextstate=bridge.next_day_state(checkpoint,two.cfg,net=two.net)
    assert nextstate.hydro_pre_history['H1']['values']==list(range(24,30))+list(range(24))
    assert len(nextstate.pumped_pre_history['PS1']['values']['xg'])==30
    nextstate.hydro_pre_history['H1']['values']=nextstate.hydro_pre_history['H1']['values'][1:]
    import pytest
    with pytest.raises(ValueError):bridge.validate_system_state(two.cfg,nextstate,expected_t=0,continuation_required=True)
