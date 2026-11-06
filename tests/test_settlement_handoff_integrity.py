"""Real two-period cash counterexamples: reconciliation alone is insufficient."""
from copy import deepcopy
from types import SimpleNamespace as NS
import numpy as np
import pandas as pd
import pytest
import yaml
from smm.da import run_da
from smm.rolling import snapshot
from smm.settlement import settle
from smm.profit import analyze
from smm import spot_costs
from test_da_stages import tiny_case


def evidence(tmp_path):
    case = tiny_case(reserve=0.)
    case.cfg.base['periods'] = 2
    case.load_bus = {2: np.array([40., 100.])}
    case.R_up = case.R_dn = np.zeros(2)
    da = run_da(case, verbose=False)
    sn = snapshot(da.model)
    rt = []
    for t in (0, 1):
        part = deepcopy(sn)
        for key in ('P','u','Pw','Ph','Pd','Pc','Pg','Pp','load_bus','shed'):
            part[key] = {u: a[t:t+1] for u,a in sn[key].items()}
        part['surplus'] = sn['surplus'][t:t+1]
        prices = {k:(v[:,t:t+1] if isinstance(v,np.ndarray) and v.ndim==2 else v) for k,v in da.prices.items()}
        rt.append(dict(t=t,snap=part,prices=prices))
    states = [NS(t=t,thermal={u['id']:deepcopy(u['init']) for u in case.cfg.units['thermal']}) for t in (0,1)]
    rr = NS(rt=rt,states=states)
    (tmp_path/'config').mkdir(exist_ok=True)
    profile = dict(source='independent fixed synthetic costs',units={u['id']:dict(type=u['type'],
        scalar_marginal_cost_yuan_per_mwh=1.,fixed_online_cost_yuan_per_h=0.,fixed_startup_cost_yuan_per_start=0.) for u in case.cfg.units['thermal']})
    (tmp_path/'config/fixed_costs.yaml').write_text(yaml.safe_dump(profile))
    case.cfg.root=tmp_path
    return da,rr


@pytest.mark.parametrize('policy', [spot_costs.LEGACY, spot_costs.BASIS])
def test_zero_physical_deviation_has_zero_cash_deviation_and_exact_actual_profit(tmp_path, policy):
    da,rr=evidence(tmp_path);result=settle(da,rr)
    if policy == spot_costs.BASIS:
        # This new-policy fixture declares fuel separately. The old scalar file
        # remains intact and must never be silently used as a spot cost fallback.
        profile = spot_costs.empty_profile()
        profile['units'] = {u['id']: dict(type=u['type'],
            source='Explicit synthetic fuel cost; excludes O&M', source_kind='user_defined',
            fuel_cost_yuan_per_mwh=1., online_cost_yuan_per_h=0.,
            online_cost_basis='no_load_fuel', startup_cost_yuan_per_start=0.,
            shutdown_cost_yuan_per_stop=0.) for u in da.case.cfg.units['thermal']}
        (tmp_path/'config'/spot_costs.FILENAME).write_text(yaml.safe_dump(profile))
    np.testing.assert_allclose(result['detail'].q_rt,result['detail'].q_da,atol=1e-9)
    assert result['execution_order_source']=='explicit_ordered_rt_periods'
    np.testing.assert_allclose(result['detail'].rt_amount,0.,atol=1e-9)
    report=analyze(dict(da=da,rr=rr,settle=result),policy=policy)
    assert report['summary']['profit_basis'] == policy
    assert report['summary']['all_unit_costs_available']
    # Keep the actual-quantity and each cost/cash component checks, not only
    # a system reconciliation or a hard-coded total profit.
    cash = result['detail'].set_index(['entity', 'period'])
    for row in report['by_period'].itertuples():
        quantity = rr.rt[row.period]['snap']['P'][row.unit][0]
        amount = cash.loc[(row.unit, row.period)]
        assert row.energy_generated_or_discharged_mwh == pytest.approx(quantity * .25)
        assert row.variable_cost_yuan == pytest.approx(quantity * .25 * 1.)
        assert row.fixed_online_cost_yuan == row.startup_cost_yuan == row.shutdown_cost_yuan == 0.
        assert row.cash_revenue_yuan == pytest.approx(amount.da_amount)
        assert row.rt_deviation_cash_revenue_yuan == pytest.approx(0., abs=1e-9)
        assert row.cash_operating_cost_yuan == pytest.approx(row.variable_cost_yuan)
        assert row.simulated_operating_profit_yuan == pytest.approx(amount.total - quantity * .25)
    units = report['by_unit'].set_index('unit')
    assert units.loc['CHEAP', 'simulated_operating_profit_yuan'] == pytest.approx(202.5)
    assert units.loc['LOCAL', 'simulated_operating_profit_yuan'] == pytest.approx(8737.5)
    # CHEAP 90MW-period * .25h * (10-1), LOCAL 50 * .25 * (700-1).
    assert report['summary']['simulated_operating_profit_yuan']==pytest.approx(8940.)
    assert result['reconciliation'].residual.abs().max()<1e-9


