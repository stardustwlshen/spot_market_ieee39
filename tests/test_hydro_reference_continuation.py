"""Known histories and selected forecast references, never future actual water."""
import copy
from types import SimpleNamespace as NS
import numpy as np
import pytest
from smm import rolling, handoff, value_outputs
from test_handoff_integrity import context


def metadata(cfg,plan):
    return handoff.selected_reference(cfg,plan,None,False)[1]


def test_reference_outflow_not_optimistic_minimum_drives_future_target(context):
    cfg,plan=context;h=cfg.units['hydro'][0];uid=h['id'];plan['Qout'][uid][:]=500.
    row=rolling.hydro_terminal_lookahead_terms(cfg,plan,rolling.initial_state(cfg),0,8,metadata(cfg,plan))[uid]
    season=h['seasons'][cfg.base['season']]
    expect=(season['qin']-500.)*.25*.36*88
    assert row['reference_recovery_volume']==pytest.approx(expect)
    assert row['minimum_window_end_volume']==pytest.approx(season['v_end']-expect)
    assert row['future_periods']==list(range(8,96))
    assert row['reference_outflows_m3s']==[500.]*88
    assert row['reference_source']=='day_ahead'


def test_selected_id_reference_and_da_fallback_are_explicit(context):
    cfg,plan=context;meta=metadata(cfg,plan);uid=cfg.units['hydro'][0]['id']
    meta['V'][uid].update(trajectory_source='intraday@4',round=4)
    row=rolling.hydro_terminal_lookahead_terms(cfg,plan,rolling.initial_state(cfg),4,12,meta)[uid]
    assert row['reference_source']=='intraday' and row['reference_round']==4
    assert row['reference_source_label']=='intraday@4'
    meta['V'][uid].update(trajectory_source='day_ahead',round=None,fallback_reason='infeasible')
    row=rolling.hydro_terminal_lookahead_terms(cfg,plan,rolling.initial_state(cfg),4,12,meta)[uid]
    assert row['reference_source']=='day_ahead' and row['reference_fallback_reason']=='infeasible'


def test_no_double_charge_at_day_end_or_legacy_hard_policy(context):
    cfg,plan=context;state=rolling.initial_state(cfg)
    assert rolling.hydro_terminal_lookahead_terms(cfg,plan,state,88,96,metadata(cfg,plan)) is None
    cfg.mode['realtime']['value_method']['terminal_policy']={'mode':'hard'}
    assert rolling.hydro_terminal_lookahead_terms(cfg,plan,state,0,8,metadata(cfg,plan)) is None


def test_unattainable_reference_threshold_is_not_clipped_to_physical_bounds(context):
    cfg,plan=context;h=cfg.units['hydro'][0];uid=h['id'];plan['Qout'][uid][:]=1.e6
    row=rolling.hydro_terminal_lookahead_terms(cfg,plan,rolling.initial_state(cfg),0,8,metadata(cfg,plan))[uid]
    assert row['minimum_window_end_volume']>h['seasons'][cfg.base['season']]['vmax']


def cascade(context):
    cfg,plan=context;up=cfg.units['hydro'][0]['id'];d=copy.deepcopy(cfg.units['hydro'][0]);d.update(id='H_child',upstream=up,lag_periods=12)
    cfg.units['hydro'].append(d);plan['Qout']['H_child']=np.full(cfg.T,90.);plan['Qout'][up][:]=100.
    state=NS(hydro_hist={up:np.r_[np.full(10,123.),np.full(cfg.T-10,999999.)]})
    return cfg,plan,state,up


def test_lagged_known_water_uses_actual_history_unknown_uses_reference(context):
    cfg,plan,state,up=cascade(context)
    row=rolling.hydro_terminal_lookahead_terms(cfg,plan,state,10,18,{})['H_child']
    assert row['upstream_arrivals_m3s'][:4]==[123.]*4
    assert row['upstream_arrivals_m3s'][4:]==[100.]*(cfg.T-18-4)
    assert row['upstream_arrival_sources'][:4]==['executed_history']*4
    assert row['upstream_arrival_sources'][4:]==['selected_reference']*(cfg.T-18-4)
    state.hydro_hist[up][6]=np.nan
    with pytest.raises(ValueError,match='已执行上游出库'):
        rolling.hydro_terminal_lookahead_terms(cfg,plan,state,10,18,{})


