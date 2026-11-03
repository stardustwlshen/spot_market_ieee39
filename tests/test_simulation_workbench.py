"""Launch freezes the visible bid and all-resource views retain formal states."""
import copy
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))


def item(elements, label):
    return next(x for x in elements if x.label == label)


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    from smm import scenario as sc
    monkeypatch.setattr(sc, 'SCEN_DIR', tmp_path / 'scenarios')
    monkeypatch.setattr(sc, 'RUNS_DIR', tmp_path / 'runs')
    sc.ensure_default()
    return sc


def test_launch_freezes_visible_manual_quote_and_nothing_else(scenario, monkeypatch):
    import common
    from smm.bids import load_bids
    cfg = common.scen_config('默认')
    original = load_bids(cfg)
    files = {p: p.read_bytes() for p in cfg.root.rglob('*') if p.is_file()}
    real_spawn = common.subprocess.Popen
    calls = []
    def fake_spawn(*args, **kwargs):
        if args and args[0][0] == 'git':
            return real_spawn(*args, **kwargs)
        calls.append((args, kwargs))
        return SimpleNamespace(pid=os.getpid())
    monkeypatch.setattr(common.subprocess, 'Popen', fake_spawn)
    at = AppTest.from_file(str(ROOT / 'app/pages/2_运行.py'), default_timeout=60).run()
    item(at.number_input, '空载报价（元/h）').set_value(6100.).run()
    item(at.button, '▶ 开始运行').click().run()
    assert not at.exception and len(calls) == 1
    run = Path(at.session_state['watch_run'])
    frozen = json.loads((run / 'submitted_bids.json').read_text())
    assert frozen['units']['C1']['noload_cost'] == 6100
    assert all(frozen['units'][uid] == original['units'][uid] for uid in original['units'] if uid != 'C1')
    assert all(path.read_bytes() == data for path, data in files.items())
    assert item(at.button, '▶ 开始运行').disabled
    assert item(at.number_input, '空载报价（元/h）').disabled
    key = item(at.number_input, '空载报价（元/h）').key.removeprefix('quote_edit:').removesuffix(':C1:noload')
    at.session_state[key]['units']['C1']['noload_cost'] = 6200
    assert json.loads((run / 'submitted_bids.json').read_text()) == frozen


def test_all_resource_results_join_complete_physical_states_without_mutation():
    from simulation_results import all_unit_parameters
    tables = {
        'units': [{'period': 0, 'unit': u, 'bus': 1, 'P_mw': value} for u, value in [('C1', 1), ('ES1', -2), ('H1', 3), ('PS1', -4)]],
        'storage': [{'period': 0, 'unit': 'ES1', 'E_mwh': 12, 'soc': .5, 'custom_storage_field': 7}],
        'hydro': [{'period': 0, 'station': 'H1', 'V_1e4m3': 23, 'spill_m3s': 1}],
        'pumped': [{'period': 0, 'unit': 'PS1', 'V_up': 34, 'P_pump': 4}],
        'prices': [{'period': 0, 'bus': 1, 'lmp': 50, 'dispatch_raw_lmp': 49}],
    }
    before = copy.deepcopy(tables)
    combined = all_unit_parameters(tables).set_index('unit')
    assert set(combined.index) == {'C1', 'ES1', 'H1', 'PS1'}
    assert combined.at['ES1', 'custom_storage_field'] == 7
    assert combined.at['ES1', 'E_mwh'] == 12
    assert combined.at['H1', 'V_1e4m3'] == 23
    assert combined.at['PS1', 'V_up'] == 34
    assert combined.at['PS1', 'P_mw'] == -4
    assert combined.lmp.tolist() == [50] * 4
    assert pd.isna(combined.at['C1', 'E_mwh'])
    assert tables == before


def test_saved_da_only_uses_da_files(tmp_path):
    from simulation_results import saved_da_tables
    (tmp_path / 'da').mkdir()
    pd.DataFrame([dict(period=0, unit='C1', P_mw=1)]).to_csv(tmp_path/'da'/'da_unit_awards.csv', index=False)
    pd.DataFrame([dict(period=0, unit='C1', P_mw=99)]).to_csv(tmp_path/'rt_units.csv', index=False)
    assert saved_da_tables(tmp_path)['units'].P_mw.iloc[0] == 1


def test_price_grid_edit_changes_only_selected_segment(scenario):
    from common import scen_config
    from smm.bids import load_bids
    original = load_bids(scen_config('默认'))
    at = AppTest.from_file(str(ROOT/'app/pages/2_运行.py'), default_timeout=60).run()
    key = item(at.number_input, '空载报价（元/h）').key.removeprefix('quote_edit:').removesuffix(':C1:noload')
    expected = copy.deepcopy(original)
    expected['units']['C1']['segments'][1]['price'] += 1
    editor = f'quote_edit:{key}:C1:segments'
    at.session_state[editor] = {'edited_rows': {1: {'报价（元/MWh）': expected['units']['C1']['segments'][1]['price']}},
                                'added_rows': [], 'deleted_rows': []}
    at.run()
    assert not at.exception
    assert at.session_state[key] == expected
    item(at.selectbox, '模拟机组').select('C2').run()
    item(at.selectbox, '模拟机组').select('C1').run()
    assert not at.exception
    assert at.session_state[key] == expected