def test_new_default_does_not_infer_spot_costs_from_legacy_scalar(tmp_path):
    da,rr=evidence(tmp_path);result=settle(da,rr)
    assert (tmp_path/'config/fixed_costs.yaml').is_file()
    assert not (tmp_path/'config'/spot_costs.FILENAME).exists()
    report=analyze(dict(da=da,rr=rr,settle=result))
    assert report['summary']['profit_basis'] == spot_costs.BASIS
    assert report['summary']['cash_revenue_yuan'] == pytest.approx(8975.)
    assert report['summary']['cash_operating_cost_yuan'] is None
    assert report['summary']['simulated_operating_profit_yuan'] is None
    assert not report['by_period'].cost_available.any()
    assert report['by_period'].cash_operating_cost_yuan.isna().all()
    np.testing.assert_allclose(result['detail'].rt_amount,0.,atol=1e-9)


def test_spot_nonzero_signed_purchase_not_deducted_again_as_cost(tmp_path):
    # Independent analytic accounting fixture, not a claim of a market solve.
    from test_operating_profit import sample
    res,_=sample(tmp_path)
    profile=spot_costs.empty_profile()
    def record(kind, **fields):
        row=dict(type=kind,source='Explicit synthetic non-O&M costs',source_kind='user_defined')
        row.update(fields)
        if kind not in spot_costs.THERMAL and kind != 'pumped':
            row['cost_scope']='no_fuel_or_defined_start_stop_costs_excluding_om'
        return row
    profile['units']={
        'G':record('gas_gt',fuel_cost_yuan_per_mwh=20.,online_cost_yuan_per_h=40.,
            online_cost_basis='no_load_fuel',startup_cost_yuan_per_start=100.,shutdown_cost_yuan_per_stop=0.),
        'W':record('wind'),'H':record('hydro'),'B':record('storage'),
        'PS':record('pumped',gen_start_yuan_per_unit_start=30.,pump_start_yuan_per_unit_start=50.,
            gen_stop_yuan_per_unit_stop=0.,pump_stop_yuan_per_unit_stop=0.)}
    (tmp_path/'config'/spot_costs.FILENAME).write_text(yaml.safe_dump(profile))
    res['da'].costs={'line_penalty':1e12,'anchor_penalty':1e12,'hydro_water_opportunity':1e12}
    report=analyze(res);units=report['by_unit'].set_index('unit')
    assert report['summary']['profit_basis'] == spot_costs.BASIS
    assert units.loc['B','charging_settlement_yuan'] == -120.
    assert units.loc['B','cash_revenue_yuan'] == 2*100.-120.
    assert units.loc['B','cash_operating_cost_yuan'] == 0.
    assert units.loc['B','simulated_operating_profit_yuan'] == 80.
    assert units.loc['PS','charging_settlement_yuan'] == -100.
    assert units.loc['PS','cash_revenue_yuan'] == 2*200.-100.
    assert units.loc['PS','startup_cost_yuan'] == units.loc['PS','cash_operating_cost_yuan'] == 30.
    assert units.loc['PS','simulated_operating_profit_yuan'] == 270.
    assert units.loc['H','cash_operating_cost_yuan'] == units.loc['W','cash_operating_cost_yuan'] == 0.
    assert units.loc['G','cash_operating_cost_yuan'] == 2*100*.25*20 + 2*.25*40 + 100
    assert report['summary']['cash_operating_cost_yuan'] == 1150.


