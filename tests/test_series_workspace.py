from copy import deepcopy
from pathlib import Path
import json
import pytest
from smm import series_workspace as w
from smm import series_ui_adapter as a
from smm.config import ROOT

@pytest.fixture
def task(tmp_path):
    return w.create_task(tmp_path,ROOT,title='逐日报价测试',start_date='2026-07-15',day_count=3,timezone='Asia/Shanghai',root_seed='718')

def test_create_has_no_implicit_daily_bids(task):
    m=w.load_task(task)
    assert [d['date'] for d in m['days']]==['2026-07-15','2026-07-16','2026-07-17']
    assert all(w.bid_status(d)=='未报价' for d in m['days'])
    assert m['initial_state'] is None
    assert m['execution']['timing']=='sequential_actual_carryover'
    assert a.verify_source(task,m)

def test_random_is_repeatable_protects_manual_and_bids(task):
    m=w.load_task(task);m['days'][0]['locked']=['wind_multiplier'];m['days'][0]['wind_multiplier']=1.17
    m['days'][1]['bid_package']={'units':{'mine':{}},'version':8}
    dates=[d['date'] for d in m['days']]
    x=w.randomize_unlocked(m,dates);y=w.randomize_unlocked(m,dates)
    assert x==y and x['days'][0]['wind_multiplier']==1.17
    assert x['days'][1]['bid_package']==m['days'][1]['bid_package']
    assert x['days'][0]['day_uid']==m['days'][0]['day_uid']

def test_random_stable_independent_other_days(task):
    m=w.load_task(task);date=m['days'][1]['date']
    one=w.randomize_unlocked(m,[date]);all_=w.randomize_unlocked(m,[d['date'] for d in m['days']])
    assert one['days'][1]==all_['days'][1]

def test_daily_copy_independent_confirm_version(task):
    from smm.bids import default_bids
    m=w.load_task(task);d=m['days'][0]['date'];cfg=a.day_config(task,m,d)
    m=w.replace_daily_bids(m,d,default_bids(cfg),source='explicit_template')
    assert w.bid_status(m['days'][0])=='待确认'
    m=w.confirm_daily_bids(m,d,cfg)
    assert w.bid_status(m['days'][0])=='已确认'
    next_=m['days'][1]['date'];m=w.replace_daily_bids(m,next_,m['days'][0]['bid_package'],source='explicit_copy')
    assert m['days'][1]['bid_receipt'] is None
    assert m['days'][1]['bid_package']['date']==next_
    m['days'][1]['bid_package']['units']['PS1']['gen_segments'][0]['price']+=1
    assert m['days'][0]['bid_package']['units']['PS1']['gen_segments'][0]['price']!=m['days'][1]['bid_package']['units']['PS1']['gen_segments'][0]['price']
    b=deepcopy(m['days'][0]['bid_package']);m=w.replace_daily_bids(m,d,b,source='edit')
    assert m['days'][0]['bid_package']['version']==2 and w.bid_status(m['days'][0])=='待确认'

def test_confirmation_tampering_is_stale(task):
    from smm.bids import default_bids
    m=w.load_task(task);d=m['days'][0]['date'];cfg=a.day_config(task,m,d)
    m=w.confirm_daily_bids(w.replace_daily_bids(m,d,default_bids(cfg),source='explicit'),d,cfg)
    m['days'][0]['bid_package']['units']['PS1']['vup_end']=201
    assert w.bid_status(m['days'][0])=='确认已失效'

def test_revision_concurrency_does_not_overwrite(task):
    m=w.load_task(task);x=deepcopy(m);x['title']='第一会话'
    w.update_task(task,x,expected_revision=1)
    with pytest.raises(RuntimeError,match='另一会话'):w.update_task(task,m,expected_revision=1)
    assert w.load_task(task)['title']=='第一会话'

