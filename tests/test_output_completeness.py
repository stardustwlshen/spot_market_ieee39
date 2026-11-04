"""Actual solves and disk re-reads for complete DA/RT exports, including old supply ties."""
from copy import deepcopy
import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from smm import full, pipeline, rolling
from smm.bids import default_bids
from smm.config import load_config
from smm.da import run_da
from test_hydro_rolling import hydro_case


def export_case():
    case = hydro_case(T=8)
    cfg = case.cfg
    cfg.base['tie_line_mode'] = 'supply'
    cfg.mode['auxiliary_pricing'].update(enabled=True, rt_tracking_policy='remove')
    cfg.mode['realtime']['tracking_policy'] = dict(mode='soft_deliverable_energy', penalty_yuan_per_discharge_mwh=1000.)
    cfg.mode['reserve'].update(storage_reserve=True, down_enabled=False, T_dur_h=.25)
    base = load_config()
    battery = deepcopy(base.units['storage'][0]); battery.update(bus=2)
    cfg.units['storage'] = [battery]
    case.bids['units'][battery['id']] = deepcopy(default_bids(base)['units'][battery['id']])
    case.bids['units'][battery['id']]['soc_end'] = None
    # Supply is exogenous, present in DA and RT exactly once, never demand.
    case.tie = {'IMPORT': (2, np.full(8, 10.))}
    cfg.units['tie'] = [dict(id='IMPORT', bus=2, pmax=10.)]
    case.profiles['IMPORT_sched'] = 10.
    return case


@pytest.fixture(scope='module')
def exported_result():
    case = export_case()
    return full.run_full(case.cfg, case, periods=3, log=lambda *a: None)


def test_complete_da_and_rt_archive_reloads_and_hashes(exported_result, tmp_path):
    res = exported_result
    assert res['checks']['rt_balance_err_mw'] < 1e-6
    summary = full.write_full_outputs(res, tmp_path, make_plots=True)
    assert summary['da']['termination'] == res['da'].scuc_info['termination']
    solver=pd.read_csv(tmp_path/'solver_windows.csv')
    assert len(solver)==4 and solver.stage.tolist()==['DA','RT','RT','RT']
    manifest = json.loads((tmp_path/'output_manifest.json').read_text())
    assert manifest['executed_periods'] == 3 and manifest['completed_scope'] == 'debug_prefix'
    assert summary['settlement_scope'] == 'illustrative_prefix'
    assert not summary['settled']
    for item in manifest['artifacts']:
        assert item['present'], item
        assert hashlib.sha256((tmp_path/item['file']).read_bytes()).hexdigest() == item['sha256']
        if item['file'].endswith('.png'):
            from PIL import Image
            with Image.open(tmp_path/item['file']) as image:
                image.verify()
    pd.testing.assert_frame_equal(pd.read_csv(tmp_path/'profiles.csv'),res['da'].case.profiles,check_dtype=False)
    metadata=json.loads((tmp_path/'profile_generation.json').read_text())
    assert metadata['explicit_override_columns'] is None  # direct fixture has no captured generator provenance
    assert metadata['metadata_capture'].startswith('case_configuration_only')
    for name in ('da_line_flows.csv','da_system.csv','da_lmp_matrix.csv','da_reserve.csv','rt_hydro.csv','rt_storage.csv','rt_lines.csv'):
        assert not pd.read_csv(tmp_path/name).empty
    units = pd.read_csv(tmp_path/'rt_units.csv')
    assert not units.duplicated(['period','unit']).any()
    assert len(units) == len(res['da'].tables['units'].unit.unique()) * 3
    assert units.loc[units.type == 'tie','P_mw'].tolist() == [10.,10.,10.]
    assert units.loc[units.type == 'tie','r_up'].tolist() == [0.,0.,0.]
    np.testing.assert_allclose(units.energy_mwh, units.P_mw*.25, atol=4e-5)
    prices = pd.read_csv(tmp_path/'rt_prices.csv')
    assert {'dispatch_raw_lmp','lmp_raw','lmp','served_load_mw'} <= set(prices)
    np.testing.assert_allclose(prices.served_load_mw, prices.load_mw-prices.shed_mw, atol=1e-4)
    system = pd.read_csv(tmp_path/'rt_system.csv')
    net_supply = units.groupby('period').P_mw.sum().to_numpy()
    np.testing.assert_allclose(net_supply, system.load_mw-system.shed_mw+system.surplus_mw, atol=2e-4)
    hydro = pd.read_csv(tmp_path/'rt_hydro.csv')
    np.testing.assert_allclose(hydro.Q_gen+hydro.Q_spill, hydro.qout_m3s, atol=1e-4)
    np.testing.assert_allclose(hydro.V_1e4m3-hydro.V_start, (hydro.Q_in-hydro.qout_m3s)*.36*.25, atol=2e-4)
    storage = pd.read_csv(tmp_path/'rt_storage.csv')
    spec = res['da'].case.cfg.units['storage'][0]
    np.testing.assert_allclose(storage.soc, storage.E_mwh/spec['ecap'], atol=5.1e-5)
    np.testing.assert_allclose(storage.E_start.iloc[1:].values, storage.E_mwh.iloc[:-1].values, atol=1e-4)
    lines = pd.read_csv(tmp_path/'rt_lines.csv')
    np.testing.assert_allclose(lines.loading, abs(lines.flow_mw)/lines.rate_mw, atol=5.1e-5)
    assert {'slack_mw','dispatch_at_capacity','pricing_dual_active','flow_source','dual_source'} <= set(lines)
    settlement = pd.read_csv(tmp_path/'settlement_detail.csv')
    np.testing.assert_allclose(settlement.total, settlement.da_amount+settlement.rt_amount, atol=2e-4)
    assert settlement.loc[settlement.entity=='IMPORT','dq'].eq(0).all()
    assert len(settlement[settlement.entity=='IMPORT']) == 3
    for filename in ('rt_reserve.csv','rt_reserve_system.csv'):
        table = pd.read_csv(tmp_path/filename)
        assert table.relative_period.eq(0).all()
        assert table.period.max() == 2
    # Future diagnostics are retained but do not multiply executed rows or cash.
    evidence = json.loads((tmp_path/'reserve_window_evidence.json').read_text())
    assert any(x['relative_period'] > 0 for w in evidence['rt_windows'] for x in w['resources'])
    assert (tmp_path/'结果文件索引.md').exists()