@pytest.mark.parametrize('damage',['reverse','duplicate','missing','mixed','float','bool','state_reverse'])
def test_invalid_execution_order_rejected_by_both_settlement_and_profit(tmp_path,damage):
    da,rr=evidence(tmp_path);good=settle(da,rr)
    if damage=='reverse':rr.rt.reverse();rr.states.reverse()
    elif damage=='duplicate':rr.rt[1]['t']=0
    elif damage=='missing':rr.rt[1]['t']=2
    elif damage=='mixed':rr.rt[1].pop('t')
    elif damage=='float':rr.rt[0]['t']=0.
    elif damage=='bool':rr.rt[0]['t']=False
    else:rr.states.reverse()
    # Original abd1cb7 returned LOCAL +8625 yuan on reverse, with reconciliation residual 0.
    with pytest.raises(ValueError):settle(da,rr)
    with pytest.raises(ValueError):analyze(dict(da=da,rr=rr,settle=good))


def test_all_legacy_positional_records_are_explicit_and_still_validated(tmp_path):
    da,rr=evidence(tmp_path);expected=settle(da,rr)
    for record in rr.rt:record.pop('t')
    result=settle(da,rr)
    assert result['execution_order_source']=='legacy_positional_all_records_without_t'
    pd.testing.assert_frame_equal(result['detail'],expected['detail'])
    rr.rt[0]['snap']['P']['CHEAP'][0]=np.nan
    with pytest.raises(ValueError):settle(da,rr)


@pytest.mark.parametrize('damage',['power_nan','price_nan','foreign_unit','missing_unit','missing_bus','wrong_shape','bad_clip'])
def test_bad_quantity_or_price_evidence_never_becomes_zero_cash(tmp_path,damage):
    da,rr=evidence(tmp_path);record=rr.rt[0]
    if damage=='power_nan':record['snap']['P']['CHEAP'][0]=np.nan
    elif damage=='price_nan':record['prices']['lmp'][0,0]=np.nan
    elif damage=='foreign_unit':record['snap']['P']['FOREIGN']=record['snap']['P'].pop('CHEAP')
    elif damage=='missing_unit':record['snap']['P'].pop('CHEAP')
    elif damage=='missing_bus':record['snap']['load_bus'].pop(1)
    elif damage=='wrong_shape':record['prices']['lmp']=np.zeros((1,1))
    else:record['prices']['lmp_clamped'][0,0]+=1.
    with pytest.raises(ValueError):settle(da,rr)


@pytest.mark.parametrize('damage',['cash_formula','quantity_link','missing_economic_column'])
def test_profit_verifies_cash_formula_and_actual_quantity_link(tmp_path,damage):
    da,rr=evidence(tmp_path);result=settle(da,rr);d=result['detail']
    row=d.index[(d.entity=='LOCAL') & (d.period==1)][0]
    if damage=='cash_formula':
        d.loc[row,['da_amount','total']]+=100. # Internal DA+RT=total still holds.
    elif damage=='quantity_link':
        d.loc[row,'q_rt']+=1.
        d.loc[row,'rt_amount']+=d.loc[row,'lmp_rt']*da.case.cfg.dt
        d.loc[row,'total']+=d.loc[row,'lmp_rt']*da.case.cfg.dt
    else:result['detail']=d.drop(columns=['lmp_da'])
    with pytest.raises(ValueError):analyze(dict(da=da,rr=rr,settle=result))