def test_started_day_and_physical_edits_blocked_future_quotes_allowed(task):
    m=w.load_task(task);(task/'launch.json').write_text('{}');(task/'run/day_2026-07-15').mkdir(parents=True)
    with pytest.raises(ValueError,match='冻结'):w.update_task(task,m,expected_revision=1)
    x=w.replace_daily_bids(m,'2026-07-15',{'units':{}},source='test')
    with pytest.raises(ValueError,match='不可修改'):w.update_task(task,x,expected_revision=1,changed_day='2026-07-15',bids_only=True)
    x=w.replace_daily_bids(m,'2026-07-16',{'units':{}},source='test')
    y=w.update_task(task,x,expected_revision=1,changed_day='2026-07-16',bids_only=True)
    assert y['revision']==2
    y['days'][1]['load_multiplier']=1.2
    with pytest.raises(ValueError,match='只可修改'):w.update_task(task,y,expected_revision=2,changed_day='2026-07-16',bids_only=True)

def test_source_profile_tamper_fails(task):
    m=w.load_task(task);p=task/'source/baseline_profiles.json';p.write_text('{}')
    with pytest.raises(ValueError,match='快照已变化'):a.verify_source(task,m)

def test_wind_pv_scale_separate_known_mode_explicit(task):
    m=w.load_task(task);d=m['days'][0]['date'];base=a.deterministic_profiles(task,m,d)
    m['days'][0]['wind_multiplier']=.5
    p=a.deterministic_profiles(task,m,d)
    assert (p.W1_actual==base.W1_actual*.5).all()
    assert p.PV1_actual.equals(base.PV1_actual)
    assert p.W1_actual.equals(p.W1_da)
    assert p.attrs['information_policy']=='deterministic_known_inputs_v1'

def test_new_forecast_never_silently_falls_back(task):
    m=w.load_task(task);m['generation']['model']='causal_physical_v3'
    # Engine capability may be absent in the UI-only tree. Either explicit
    # readiness blocker is acceptable; never generate legacy forecasts.
    report=a.readiness(task,m,'all_ready')
    assert not report['ready'] and report['errors']

@pytest.mark.parametrize('seed',['-1','abc','1.5','1'*81])
def test_bad_seed_rejected(tmp_path,seed):
    with pytest.raises(ValueError):w.create_task(tmp_path,ROOT,title='x',start_date='2026-07-15',day_count=1,timezone='UTC',root_seed=seed)
    assert not list(tmp_path.glob('*/manifest.json'))

@pytest.mark.parametrize('key,value',[('season','summer'),('wind_multiplier',float('nan')),('pv_multiplier',True),('load_multiplier',3.)])
def test_bad_day_values_rejected(task,key,value):
    m=w.load_task(task);m['days'][0][key]=value
    with pytest.raises(ValueError):w.validate_manifest(m)

def test_known_spawn_failure_is_not_unknown(task):
    (task/'launch.json').write_text(json.dumps({'status':'not_started','identity':None}))
    assert a.launch_state(task)=='dead'


def test_research_initial_edit_applies_and_invalid_not_clipped(task):
    m=w.load_task(task);m['initial_state']=a.research_initial(task,m);m['initial_state_source']='research_assumption'
    edited=a.update_research_state(task,m,{'storage':{'ES1':111.1234567890123}})
    assert edited['state']['storage']['ES1']==111.1234567890123
    with pytest.raises(ValueError):a.update_research_state(task,m,{'storage':{'ES1':100000.}})
    assert m['initial_state']['state']['storage']['ES1']!=111.1234567890123
    m['initial_state_source']='executed_checkpoint'
    with pytest.raises(ValueError,match='只读'):a.update_research_state(task,m,{'storage':{'ES1':110.}})


