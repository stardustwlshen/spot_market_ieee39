"""Independent rounded-CSV evidence gates, separate from full-precision model audit."""
from types import SimpleNamespace as NS
import pandas as pd
import pytest
from test_validate_runs_evidence import validator

@pytest.fixture
def csv_evidence(tmp_path):
    cfg=NS(T=4,dt=.25,units={'hydro':[{'id':'H1','e_day_min':40.}]},mode={'intraday':{'enabled':True}})
    for stage in ('da','rt'):
        pd.DataFrame([dict(period=t,station='H1',P_mw=40.) for t in range(4)]).to_csv(tmp_path/f'{stage}_hydro.csv',index=False)
    pd.DataFrame([dict(tau=t) for t in (0,2)]).to_csv(tmp_path/'id_rounds_summary.csv',index=False)
    pd.DataFrame([dict(tau=t,period=k,unit='H1',type='hydro',P_mw=40.) for t in (0,2) for k in range(t,4)]).to_csv(tmp_path/'id_plans.csv',index=False)
    return tmp_path,cfg


def test_minimum_is_recomputed_from_power_not_claimed_green(csv_evidence):
    d,cfg=csv_evidence
    r=validator._csv_daily_minimum_audit(d,cfg)
    assert r['records'][0]['executed_mwh']==40.
    assert r['records'][0]['da_mwh']==40.


@pytest.mark.parametrize('stage',['da','rt'])
@pytest.mark.parametrize('damage',['short','missing','duplicate','nan','wrong_station'])
def test_corrupt_or_insufficient_daily_power_fails(csv_evidence,stage,damage):
    d,cfg=csv_evidence;p=d/f'{stage}_hydro.csv';a=pd.read_csv(p)
    if damage=='short':a.P_mw=39.
    elif damage=='missing':a=a.iloc[:-1]
    elif damage=='duplicate':a=pd.concat([a,a.iloc[:1]])
    elif damage=='nan':a.loc[0,'P_mw']=float('nan')
    else:a.station='OTHER'
    a.to_csv(p,index=False)
    with pytest.raises(ValueError):validator._csv_daily_minimum_audit(d,cfg)


@pytest.mark.parametrize('damage',['short','missing','duplicate','nan','wrong_tau','extra_tau'])
def test_id_requires_actual_before_plus_remaining_plan(csv_evidence,damage):
    d,cfg=csv_evidence;p=d/'id_plans.csv';a=pd.read_csv(p)
    if damage=='short':a.loc[a.tau==2,'P_mw']=39.
    elif damage=='missing':a=a.iloc[:-1]
    elif damage=='duplicate':a=pd.concat([a,a.iloc[:1]])
    elif damage=='nan':a.loc[0,'P_mw']=float('inf')
    elif damage=='extra_tau':a=pd.concat([a,a.iloc[:1].assign(tau=1)])
    else:a.loc[a.tau==2,'tau']=3
    a.to_csv(p,index=False)
    with pytest.raises(ValueError):validator._csv_daily_minimum_audit(d,cfg)


def test_null_configuration_does_not_require_new_evidence(csv_evidence):
    d,cfg=csv_evidence;cfg.units['hydro'][0]['e_day_min']=None
    for p in d.iterdir():p.unlink()
    assert not validator._csv_daily_minimum_audit(d,cfg)['active']


def test_known_csv_rounding_bound_is_only_dt_times_number_of_rows(csv_evidence):
    d,cfg=csv_evidence;p=d/'rt_hydro.csv';a=pd.read_csv(p);a.P_mw-=.00001;a.to_csv(p,index=False)
    r=validator._csv_daily_minimum_audit(d,cfg)
    assert r['csv_energy_rounding_tolerance_mwh']==pytest.approx(.25*4*5.1e-5+1e-6)
    a.P_mw-=.001;a.to_csv(p,index=False)
    with pytest.raises(ValueError):validator._csv_daily_minimum_audit(d,cfg)


@pytest.mark.parametrize('location',['resolved_bids.json','scenario_snapshot/resolved_bids.json'])
def test_only_run_effective_bid_snapshots_are_trusted(tmp_path,location):
    import json
    p=tmp_path/location;p.parent.mkdir(parents=True,exist_ok=True)
    data={'version':1,'units':{'H1':{'segments':[{'p_start':0.,'p_end':100.,'price':5.}]}}}
    p.write_text(json.dumps(data))
    assert validator._frozen_resolved_bids(tmp_path)==data


def test_effective_bid_sources_cannot_conflict(tmp_path):
    import json
    (tmp_path/'scenario_snapshot').mkdir()
    (tmp_path/'resolved_bids.json').write_text(json.dumps({'units':{},'version':1}))
    (tmp_path/'scenario_snapshot/resolved_bids.json').write_text(json.dumps({'units':{},'version':2}))
    with pytest.raises(ValueError,match='冲突'):validator._frozen_resolved_bids(tmp_path)


@pytest.mark.parametrize('payload',[None,[],{'units':None}])
def test_invalid_effective_bid_evidence_is_not_filled_from_defaults(tmp_path,payload):
    import json
    (tmp_path/'resolved_bids.json').write_text(json.dumps(payload))
    with pytest.raises(ValueError):validator._frozen_resolved_bids(tmp_path)


def test_no_fallback_to_submission_or_ambient_bid_library(tmp_path):
    (tmp_path/'submitted_bids.json').write_text('{"units":{},"version":1}')
    with pytest.raises(ValueError,match='缺少'):validator._frozen_resolved_bids(tmp_path)