def test_da_only_saves_reserve_stage_and_auxiliary_evidence(exported_result, tmp_path):
    result = exported_result['da']
    summary = pipeline.write_outputs(result, tmp_path, make_plots=True)
    assert summary['completed_scope'] == 'da_only'
    assert summary['settlement_scope'] == 'not_applicable'
    assert not summary['settled']
    manifest = json.loads((tmp_path/'output_manifest.json').read_text())
    assert all(item['present'] for item in manifest['artifacts'])
    assert not list(tmp_path.glob('settlement_*.csv'))
    assert not list(tmp_path.glob('operating_profit_*.csv'))
    assert not pd.read_csv(tmp_path/'da_reserve.csv').empty
    stages = json.loads((tmp_path/'stage_diagnostics.json').read_text())
    assert 'energy_sced' in stages['da'] and 'auxiliary_pricing' in stages['da']
    assert not stages['rt_windows']


@pytest.mark.parametrize('corruption',['duplicate','reorder','missing_state','wrong_state'])
def test_rt_rejects_overlap_reordering_or_missing_actual_state(exported_result, corruption):
    rr = deepcopy(exported_result['rr'])
    if corruption == 'duplicate': rr.rt[1]['t'] = 0
    elif corruption == 'reorder': rr.rt[0],rr.rt[1] = rr.rt[1],rr.rt[0]
    elif corruption == 'missing_state': rr.states = rr.states[:1]
    else: rr.states[1].t = 0
    with pytest.raises(ValueError, match='RT output requires'):
        rolling.rt_tables(exported_result['da'], rr)


def test_no_storage_or_hydro_is_readable_header_only(tmp_path):
    from test_da_stages import tiny_case
    result = run_da(tiny_case(reserve=0), verbose=False)
    pipeline.write_outputs(result,tmp_path,make_plots=True)
    for name in ('storage','hydro','pumped'):
        table = pd.read_csv(tmp_path/f'da_{name}.csv')
        assert table.empty and 'period' in table
    assert not (tmp_path/'storage_soc.png').exists()


def test_static_stack_keeps_pumped_generation_and_pumping(tmp_path, monkeypatch):
    from smm import plots
    import matplotlib.axes
    labels = []
    original = matplotlib.axes.Axes.stackplot
    def record(ax,*args,**kwargs):
        labels.extend(kwargs.get('labels',[]))
        return original(ax,*args,**kwargs)
    monkeypatch.setattr(matplotlib.axes.Axes,'stackplot',record)
    units=pd.DataFrame({'period':[0,1,0,1], 'type':['pumped']*2+['coal']*2,
                        'P_mw':[10.,-5.,90.,105.], 'curtail_mw':[0.]*4})
    system=pd.DataFrame({'period':[0,1],'load_mw':[100.,100.], 'shed_mw':[0.,0.]})
    plots.generation_stack(units,system,tmp_path/'stack.png')
    assert '抽蓄(发电)' in labels


def test_no_plots_manifest_does_not_claim_stale_images(exported_result, tmp_path):
    (tmp_path/'storage_soc.png').write_bytes(b'previous-run-not-a-png')
    full.write_full_outputs(exported_result,tmp_path,make_plots=False)
    manifest=json.loads((tmp_path/'output_manifest.json').read_text())
    assert not any(x['file'].endswith('.png') for x in manifest['artifacts'])


def test_prepared_generation_metadata_is_not_reloaded_from_changed_inputs(exported_result, tmp_path, monkeypatch):
    from smm.output_catalog import write_profile_evidence
    case=deepcopy(exported_result['da'].case)
    case.profiles.attrs['generation_metadata']={'model':'multiscale_v2','seed':99,'explicit_override_columns':['W1_da']}
    def forbidden(*args,**kwargs):
        raise AssertionError('Do not read current input metadata after the solve')
    monkeypatch.setattr('smm.forecast_settings.generation_metadata',forbidden)
    write_profile_evidence(case,tmp_path)
    saved=json.loads((tmp_path/'profile_generation.json').read_text())
    assert saved['seed']==99 and saved['explicit_override_columns']==['W1_da']
    assert saved['metadata_capture']=='before_solve_with_prepared_profiles'