def test_research_fork_breaks_chain_and_quote_confirmation(task):
    from smm.bids import default_bids
    m=w.load_task(task);m['initial_state']=a.research_initial(task,m);m['initial_state_source']='executed_checkpoint'
    date=m['days'][0]['date'];cfg=a.day_config(task,m,date)
    m=w.confirm_daily_bids(w.replace_daily_bids(m,date,default_bids(cfg),source='explicit'),date,cfg)
    original=(task/'manifest.json').read_bytes()
    child=w.fork_research_task(task,m,reason='研究初态敏感性')
    new=w.load_task(child)
    assert (task/'manifest.json').read_bytes()==original
    assert new['parent']['continuity_broken'] is True
    assert new['initial_state_source']=='research_assumption'
    assert all(d['bid_receipt'] is None for d in new['days'])
    assert a.validate_initial(child,new,new['initial_state'])==new['initial_state']


def test_missing_day_blocks_all_ready_prefix_requires_explicit_mode(task):
    from smm.bids import default_bids
    m=w.load_task(task);m['initial_state']=a.research_initial(task,m);m['initial_state_source']='research_assumption'
    date=m['days'][0]['date'];cfg=a.day_config(task,m,date)
    m=w.confirm_daily_bids(w.replace_daily_bids(m,date,default_bids(cfg),source='explicit'),date,cfg)
    report=a.readiness(task,m,'all_ready');assert not report['ready'] and '2026-07-16' in report['errors'][0]
    prefix=a.readiness(task,m,'ready_prefix');assert prefix['ready'] and '2026-07-16' in prefix['warnings'][0]
    assert not (task/'launch.json').exists()


def test_causal_random_preserves_manual_preset(task):
    m=w.load_task(task);m['generation']['model']='causal_physical_v3'
    d=m['days'][0];d['process_specs']['wind']={'preset':'wind_ramp','parameters':{'target':.3}}
    d['locked']=['process_specs.wind']
    dates=[x['date'] for x in m['days']]
    x=w.randomize_unlocked(m,dates);assert x==w.randomize_unlocked(m,dates)
    assert x['days'][0]['process_specs']['wind']==d['process_specs']['wind']
    assert all(y['forecast_policy'] for y in x['days'])


def test_causal_season_expands_consumed_inflow_and_preview_is_bound(task):
    m=w.load_task(task);m['generation']['model']='causal_physical_v3';m['days'][1]['season']='dry'
    plan,policies=a.causal_plan(task,m)
    resources={r.resource_id:r for r in plan.resources};cfg=a.day_config(task,m,m['start_date'])
    h=cfg.units['hydro'][0];r=resources[h['id']]
    assert plan.day_specs[m['days'][1]['date']][h['id']]['parameters']['target']==h['seasons']['dry']['qin']/r.scale
    preview=a.causal_preview(task,m,m['start_date'],stage='DA')
    assert len(preview['actual']['load'])==96 and len(preview['forecast']['load'])==96
    assert preview['issue_time_utc'].endswith('Z') or '+' in preview['issue_time_utc']
    repeat=a.causal_preview(task,m,m['start_date'],stage='DA')
    assert repeat['actual_hash']==preview['actual_hash'] and repeat['forecast']==preview['forecast']
    m['days'][0]['forecast_policy']['wind']='delayed'
    revised=a.causal_preview(task,m,m['start_date'],stage='DA')
    assert revised['actual_hash']==preview['actual_hash']
    assert revised['forecast']['W1']!=preview['forecast']['W1']
    assert not a.readiness(task,m,'all_ready')['ready']


def test_explicit_base_revision_preserves_old_and_reconfirms(task):
    m=w.load_task(task);old=(task/'manifest.json').read_bytes()
    m['days'][0]['wind_multiplier']=.83;m['days'][0]['locked']=['wind_multiplier']
    child=w.revise_from_current_base(task,m);new=w.load_task(child)
    assert (task/'manifest.json').read_bytes()==old
    assert new['task_id']!=m['task_id'] and new['initial_state'] is None
    assert new['days'][0]['wind_multiplier']==.83
    assert all(d['bid_receipt'] is None for d in new['days'])
    assert a.verify_source(child,new)