def test_output_rows_keep_window_scope_and_full_forecast_inputs(context):
    cfg,plan=context
    row=rolling.hydro_terminal_lookahead_terms(cfg,plan,rolling.initial_state(cfg),4,12,{})['H1']
    row.update(unit='H1',forecast_only=True)
    output=value_outputs.hydro_terminal_lookahead_rows(NS(rt=[{'t':4,'snap':{'hydro_terminal_lookahead':[row]}}]))
    assert output[0]['window_start']==4 and output[0]['tau']==11
    assert output[0]['future_periods']==list(range(12,96))


@pytest.mark.parametrize("selector", [None, "off"])
def test_old_scenes_and_explicit_off_do_not_silently_add_lookahead(context, selector):
    cfg,plan=context
    spec=cfg.mode['realtime']['value_method']
    if selector is None:
        spec.pop('hydro_lookahead',None)
    else:
        spec['hydro_lookahead']=selector
    assert rolling.hydro_terminal_lookahead_terms(cfg,plan,rolling.initial_state(cfg),0,8,{}) is None


@pytest.mark.parametrize("selector", [True, False, None, "reference", 1])
def test_invalid_lookahead_selector_is_rejected(context, selector):
    from smm.config import validate_value_method
    cfg,plan=context
    cfg.mode['realtime']['value_method']['hydro_lookahead']=selector
    with pytest.raises(ValueError,match='hydro_lookahead'):
        validate_value_method(cfg.mode)
    with pytest.raises(ValueError,match='hydro_lookahead'):
        rolling.hydro_terminal_lookahead_terms(cfg,plan,rolling.initial_state(cfg),0,8,{})


def test_cross_day_initial_outflow_matches_physical_model(context):
    cfg,plan,state,up=cascade(context)
    upstream=cfg.units['hydro'][0]
    upstream.update(water_rate=1.2,init_units_on=2,init_p_unit=100.)
    # RT[0,8): arrivals at absolute 8..11 refer to pre-day periods -4..-1.
    row=rolling.hydro_terminal_lookahead_terms(cfg,plan,state,0,8,{})['H_child']
    assert row['upstream_arrivals_m3s'][:4]==[240.]*4
    assert row['upstream_arrival_sources'][:4]==['pre_day_initial_outflow']*4
    assert row['upstream_arrivals_m3s'][4:]==[100.]*84
    child=cfg.units['hydro'][-1];qin=child['seasons'][cfg.base['season']]['qin']
    zero_history_recovery=((qin-90.)*88+100.*84)*cfg.dt*.36
    assert row['reference_recovery_volume']-zero_history_recovery==pytest.approx(86.4)


def test_cross_day_zero_initial_unit_count_stays_zero(context):
    cfg,plan,state,up=cascade(context);cfg.units['hydro'][0]['init_units_on']=0
    row=rolling.hydro_terminal_lookahead_terms(cfg,plan,state,0,8,{})['H_child']
    assert row['upstream_arrivals_m3s'][:4]==[0.]*4
    assert row['upstream_arrival_sources'][:4]==['pre_day_initial_outflow']*4


def test_pre_day_lookahead_matches_real_scuc_upstream_expression():
    from test_hydro_rolling import hydro_case
    from smm import scuc
    case=hydro_case(T=20);cfg=case.cfg
    up=cfg.units['hydro'][0];up.update(water_rate=1.2,init_units_on=1,init_p_unit=100.)
    child=copy.deepcopy(up);child.update(id='H2',upstream='H1',lag_periods=12)
    cfg.units['hydro'].append(child);case.bids['units']['H2']=copy.deepcopy(case.bids['units']['H1'])
    model=scuc.build_model(case)
    for t in (0,8,11):
        assert float(model._upstream_out('H2',t))==rolling._pre_day_hydro_outflow(cfg,'H1')==120.
