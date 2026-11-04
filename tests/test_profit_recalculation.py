"""Historical supplements never mutate dispatch, settlement, snapshots or original reports."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import pytest
import yaml

from smm.profit_recalculation import prepare_recalculation, apply_recalculation, read_recalculation


def write_json(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')


def fixture_run(tmp_path):
    run, source = tmp_path / 'run', tmp_path / 'source'
    config = run / 'scenario_snapshot/config'
    config.mkdir(parents=True)
    (source / 'config').mkdir(parents=True)
    (config / 'base.yaml').write_text(yaml.safe_dump(dict(dt_h=.25, periods=4)))
    (config / 'units.yaml').write_text(yaml.safe_dump(dict(
        thermal=[dict(id='C1', type='coal', init=dict(online=False))],
        storage=[dict(id='ES1')], tie=[dict(id='TIE')])))
    scope = dict(expected_periods=4, executed_periods=2, completed_scope='debug_prefix', settlement_scope='illustrative_prefix')
    write_json(run / 'summary.json', dict(scope, settlement_calculated=True))
    write_json(run / 'fixed_cost_profile.json', dict(available=False, units={}))
    profile = dict(source='Explicit test research assumptions, not plant data', source_kind='research_assumption_not_observed_plant_data', units={
        'C1': dict(type='coal', scalar_marginal_cost_yuan_per_mwh=10, fixed_online_cost_yuan_per_h=4, fixed_startup_cost_yuan_per_start=20),
        'ES1': dict(type='storage', discharge_degradation_yuan_per_mwh=2)})
    (source / 'config/fixed_costs.yaml').write_text(yaml.safe_dump(profile))
    rows, cash, rt, storage = [], [], [], []
    for t in range(2):
        for uid, typ, energy, starts, revenue, charge in [('C1', 'coal', 5., int(t == 0), 100., 0.), ('ES1', 'storage', 2., 0, 20., -10.)]:
            rows.append(dict(unit=uid, type=typ, period=t, energy_generated_or_discharged_mwh=energy,
                startup_count=starts, pump_startup_count=0, cash_revenue_yuan=revenue,
                da_cash_revenue_yuan=revenue, rt_deviation_cash_revenue_yuan=0., charging_settlement_yuan=charge,
                cost_available=False, cost_missing_reason='missing', inventory_start=100. + t if uid == 'ES1' else None,
                inventory_end=101. + t if uid == 'ES1' else None, inventory_change=1. if uid == 'ES1' else None,
                inventory_unit='MWh' if uid == 'ES1' else None, **scope))
            rt.append(dict(unit=uid, type=typ, period=t, on=1, P_mw=20. if uid == 'C1' else 4.))
        for entity, amount in [('C1', 100.), ('ES1_放电', 30.), ('ES1_充电', -10.), ('TIE', 4.), ('L3', -124.)]:
            cash.append(dict(entity=entity, period=t, da_amount=amount, rt_amount=0., total=amount, **scope))
        storage.append(dict(unit='ES1', period=t, P_dis=8., P_ch=4.))
    for name, data in [('operating_profit_by_period', rows), ('rt_units', rt), ('settlement_detail', cash), ('rt_storage', storage)]:
        pd.DataFrame(data).to_csv(run / f'{name}.csv', index=False, float_format='%.4f')
    return run, source


def hashes(run):
    return {str(p.relative_to(run)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in run.rglob('*') if p.is_file() and 'cost_recalculations' not in p.parts}


def test_preview_is_read_only_and_publish_idempotent(tmp_path):
    run, source = fixture_run(tmp_path)
    before = hashes(run)
    plan = prepare_recalculation(run, source)
    assert hashes(run) == before and not (run / 'cost_recalculations').exists()
    assert plan['summary']['completed_scope'] == 'debug_prefix'
    assert plan['summary']['cash_operating_cost_yuan'] == 130
    assert plan['summary']['simulated_operating_profit_yuan'] == 110
    directory = apply_recalculation(plan)
    assert directory == Path(plan['output_dir']) == apply_recalculation(plan)
    result = read_recalculation(directory, run_dir=run)
    assert result['by_unit'].cost_available.all() and result['by_type'].cost_available.all()
    assert result['summary']['original_run_unchanged']
    assert hashes(run) == before


def test_concurrent_clicks_are_idempotent(tmp_path):
    run, source = fixture_run(tmp_path)
    plan = prepare_recalculation(run, source)
    with ThreadPoolExecutor(max_workers=3) as workers:
        paths = list(workers.map(lambda _: apply_recalculation(plan), range(3)))
    assert len(set(paths)) == 1
    assert len(list((run / 'cost_recalculations').glob('*/manifest.json'))) == 1


def test_partial_profile_preserves_original_parameters(tmp_path):
    run, source = fixture_run(tmp_path)
    original = dict(available=True, source='Original user cost', units={'C1': dict(type='coal',
        scalar_marginal_cost_yuan_per_mwh=1, fixed_online_cost_yuan_per_h=4, fixed_startup_cost_yuan_per_start=20)})
    write_json(run / 'fixed_cost_profile.json', original)
    frame = pd.read_csv(run / 'operating_profit_by_period.csv')
    for index, row in frame[frame.unit == 'C1'].iterrows():
        total = 6 + row.startup_count * 20
        frame.loc[index, ['cost_available','variable_cost_yuan','fixed_online_cost_yuan','startup_cost_yuan','cash_operating_cost_yuan','simulated_operating_profit_yuan']] = [True,5,1,row.startup_count*20,total,100-total]
    frame.to_csv(run / 'operating_profit_by_period.csv', index=False)
    plan = prepare_recalculation(run, source)
    assert plan['unit_sources']['C1']['kind'] == 'original_frozen_cost'
    assert plan['unit_sources']['C1']['parameters']['scalar_marginal_cost_yuan_per_mwh'] == 1
    assert plan['unit_sources']['ES1']['kind'] == 'retrospective_supplemental_assumption'
    assert plan['summary']['cash_operating_cost_yuan'] == 40


@pytest.mark.parametrize('change', ['missing_period','duplicate','cash','nan','thermal_starts','storage_energy','foreign_unit','scope','type'])
def test_bad_evidence_is_rejected(tmp_path, change):
    run, source = fixture_run(tmp_path)
    path = run / 'operating_profit_by_period.csv'
    df = pd.read_csv(path)
    if change == 'missing_period': df = df.iloc[:-1]
    elif change == 'duplicate': df = pd.concat([df, df.iloc[:1]])
    elif change == 'cash': df.loc[0, 'cash_revenue_yuan'] += 1
    elif change == 'nan': df.loc[0, 'energy_generated_or_discharged_mwh'] = float('nan')
    elif change == 'thermal_starts': df.loc[0, 'startup_count'] = 0
    elif change == 'storage_energy': df.loc[1, 'energy_generated_or_discharged_mwh'] = 99
    elif change == 'foreign_unit': df.loc[0, 'unit'] = 'FOREIGN'
    elif change == 'scope': df.loc[0, 'executed_periods'] = 4
    elif change == 'type': df.loc[0, 'type'] = 'wind'
    df.to_csv(path, index=False)
    with pytest.raises(ValueError): prepare_recalculation(run, source)
    assert not (run / 'cost_recalculations').exists()


@pytest.mark.parametrize('change', ['source', 'run', 'snapshot'])
def test_stale_plan_rejected(tmp_path, change):
    run, source = fixture_run(tmp_path)
    plan = prepare_recalculation(run, source)
    target = {'source': source / 'config/fixed_costs.yaml', 'run': run / 'summary.json', 'snapshot': run / 'scenario_snapshot/config/base.yaml'}[change]
    target.write_bytes(target.read_bytes() + b'\n')
    with pytest.raises(ValueError): apply_recalculation(plan)


def test_tampered_report_not_overwritten(tmp_path):
    run, source = fixture_run(tmp_path)
    plan = prepare_recalculation(run, source)
    directory = apply_recalculation(plan)
    (directory / 'operating_profit_by_unit.csv').write_text('tampered')
    with pytest.raises(ValueError): apply_recalculation(plan)
    with pytest.raises(ValueError): read_recalculation(directory)


@pytest.mark.parametrize('invalid', ['type', 'partial', 'syntax', 'missing'])
def test_invalid_selected_profile_never_invents_cost(tmp_path, invalid):
    run, source = fixture_run(tmp_path)
    path = source / 'config/fixed_costs.yaml'
    profile = yaml.safe_load(path.read_text())
    if invalid == 'type': profile['units']['ES1']['type'] = 'hydro'
    elif invalid == 'partial': del profile['units']['C1']['fixed_online_cost_yuan_per_h']
    if invalid == 'missing': path.unlink()
    elif invalid == 'syntax': path.write_text('units: [')
    else: path.write_text(yaml.safe_dump(profile))
    with pytest.raises(ValueError): prepare_recalculation(run, source)


def test_saved_supplement_remains_readable_after_selected_source_changes(tmp_path):
    run, source = fixture_run(tmp_path)
    directory = apply_recalculation(prepare_recalculation(run, source))
    (source / 'config/fixed_costs.yaml').unlink()
    assert read_recalculation(directory)['summary']['cash_operating_cost_yuan'] == 130


def test_pumped_separate_generation_and_start_costs(tmp_path):
    run, source = fixture_run(tmp_path)
    path = run / 'scenario_snapshot/config/units.yaml'
    units = yaml.safe_load(path.read_text())
    units['pumped'] = [dict(id='PS1', n_units=2, init_mode='idle')]
    path.write_text(yaml.safe_dump(units))
    path = source / 'config/fixed_costs.yaml'
    costs = yaml.safe_load(path.read_text())
    costs['units']['PS1'] = dict(type='pumped', generation_vom_yuan_per_mwh=3,
        gen_start_yuan_per_unit_start=7, pump_start_yuan_per_unit_start=11)
    path.write_text(yaml.safe_dump(costs))
    profit = pd.read_csv(run / 'operating_profit_by_period.csv')
    added = []
    for t in range(2):
        row = profit[profit.unit == 'ES1'].iloc[t].to_dict()
        row.update(unit='PS1', type='pumped', energy_generated_or_discharged_mwh=0 if t == 0 else 2,
            startup_count=t, pump_startup_count=1 - t, inventory_unit='1e4m3')
        added.append(row)
    pd.concat([profit, pd.DataFrame(added)]).to_csv(run / 'operating_profit_by_period.csv', index=False)
    rt = pd.read_csv(run / 'rt_units.csv')
    rt = pd.concat([rt, pd.DataFrame([dict(unit='PS1', type='pumped', period=t, on=1, P_mw=-4 if t == 0 else 8) for t in range(2)])])
    rt.to_csv(run / 'rt_units.csv', index=False)
    pd.DataFrame([dict(unit='PS1', period=t, P_gen=0 if t == 0 else 8, P_pump=4 if t == 0 else 0, n_gen=t, n_pump=1-t) for t in range(2)]).to_csv(run / 'rt_pumped.csv', index=False)
    settlement = pd.read_csv(run / 'settlement_detail.csv')
    extra = settlement[settlement.entity.str.startswith('ES1')].copy()
    extra.entity = extra.entity.map({'ES1_放电': 'PS1_发电', 'ES1_充电': 'PS1_抽水'})
    pd.concat([settlement, extra]).to_csv(run / 'settlement_detail.csv', index=False)
    plan = prepare_recalculation(run, source)
    assert plan['summary']['cash_operating_cost_yuan'] == 154
    report = read_recalculation(apply_recalculation(plan))
    assert report['by_unit'].set_index('unit').loc['PS1', 'startup_cost_yuan'] == 18
    profit = pd.read_csv(run / 'operating_profit_by_period.csv')
    profit.loc[(profit.unit == 'PS1') & (profit.period == 0), 'pump_startup_count'] = 0
    profit.to_csv(run / 'operating_profit_by_period.csv', index=False)
    with pytest.raises(ValueError, match='pumped startup'):
        prepare_recalculation(run, source)


def test_invalid_original_cost_cannot_be_replaced_by_valid_current_cost(tmp_path):
    run, source = fixture_run(tmp_path)
    original = yaml.safe_load((source / 'config/fixed_costs.yaml').read_text())
    original.update(available=True)
    original['units']['C1'].pop('fixed_online_cost_yuan_per_h')
    write_json(run / 'fixed_cost_profile.json', original)
    with pytest.raises(ValueError, match='fixed_online_cost'):
        prepare_recalculation(run, source)


def test_unavailable_archive_cannot_hide_valid_frozen_costs(tmp_path):
    run, source = fixture_run(tmp_path)
    (run / 'scenario_snapshot/config/fixed_costs.yaml').write_bytes((source / 'config/fixed_costs.yaml').read_bytes())
    with pytest.raises(ValueError, match='contradicts'):
        prepare_recalculation(run, source)


def test_saved_report_rejects_new_files_in_original_snapshot(tmp_path):
    run, source = fixture_run(tmp_path)
    directory = apply_recalculation(prepare_recalculation(run, source))
    (run / 'scenario_snapshot/config/fixed_costs.yaml').write_bytes(
        (source / 'config/fixed_costs.yaml').read_bytes())
    with pytest.raises(ValueError, match='Original run evidence changed'):
        read_recalculation(directory)


def test_generated_supplement_uses_frozen_parameters_not_current_capacity(tmp_path):
    from smm.config import ROOT
    run, source = fixture_run(tmp_path)
    config = run / 'scenario_snapshot/config'
    path = config / 'base.yaml'
    base = yaml.safe_load(path.read_text())
    base['season'] = 'wet'
    path.write_text(yaml.safe_dump(base))
    path = config / 'units.yaml'
    units = yaml.safe_load(path.read_text())
    units['thermal'][0]['pmax'] = 646
    units['storage'][0].update(pmax=100, ecap=200, eta_c=.9, eta_d=.9)
    path.write_text(yaml.safe_dump(units))
    (source / 'config/fixed_costs.yaml').write_bytes((ROOT / 'config/fixed_costs.yaml').read_bytes())
    units['thermal'][0]['pmax'] = 999
    (source / 'config/units.yaml').write_text(yaml.safe_dump(units))
    before = hashes(run)
    plan = prepare_recalculation(run, source)
    assert plan['unit_sources']['C1']['generation_evidence']['capacity_mw'] == 646
    assert plan['unit_sources']['C1']['parameters']['fixed_online_cost_yuan_per_h'] == 6000
    assert plan['summary']['cash_operating_cost_yuan'] == 255900
    report = read_recalculation(apply_recalculation(plan))
    assert report['summary']['executed_periods'] == 2
    assert hashes(run) == before
