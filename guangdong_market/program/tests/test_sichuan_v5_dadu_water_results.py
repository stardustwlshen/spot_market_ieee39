from copy import deepcopy
from pathlib import Path

import pytest


@pytest.fixture
def water(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'app'))
    import sichuan_v5_dadu_water_results
    return sichuan_v5_dadu_water_results


def _example():
    a = {'id':'a', 'regulation':'reservoir', 'initial_storage_m3':1000, 'storage_min_m3':0,
         'storage_max_m3':100000, 'terminal_min_m3':1400, 'terminal_max_m3':120000}
    b = {'id':'b', 'regulation':'run_of_river', 'initial_storage_m3':0}
    stations = [{'id':'a','name':'上库','model':'reservoir'}, {'id':'b','name':'下站','model':'run_of_river'}]
    reach = {'upstream':'a','downstream':'b','travel_quarters':1,'initial_arrivals_m3':[900]}
    rows = []
    for t in range(2):
        arrived = 900 if t == 0 else 1350
        hydro = {
            'a': {'storage_m3':1450 + t*450,'natural_inflow_m3s':2,'upstream_arrival_m3':0,
                  'turbine_m3s':1,'eco_bypass_m3s':.25,'water_spill_m3s':.25,'release_m3s':1.5},
            'b': {'storage_m3':0,'natural_inflow_m3s':.5,'upstream_arrival_m3':arrived,
                  'turbine_m3s':.5 + arrived/900,'eco_bypass_m3s':0,'water_spill_m3s':0,
                  'release_m3s':.5 + arrived/900},
        }
        queue = [{'upstream':'a','downstream':'b','next_arrivals_m3':[1350]}]
        rows.append({'quarter':t,'hydro':hydro,'water_handoff':{'in_transit':queue}})
    return {'inputs':{'stations':stations,'market_case':{'cascade':{'reservoirs':[a,b],'reaches':[reach]}}},
            'rt':rows, 'da':deepcopy(rows)}


def test_flow_components_storage_and_delayed_arrival(water):
    r = _example()
    r['da'][0]['hydro']['a']['storage_m3'] += 60
    ledger = water.station_water_ledger(r)
    assert len(ledger) == 4
    assert ledger[0]['storage_vs_da_m3'] == -60
    assert ledger[0]['non_turbine_m3s'] == .5
    assert ledger[0]['release_m3'] == 1350
    assert ledger[1]['upstream_arrival_m3s'] == 1
    assert ledger[2]['storage_begin_m3'] == 1450
    assert all(row['balance_residual_m3'] == 0 for row in ledger)
    reaches = water.reach_water_ledger(r)
    assert [row['arrival_recorded_m3'] for row in reaches] == [900,1350]
    assert [row['end_transit_m3'] for row in reaches] == [1350,1350]
    assert all(row['balance_residual_m3'] == row['arrival_residual_m3'] == 0 for row in reaches)
    r['rt'][1]['hydro']['b']['upstream_arrival_m3'] -= 4
    assert water.reach_water_ledger(r)[1]['arrival_residual_m3'] == -4
    assert water.station_water_ledger(r)[3]['balance_residual_m3'] == 4


def test_missing_legacy_fields_and_partial_daily_targets(water):
    r = _example()
    target, = water.reservoir_target_rows(r, 0)
    assert target['status'] == '尚未执行至日末'
    assert target['selected_storage_m3'] == 1450 and target['last_executed_storage_m3'] == 1900
    assert target['terminal_lower_margin_m3'] is None
    r['inputs'].pop('market_case')
    r['rt'][0]['hydro']['a'].pop('eco_bypass_m3s')
    old = water.station_water_ledger(r)
    assert old[0]['release_components_residual_m3s'] is None
    assert old[0]['non_turbine_m3s'] is None
    assert old[0]['balance_residual_m3'] is None and old[2]['balance_residual_m3'] == 0
    reach = water.reach_water_ledger(r)[0]
    assert reach['begin_transit_m3'] is None and reach['arrival_recorded_m3'] == 900
    assert reach['lag_quarters'] == 1 and reach['balance_residual_m3'] is None


def test_only_assess_daily_target_on_contiguous_full_day(water):
    r = _example()
    r['rt'] = [dict(r['rt'][-1], quarter=t) for t in range(96)]
    target, = water.reservoir_target_rows(r, 0)
    assert target['status'] == '满足已记录目标'
    assert target['terminal_lower_margin_m3'] == 500 and target['last_executed_end'] == '24:00'
    r['rt'][-1] = dict(r['rt'][-1], quarter=94)
    assert water.reservoir_target_rows(r, 0)[0]['status'] == '尚未执行至日末'
    assert water._clock(97) == 'D+1 00:15'
