from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import sys
from types import ModuleType
import numpy as np
import pandas as pd
import pytest
from multiday_fixture import micro_day
from smm.series import DayInputs,run_series,validate_day,day_fingerprint,AwaitingBids
from smm.series_inputs import SeriesSource,public_schedule_profiles,consumer_contract,CONSUMER_CONTRACT
from smm.causal_types import ResourceSpec
from smm.causal_processes import ProcessPlan
from smm.causal_provider import compile_provider,save_bundle


def days_fixture():
    days=[]
    for date in ('2026-07-15','2026-07-16'):
        day,_=micro_day(date,with_hydro=False)
        day.cfg.base.update(periods=96,dt_h=.25,forecast={'model':'causal_physical_v3'})
        day.cfg.mode['realtime']['interval_min']=15
        day.profiles=pd.DataFrame({'period':np.arange(1,97)})
        day.information_policy='causal_physical_v3'
        days.append(day)
    return days


def make_source(tmp_path,*,future_change=False):
    plan=ProcessPlan('2026-07-15T00:00:00Z',2,'UTC',(ResourceSpec('load','load',60.),),root_seed='42',
        day_specs={'2026-07-16':{'load':'load_high'}} if future_change else {})
    actual,preview=compile_provider(plan)
    preview.select_day('2026-07-16T00:00:00Z',48,48,stage='RT',executed_through_period=48)
    save_bundle(tmp_path/'bundle',actual)
    return SeriesSource(tmp_path/'bundle'),preview


def test_bind_dates_and_freeze_one_fresh_source(tmp_path,monkeypatch):
    source,preview=make_source(tmp_path)
    assert preview.archive()['forecasts'] and not source.provider.archive()['forecasts']
    def no_generate(*a,**k):raise AssertionError('Must not redraw a frozen series')
    monkeypatch.setattr('smm.causal_processes.generate_actual',no_generate)
    days=source.bind(days_fixture())
    for day in days:validate_day(day)
    root=tmp_path/'run';root.mkdir()
    frozen=source.freeze(root)
    assert frozen.provider is not source.provider
    assert not frozen.provider.archive()['forecasts']
    frozen.provider.select_day(days[0].input_source_ref['day_summary']['day_start_utc'],0,0,stage='DA',executed_through_period=0)
    frozen.provider.select_day(days[1].input_source_ref['day_summary']['day_start_utc'],0,0,stage='DA',executed_through_period=0)
    assert len(frozen.provider.archive()['forecasts'])==2
    assert source.freeze(root).identity()==frozen.identity()
    assert not source.freeze(root).provider.archive()['forecasts']
    assert len(list((root/'inputs/causal_v3').iterdir()))==2


def test_actual_bundle_change_does_not_change_day_one_forecast(tmp_path):
    a,_=make_source(tmp_path/'a'); b,_=make_source(tmp_path/'b',future_change=True)
    da=a.bind(days_fixture());db=b.bind(days_fixture())
    assert a.identity()!=b.identity()
    assert day_fingerprint(da[0])!=day_fingerprint(db[0])  # run lineage is intentionally different
    x=a.provider.select_day('2026-07-15T00:00:00Z',0,0,stage='DA',executed_through_period=0)
    y=b.provider.select_day('2026-07-15T00:00:00Z',0,0,stage='DA',executed_through_period=0)
    assert x.forecast_id==y.forecast_id and x.content_hash==y.content_hash


def test_wrong_date_source_and_private_profiles_fail(tmp_path):
    source,_=make_source(tmp_path)
    days=source.bind(days_fixture())
    altered=deepcopy(days);altered[0].input_source_ref['bundle_content_hash']='forged'
    with pytest.raises(ValueError,match='reference differs'):source.bind(altered)
    altered=days_fixture();altered[1].cfg.base['date']='2026-07-17'
    with pytest.raises(ValueError,match='date range'):source.bind(altered)
    days[0].profiles['load_actual']=60.
    with pytest.raises(ValueError,match='future actual'):validate_day(days[0])
    days[0].profiles=public_schedule_profiles(days[0].cfg,days[0].profiles)
    validate_day(days[0])
    days[0].bid_receipt=None
    with pytest.raises(AwaitingBids):validate_day(days[0])


def test_missing_or_false_consumer_gate_before_output(tmp_path,monkeypatch):
    source,_=make_source(tmp_path)
    days=days_fixture()
    monkeypatch.setitem(sys.modules,'smm.causal_integration',None)
    with pytest.raises(ValueError,match='not ready'):
        run_series(days,tmp_path/'not_created',initial_actual_state=None,source_bundle=source.directory)
    assert not (tmp_path/'not_created').exists()
    fake=ModuleType('smm.causal_integration')
    fake.consumer_capabilities=lambda:dict(contract_version=CONSUMER_CONTRACT,ready=False,grid_minutes=15,natural_coverage='remaining_day_same_issue')
    monkeypatch.setitem(sys.modules,'smm.causal_integration',fake)
    with pytest.raises(ValueError,match='not ready'):consumer_contract()


def test_corrupt_bundle_and_existing_frozen_difference_fail(tmp_path):
    a,_=make_source(tmp_path/'a'); b,_=make_source(tmp_path/'b',future_change=True)
    root=tmp_path/'run';root.mkdir();a.freeze(root)
    with pytest.raises(ValueError,match='differs'):b.freeze(root)
    (a.directory/'actual.npz').write_bytes(b'corrupted')
    with pytest.raises(ValueError,match='changed after admission'):a.freeze(tmp_path/'other')


def test_deterministic_cannot_smuggle_causal_ref():
    day,_=micro_day();day.input_source_ref={'consumer_contract_version':CONSUMER_CONTRACT}
    with pytest.raises(ValueError,match='cannot carry'):validate_day(day)


def test_missing_day_bids_blocks_before_any_DA(tmp_path,monkeypatch):
    from smm.state_bridge import research_initial_state
    source,_=make_source(tmp_path)
    fake=ModuleType('smm.causal_integration')
    fake.consumer_capabilities=lambda:dict(contract_version=CONSUMER_CONTRACT,ready=True,grid_minutes=15,natural_coverage='remaining_day_same_issue')
    monkeypatch.setitem(sys.modules,'smm.causal_integration',fake)
    days=days_fixture();days[1].bid_receipt=None
    initial=research_initial_state(days[0].cfg,acknowledge_constant_prehistory=True)
    def no_DA(*a,**kw):raise AssertionError('DA must not begin before all daily bids are ready')
    monkeypatch.setattr('smm.case.build_da_case',no_DA)
    with pytest.raises(AwaitingBids):
        run_series(days,tmp_path/'no_DA',initial_actual_state=initial,source_bundle=source.directory)
    assert not (tmp_path/'no_DA').exists()
