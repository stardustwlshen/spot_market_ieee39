"""Read-only hydro minimum presentation and run-record compatibility regressions."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from hydro_daily_energy_views import actual_daily_minimum, planned_daily_minimum


def summary(*, full=True, achieved=True):
    actual = 45. if achieved else 15.
    return dict(completed_scope='full_day' if full else 'debug_prefix', expected_periods=96,
                executed_periods=96 if full else 4,
                hydro_daily_minimum=dict(active=True, evidence_valid=True, full_day=full,
                    final_satisfied=achieved if full else None, errors=[], scope='actual_executed_first_periods_only',
                    records=[dict(unit='H1', minimum_mwh=40., executed_mwh=actual, remaining_mwh=max(0., 40.-actual),
                                  full_day=full, final_satisfied=achieved if full else None)]))


def render(data, directory):
    result = dict(summary=data, dir=str(directory))
    code = f'''import sys
sys.path.insert(0, {str(ROOT/'app')!r})
from hydro_daily_energy_views import render_hydro_daily_energy
render_hydro_daily_energy({result!r})'''
    return AppTest.from_string(code, default_timeout=30).run()


def test_complete_explicit_consistent_evidence_can_pass(tmp_path):
    data = summary()
    status, table, errors = actual_daily_minimum(data)
    assert status == 'passed' and not errors and table['实际已发 MWh'].tolist() == [45.]
    at = render(data, tmp_path)
    assert not at.exception and at.success and not at.error


@pytest.mark.parametrize('achieved', [True, False])
def test_prefix_never_green_even_when_minimum_already_reached(tmp_path, achieved):
    data = summary(full=False, achieved=achieved)
    status, table, errors = actual_daily_minimum(data)
    assert status == 'prefix' and not errors
    at = render(data, tmp_path)
    assert not at.exception and not at.success and not at.error
    assert any('未完成验收' in item.value for item in at.info)
    assert table['距下限尚缺 MWh'].iloc[0] == (0. if achieved else 25.)


@pytest.mark.parametrize('damage', ['missing_evidence_valid', 'invalid_evidence', 'final_null', 'full_null', 'scope_prefix',
                                  'short_count', 'row_prefix', 'row_final_null', 'nan_actual', 'duplicate', 'false_remaining',
                                  'claimed_pass_but_short', 'audit_errors', 'bad_top_level'])
def test_missing_or_conflicting_evidence_never_green(damage):
    data = summary(); audit = data['hydro_daily_minimum']; row = audit['records'][0]
    if damage == 'missing_evidence_valid':audit.pop('evidence_valid')
    elif damage == 'invalid_evidence':audit['evidence_valid'] = False
    elif damage == 'final_null':audit['final_satisfied'] = None
    elif damage == 'full_null':audit['full_day'] = None
    elif damage == 'scope_prefix':data['completed_scope'] = 'debug_prefix'
    elif damage == 'short_count':data['executed_periods'] = 4
    elif damage == 'row_prefix':row['full_day'] = False
    elif damage == 'row_final_null':row['final_satisfied'] = None
    elif damage == 'nan_actual':row['executed_mwh'] = float('nan')
    elif damage == 'duplicate':audit['records'].append(deepcopy(row))
    elif damage == 'false_remaining':row['remaining_mwh'] = 99.
    elif damage == 'claimed_pass_but_short':row.update(executed_mwh=15., remaining_mwh=25.)
    elif damage == 'audit_errors':audit['errors'] = ['missing executed period']
    else:data['hydro_daily_minimum'] = []
    assert actual_daily_minimum(data)[0] != 'passed'


def test_full_day_shortfall_is_red_not_prefix(tmp_path):
    at = render(summary(achieved=False), tmp_path)
    assert not at.exception and at.error and not at.success


@pytest.mark.parametrize('data, expected', [({}, 'missing'), ({'hydro_daily_minimum': {'active': False}}, 'inactive')])
def test_historical_missing_and_explicit_disabled_never_claim_pass(tmp_path, data, expected):
    assert actual_daily_minimum(data)[0] == expected
    at = render(data, tmp_path)
    assert not at.exception and not at.success and not at.dataframe
    assert any('不能推断' in x.value or '不表示已完成' in x.value for x in at.caption)


def test_checks_only_explicit_old_location_can_be_read():
    data = summary();audit=data.pop('hydro_daily_minimum')
    data['checks'] = {'hydro_daily_minimum_' + key: value for key, value in audit.items()}
    assert actual_daily_minimum(data)[0] == 'passed'
    data['hydro_daily_minimum'] = []
    assert actual_daily_minimum(data)[0] == 'unverified'  # Do not hide a malformed primary section.


def daily(stage, start, before, window, witness):
    return dict(hydro_daily_energy=dict(active=True, stage=stage, start_period=start, end_period_exclusive=start+8,
        rows=[dict(unit='H1', daily_minimum_mwh=40., executed_before_mwh=before,
                   remaining_minimum_mwh=max(0.,40.-before), window_generation_mwh=window,
                   witness_generation_mwh=witness, minimum_margin_mwh=window+witness-max(0.,40.-before))],
        suffix={'quantities_execute':False, 'quantities_settle':False, 'reference_metadata':{'H1':'day_ahead'}}))


def test_forecast_rows_not_summed_or_counted_as_actual(tmp_path):
    evidence = dict(da=daily('DA',0,0.,40.,0.),
                    id_rounds=[dict(tau=4,snapshot=daily('ID',4,15.,25.,0.))],
                    rt_windows=[dict(t=0,snapshot=daily('RT',0,0.,10.,35.)),dict(t=1,snapshot=daily('RT',1,2.,10.,35.))])
    table = planned_daily_minimum(evidence)
    assert len(table) == 4
    assert table['后缀见证发电 MWh（不执行）'].tolist() == [0.,0.,35.,35.]
    path = tmp_path/'hydro_window_evidence.json'; path.write_text(json.dumps(evidence));before=path.read_bytes()
    at = render(summary(full=False, achieved=False),tmp_path)
    assert not at.exception and not at.success
    assert at.dataframe[0].value['实际已发 MWh'].iloc[0] == 15.
    assert len(at.dataframe[1].value) == 4
    assert any('不跨窗口累加' in x.value and '不证明未来网络' in x.value for x in at.caption)
    assert path.read_bytes() == before


def test_missing_forecast_numbers_stay_unknown():
    record = daily('RT',0,0.,10.,35.);record['hydro_daily_energy']['rows'][0].pop('witness_generation_mwh')
    table = planned_daily_minimum({'rt_windows':[dict(snapshot=record)]})
    assert pd.isna(table['后缀见证发电 MWh（不执行）'].iloc[0])


def test_corrupt_forecast_does_not_hide_actual_or_rewrite_file(tmp_path):
    path=tmp_path/'hydro_window_evidence.json';path.write_text('{')
    at=render(summary(),tmp_path)
    assert not at.exception and at.success and at.warning and at.dataframe
    assert path.read_text() == '{'


def record(root, status='done', periods=100):
    directory=root/'默认'/'record';directory.mkdir(parents=True)
    data=dict(scenario='默认',mode='full',status=status,periods=periods,started='2026-11-05T00:00:00')
    path=directory/'meta.json';path.write_text(json.dumps(data))
    return path


@pytest.mark.parametrize('periods', [1,96,100,10000,None])
def test_positive_requested_periods_above_day_are_compatible(tmp_path,monkeypatch,periods):
    from smm import scenario as sc
    monkeypatch.setattr(sc,'RUNS_DIR',tmp_path)
    path=record(tmp_path,periods=periods);before=path.read_bytes()
    row=sc.list_runs()[0]
    assert row['status']=='done' and row['periods']==periods
    assert path.read_bytes()==before


@pytest.mark.parametrize('periods', [-1,0,1.5,True,'100'])
def test_invalid_requested_periods_stay_quarantined(tmp_path,monkeypatch,periods):
    from smm import scenario as sc
    monkeypatch.setattr(sc,'RUNS_DIR',tmp_path)
    path=record(tmp_path,periods=periods);before=path.read_bytes()
    assert sc.list_runs()[0]['status']=='invalid'
    assert path.read_bytes()==before


@pytest.mark.parametrize('status', ['starting','running','cancelling','done','failed','stopped',
                                  'launch_failed','cancelled_before_start','process_succeeded','process_failed'])
def test_actual_and_legacy_terminal_statuses_remain_compatible(tmp_path,monkeypatch,status):
    from smm import scenario as sc
    monkeypatch.setattr(sc,'RUNS_DIR',tmp_path)
    record(tmp_path,status=status)
    assert sc.list_runs()[0]['status']==status


def test_unknown_status_is_quarantined_and_blocks_same_scene_spawn(tmp_path,monkeypatch):
    from smm import scenario as sc
    import common
    monkeypatch.setattr(sc,'RUNS_DIR',tmp_path/'runs');monkeypatch.setattr(sc,'SCEN_DIR',tmp_path/'scenes')
    sc.ensure_default();path=record(sc.RUNS_DIR,status='garbled');before=path.read_bytes()
    assert sc.list_runs()[0]['status']=='invalid'
    monkeypatch.setattr(common.subprocess,'Popen',lambda *args,**kwargs:pytest.fail('unknown status must block spawning'))
    with pytest.raises(Exception,match='元数据损坏'):
        common.start_run(sc.DEFAULT,'full',1,None)
    assert path.read_bytes()==before
