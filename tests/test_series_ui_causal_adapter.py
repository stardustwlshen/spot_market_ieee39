"""Real provider/consumer UI admission, no mocked capability readiness."""
from copy import deepcopy
import json
import pytest
from smm import series_workspace as w,series_ui_adapter as a
from smm.config import ROOT

@pytest.fixture
def ready(tmp_path):
    from smm.bids import default_bids
    root=w.create_task(tmp_path,ROOT,title='四资源逐日准入',start_date='2026-07-15',day_count=2,
        timezone='Asia/Shanghai',root_seed='917',input_model='causal_physical_v3')
    m=w.load_task(root)
    m['initial_state']=a.research_initial(root,m);m['initial_state_source']='research_assumption'
    for date in [d['date'] for d in m['days']]:
        cfg=a.day_config(root,m,date)
        m=w.replace_daily_bids(m,date,default_bids(cfg),source='explicit_test_template')
        m=w.confirm_daily_bids(m,date,cfg)
    return root,m

def test_real_contract_public_only_and_one_bundle(ready):
    root,m=ready
    assert a.capabilities()['causal_execution_contract']['ready'] is True
    days=a.compile_days(root,m)
    assert len(days)==2
    expected={'period'}|{u['id']+'_sched' for u in days[0].cfg.units['tie']}
    assert all(set(d.profiles)==expected and not d.profiles.attrs for d in days)
    assert all(d.cfg.base['forecast']['model']=='causal_physical_v3' for d in days)
    hashes={d.input_source_ref['bundle_content_hash'] for d in days};assert len(hashes)==1
    assert len(list((root/'causal_previews').iterdir()))==1
    assert a.readiness(root,m,'all_ready')['ready']
    assert not (root/'launch.json').exists() and not (root/'run').exists()

def test_installed_false_contract_cannot_be_ui_overridden(ready,monkeypatch):
    root,m=ready
    monkeypatch.setattr('smm.causal_integration.consumer_capabilities',lambda:dict(ready=False))
    report=a.readiness(root,m,'all_ready')
    assert not report['ready'] and 'not ready' in report['errors'][0]
    assert not (root/'causal_previews').exists()

def test_explicit_forecast_bid_keeps_prices_and_invalidates_confirmation(ready):
    root,m=ready;date=m['days'][0]['date'];before=deepcopy(m['days'][0]['bid_package'])
    updated=a.apply_issued_bid_availability(root,m,date)
    cfg=a.day_config(root,m,date)
    for u in cfg.units['renewable']:
        row=updated['units'][u['id']]
        assert row['segments']==before['units'][u['id']]['segments']
        assert len(row['avail_pmax'])==96 and row['availability_provenance']['kind']=='issued_forecast'
    m=w.replace_daily_bids(m,date,updated,source='explicit_forecast')
    assert w.bid_status(m['days'][0])=='待确认' and m['days'][0]['bid_package']['version']==2
    m=w.confirm_daily_bids(m,date,cfg);assert a.readiness(root,m,'all_ready')['ready']
    nextdate=m['days'][1]['date']
    m=w.replace_daily_bids(m,nextdate,updated,source='explicit_copy_previous')
    m=w.confirm_daily_bids(m,nextdate,a.day_config(root,m,nextdate))
    report=a.readiness(root,m,'all_ready')
    assert not report['ready'] and 'different issued forecast' in report['errors'][0]

def test_changed_forecast_requires_fresh_availability_without_resampling(ready):
    root,m=ready;date=m['days'][0]['date']
    actual1,_,bundle1=a.prepared_causal_bundle(root,m)
    bids=a.apply_issued_bid_availability(root,m,date)
    m=w.replace_daily_bids(m,date,bids,source='explicit_forecast');m=w.confirm_daily_bids(m,date,a.day_config(root,m,date))
    m['days'][0]['forecast_policy']['wind']='delayed'
    actual2,_,bundle2=a.prepared_causal_bundle(root,m)
    assert actual1.content_hash==actual2.content_hash and bundle1!=bundle2
    assert not a.readiness(root,m,'all_ready')['ready']

def test_missing_next_day_prefix_and_nested_launch_lock(ready):
    root,m=ready;m['days'][1]['bid_package']=None;m['days'][1]['bid_receipt']=None
    # Launch owns draft lock; bundle preparation must use a different lock.
    with w.locked(root):report=a.readiness(root,m,'ready_prefix')
    assert report['ready'] and m['days'][1]['date'] in report['warnings'][0]
    assert not a.readiness(root,m,'all_ready')['ready']

def test_runner_passes_verified_bundle_not_preview_provider(ready,tmp_path,monkeypatch):
    from smm.series_ui_runner import main
    root,m=ready;manifest=root/'test_frozen.json';w.atomic_json(manifest,m)
    actual,provider,bundle=a.prepared_causal_bundle(root,m)
    a.causal_preview(root,m,m['days'][0]['date'],stage='RT',issue_period=50)
    seen={}
    def record(days,output,**kwargs):
        seen.update(kwargs);seen['days']=days
        return {'status':'not_solved_test_spy'}
    monkeypatch.setattr('smm.series.run_series',record)
    main(root,manifest,'all_ready')
    assert seen['source_bundle']==bundle
    assert 'input_provider' not in seen and 'actual_store' not in seen
    assert all(not any(x.endswith(('_actual','_da','_id','_rt')) for x in d.profiles) for d in seen['days'])
    assert json.loads((root/'worker_outcome.json').read_text())['status']=='not_solved_test_spy'
