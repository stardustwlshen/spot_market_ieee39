"""Archive loss/recovery and honest execution scope, without changing dispatch."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import shutil
import sys

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from result_navigation import validate_display_result


def saved_prefix():
    da_prices = pd.DataFrame([dict(period=t, time=f'00:{15*t:02d}', bus=b, lmp=30., lmp_raw=30., energy=30., congestion=0.)
                              for t in range(4) for b in (1, 2)])
    da_units = pd.DataFrame([dict(period=t, unit=u, type='coal', P_mw=10., energy_mwh=2.5, revenue_yuan=75.)
                            for t in range(4) for u in ('G1', 'G2')])
    return dict(full=True, summary=dict(completed_scope='debug_prefix', expected_periods=4, executed_periods=2),
                da_summary=dict(completed_scope='da_only', expected_periods=4, executed_periods=0),
                da_nodal_prices=da_prices, da_unit_awards=da_units,
                rt_prices=da_prices[da_prices.period < 2].copy(), rt_units=da_units[da_units.period < 2].copy(),
                rt_system=pd.DataFrame(dict(period=[0, 1], load_mw=[20., 20.])),
                settlement_by_type=pd.DataFrame([dict(type='coal', q_rt=10., total=300., da_amount=300., rt_amount=0., dev_pnl=0.)]),
                settlement_by_entity=pd.DataFrame([dict(entity='G1', type='coal')]),
                settlement_detail=pd.DataFrame([dict(entity='G1')]),
                settlement_reconciliation=pd.DataFrame([dict(error=0.)]),
                settlement_line_rent=pd.DataFrame(columns=['line', 'rent_da', 'rent_rt']))


def test_valid_prefix_and_da_only_not_confused_with_missing_full_day():
    result = saved_prefix()
    validate_display_result(result)
    result['full'] = False
    result['summary'] = deepcopy(result['da_summary'])
    result['rt_prices'] = None
    validate_display_result(result)


@pytest.mark.parametrize('damage', ['middle_period', 'last_period', 'one_node_row', 'whole_rt_node',
                                  'unit_row', 'system_duplicate', 'cross_table_period', 'null_unit'])
def test_missing_or_conflicting_record_is_not_a_complete_result(damage):
    result = saved_prefix()
    if damage in ('middle_period', 'last_period'):
        t = 0 if damage == 'middle_period' else 1
        result['rt_prices'] = result['rt_prices'].query('period != @t').copy()
    elif damage == 'one_node_row':
        result['rt_prices'] = result['rt_prices'].iloc[1:].copy()
    elif damage == 'whole_rt_node':
        result['rt_prices'] = result['rt_prices'].query('bus != 2').copy()
    elif damage == 'unit_row':
        result['rt_units'] = result['rt_units'].iloc[1:].copy()
    elif damage == 'system_duplicate':
        result['rt_system'] = pd.concat([result['rt_system'], result['rt_system'].iloc[:1]])
    elif damage == 'cross_table_period':
        result['rt_system'].loc[1, 'period'] = 2
    else:
        result['rt_units'].loc[0, 'unit'] = None
    with pytest.raises(ValueError):
        validate_display_result(result)


@pytest.mark.parametrize('bad_count', [0, 97, 2.5, float('nan'), float('inf'), 'unknown'])
def test_invalid_saved_execution_count_fails_clearly(bad_count):
    result = saved_prefix()
    result['summary']['executed_periods'] = bad_count
    with pytest.raises(ValueError, match='摘要时段数量无效'):
        validate_display_result(result)


@pytest.mark.parametrize('dtype', [float, str])
def test_valid_integral_periods_and_numeric_quantities_are_normalized(dtype):
    result = saved_prefix()
    for key in ('da_nodal_prices', 'da_unit_awards', 'rt_prices', 'rt_units', 'rt_system'):
        result[key]['period'] = result[key].period.astype(dtype)
    result['rt_units']['P_mw'] = result['rt_units'].P_mw.astype(str)
    validate_display_result(result)
    assert pd.api.types.is_integer_dtype(result['rt_prices'].period)
    assert pd.api.types.is_numeric_dtype(result['rt_units'].P_mw)


def test_result_cache_tracks_csv_repair_removal_and_nested_da_change(tmp_path):
    import common
    (tmp_path / 'summary.json').write_text('{}')
    path = tmp_path / 'rt_prices.csv'
    path.write_text('period,bus,lmp\n0,1,30\n')
    meta = dict(dir=str(tmp_path), mode='full')
    assert common.get_result(meta)['rt_prices'].lmp.iloc[0] == 30
    path.write_text('period,bus,lmp\n0,1,42\n')
    assert common.get_result(meta)['rt_prices'].lmp.iloc[0] == 42
    path.unlink()
    assert common.get_result(meta)['rt_prices'] is None
    assert common.get_result(meta)['full']
    da = tmp_path / 'da'; da.mkdir()
    (da / 'da_nodal_prices.csv').write_text('period,bus,lmp\n0,1,55\n')
    assert common.get_result(meta)['da_nodal_prices'].lmp.iloc[0] == 55
    (da / 'da_nodal_prices.csv').write_text('period,bus,lmp\n0,1,66\n')
    assert common.get_result(meta)['da_nodal_prices'].lmp.iloc[0] == 66


@pytest.fixture
def real_archive():
    evidence = os.environ.get('IEEE39_OPERATOR_EVIDENCE')
    if not evidence:
        pytest.skip('set IEEE39_OPERATOR_EVIDENCE to existing read-only 96-period archives')
    path = Path(evidence) / 'final_c1f829a_baseline_full96'
    assert (path / 'summary.json').is_file()
    return path


@pytest.mark.parametrize('damage', ['missing_period', 'missing_node', 'float_period', 'missing_rt_prices', 'bad_spread', 'bad_frozen_buses',
                                  'missing_rt_resource', 'missing_da_resource', 'missing_storage_resource'])
def test_real_archive_boundary_and_switch_recovery(real_archive, monkeypatch, tmp_path, damage):
    import common
    from smm import scenario
    original = common.get_result(dict(dir=str(real_archive), mode='full'))
    result = deepcopy(original)
    broken = tmp_path / damage; broken.mkdir()
    if damage == 'missing_period':
        result['rt_prices'] = result['rt_prices'].query('period != 40').copy()
    elif damage == 'missing_node':
        result['rt_prices'] = result['rt_prices'].query('bus != 16').copy()
    elif damage == 'float_period':
        for key in ('da_nodal_prices', 'da_unit_awards', 'rt_prices', 'rt_units', 'rt_system'):
            result[key]['period'] = result[key].period.astype(float)
    elif damage in ('missing_rt_resource', 'missing_da_resource', 'missing_storage_resource'):
        key, unit = {'missing_rt_resource': ('rt_units', 'PV1'),
                     'missing_da_resource': ('da_unit_awards', 'PV1'),
                     'missing_storage_resource': ('rt_storage', 'ES1')}[damage]
        result[key] = result[key][result[key].unit != unit].copy()
    elif damage == 'bad_spread':
        result['summary']['spread'] = ['not an object']
    elif damage == 'bad_frozen_buses':
        case = broken / 'scenario_snapshot/data/case39'
        case.mkdir(parents=True)
        (case / 'buses.csv').write_text('bus,load_mw\n1,"unclosed')
        result['snapshot_root'] = str(broken / 'scenario_snapshot')
    else:
        for file in real_archive.glob('*'):
            if file.is_file() and file.suffix in ('.csv', '.json') and file.name != 'rt_prices.csv':
                shutil.copy2(file, broken / file.name)
        result = common.get_result(dict(dir=str(broken), mode='full'))
    files = [real_archive / name for name in ('summary.json', 'rt_prices.csv', 'rt_units.csv')]
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    monkeypatch.setattr(scenario, 'SCEN_DIR', tmp_path / 'scenarios')
    monkeypatch.setattr(scenario, 'RUNS_DIR', tmp_path / 'runs')
    scenario.ensure_default()
    records = [dict(dir=str(p), scenario=p.name, mode='full', status='done') for p in (broken, real_archive)]
    monkeypatch.setattr(common, 'all_results', lambda: records)
    monkeypatch.setattr(common, 'get_result', lambda m: deepcopy(result if m['dir'] == str(broken) else original))
    at = AppTest.from_file(str(ROOT / 'app/主页.py'), default_timeout=90).run()
    at.switch_page('pages/3_结果.py').run()
    assert not at.exception, [x.message for x in at.exception]
    if damage == 'float_period':
        assert next(x.value for x in at.metric if x.label == '实时执行范围') == '96 / 96 段'
    else:
        assert any('本次结果缺失或损坏' in x.value for x in at.error)
        assert not any(x.label == '实时执行范围' for x in at.metric)
    at.selectbox(key='_result_sel_widget').set_value(str(real_archive)).run()
    assert not at.exception
    assert not any('本次结果缺失或损坏' in x.value for x in at.error)
    assert before == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def test_legacy_periods_count_still_rejects_lost_last_interval():
    result = saved_prefix()
    result['summary'].pop('executed_periods')
    result['summary']['periods'] = 2
    result['rt_prices'] = result['rt_prices'].query('period == 0').copy()
    with pytest.raises(ValueError, match='摘要声明的 2 段不一致'):
        validate_display_result(result)


def test_frozen_nodes_reject_whole_node_loss_even_without_scope_metadata(tmp_path):
    result = saved_prefix()
    result['da_summary'] = {}; result['summary'] = {}
    case = tmp_path / 'data/case39'; case.mkdir(parents=True)
    (case / 'buses.csv').write_text('bus,load_mw\n1,10\n2,10\n')
    result['snapshot_root'] = str(tmp_path)
    for key in ('da_nodal_prices', 'rt_prices'):
        result[key] = result[key].query('bus == 1').copy()
    with pytest.raises(ValueError, match='冻结网络的节点集合不一致'):
        validate_display_result(result)


def test_empty_line_rent_header_is_valid_but_absent_or_missing_header_is_not():
    result = saved_prefix()
    validate_display_result(result)
    result['settlement_line_rent'] = None
    with pytest.raises(ValueError, match='线路结算文件缺失或为空'):
        validate_display_result(result)
    result['settlement_line_rent'] = pd.DataFrame()
    with pytest.raises(ValueError, match='线路结算缺少必要字段'):
        validate_display_result(result)


@pytest.mark.parametrize('field', ['spread', 'realtime', 'forecast_error', 'settlement_by_type', 'pumped'])
def test_wrong_nested_summary_shape_is_business_error(field):
    result = saved_prefix()
    result['summary'][field] = ['bad shape']
    with pytest.raises(ValueError, match='应为对象记录'):
        validate_display_result(result)


def test_cancelled_failed_and_pending_runs_are_not_completed_results(tmp_path, monkeypatch):
    import common
    records = [dict(dir=str(tmp_path / status), status=status, mode='full')
               for status in ('done', 'failed', 'stopped', 'running', 'cancelling', 'starting')]
    for row in records:
        path = Path(row['dir']); path.mkdir()
        (path / 'summary.json').write_text('{}')
    monkeypatch.setattr(common, 'ROOT', tmp_path)
    monkeypatch.setattr(common.sc, 'list_runs', lambda: records)
    assert common.all_results() == [records[0]]


def test_repaired_frozen_ratings_and_resource_labels_are_not_stale(tmp_path):
    import common
    case = tmp_path / 'data/case39'; case.mkdir(parents=True)
    config = tmp_path / 'config'; config.mkdir()
    (case / 'branches.csv').write_text('branch_id,rate_mw\nL1,100\n')
    (config / 'base.yaml').write_text('line_capacity_multiplier: 1\n')
    (config / 'units.yaml').write_text('thermal:\n  - {id: G1, bus: 1, type: coal}\n')
    assert common.branches_with_rate(str(tmp_path)).rate_eff.iloc[0] == 100
    assert common.bus_resources(str(tmp_path)) == {1: [('G1', 'coal')]}
    (config / 'base.yaml').write_text('line_capacity_multiplier: 2\n')
    (config / 'units.yaml').write_text('thermal:\n  - {id: G2, bus: 2, type: gas_gt}\n')
    assert common.branches_with_rate(str(tmp_path)).rate_eff.iloc[0] == 200
    assert common.bus_resources(str(tmp_path)) == {2: [('G2', 'gas_gt')]}


def test_missing_whole_rt_resource_rejected_without_any_frozen_catalog():
    result = saved_prefix()
    result['rt_units'] = result['rt_units'].query("unit != 'G2'").copy()
    with pytest.raises(ValueError, match='实时缺少：G2'):
        validate_display_result(result)


def test_old_rt_without_exogenous_tie_remains_allowed_not_zero_filled():
    result = saved_prefix()
    tie = result['da_unit_awards'].query("unit == 'G1'").copy()
    tie['unit'] = 'IMPORT'; tie['type'] = 'tie'
    result['da_unit_awards'] = pd.concat([result['da_unit_awards'], tie], ignore_index=True)
    validate_display_result(result)
    assert 'IMPORT' not in set(result['rt_units'].unit)
    assert len(result['rt_units']) == 4


@pytest.mark.parametrize('tie_mode', ['fixed_load', None])
def test_disabled_units_and_non_supply_tie_are_not_required(tmp_path, tie_mode):
    result = saved_prefix()
    config = tmp_path / 'config'; config.mkdir()
    (config / 'units.yaml').write_text('thermal:\n  - {id: G1, type: coal}\n  - {id: G2, type: coal}\n'
                                      'hydro:\n  - {id: CLOSED, enabled: false}\n'
                                      'tie:\n  - {id: IMPORT}\n')
    if tie_mode:
        (config / 'base.yaml').write_text('tie_line_mode: fixed_load\n')
    result['snapshot_root'] = str(tmp_path)
    validate_display_result(result)
    assert set(result['da_unit_awards'].unit) == {'G1', 'G2'}


def test_frozen_enabled_resource_loss_rejected_in_da_only(tmp_path):
    result = saved_prefix()
    result['full'] = False
    result['da_unit_awards'] = result['da_unit_awards'].query("unit != 'G2'").copy()
    config = tmp_path / 'config'; config.mkdir()
    (config / 'units.yaml').write_text('thermal:\n  - {id: G1, type: coal}\n  - {id: G2, type: coal}\n')
    result['snapshot_root'] = str(tmp_path)
    with pytest.raises(ValueError, match='冻结启用主体：G2'):
        validate_display_result(result)


def test_missing_optional_legacy_hydro_pumped_states_are_not_zero_filled():
    result = saved_prefix()
    result['da_unit_awards'].loc[result['da_unit_awards'].unit == 'G1', 'type'] = 'hydro'
    result['rt_units'].loc[result['rt_units'].unit == 'G1', 'type'] = 'hydro'
    result['da_hydro'] = result['rt_hydro'] = None
    validate_display_result(result)
    assert result['rt_hydro'] is None
