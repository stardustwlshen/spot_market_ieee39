import json
from types import SimpleNamespace
import pandas as pd
import pytest
from smm.live_runner import ProgressWriter, RunCancelled, atomic_json, read_live, freeze_inputs
from smm.config import load_config
from smm.bids import default_bids


def test_atomic_progress_visible_before_completion_and_cancel(tmp_path, monkeypatch):
    p=ProgressWriter(tmp_path,96)
    assert read_live(tmp_path)['executed_periods']==0
    table=pd.DataFrame([{'period':0,'P_mw':12.}])
    monkeypatch.setattr('smm.rolling.rt_tables',lambda da,rr:{k:table for k in ('units','prices','system','storage','lines')})
    da=SimpleNamespace(tables={'prices':pd.DataFrame([{'period':0,'lmp':300.,'optional_field':float('nan')}])})
    p({'phase':'da_done','da':da})
    rr=SimpleNamespace(rt=[{'t':0,'snap':{'P':{'C1':[12.,13.]}},'prices':{}}])
    p({'phase':'rt_window','rr':rr})
    live=read_live(tmp_path)
    assert live['executed_periods']==1 and not live['settled']
    assert live['da_prices'][0]['optional_field'] is None
    assert live['lookahead'][0]['status']=='已执行'
    assert live['lookahead'][1]['status']=='前瞻计划（未执行）'
    (tmp_path/'cancel.request').touch()
    with pytest.raises(RunCancelled):p.check_cancel()
    assert read_live(tmp_path)['executed_periods']==1
    assert not list(tmp_path.glob('*.tmp'))


def test_snapshot_bid_isolation_and_reject_invalid(tmp_path):
    cfg=load_config(); bids=default_bids(cfg)
    snap=freeze_inputs(cfg.root,tmp_path,bids)
    submitted=json.loads((tmp_path/'submitted_bids.json').read_text())
    uid=next(iter(bids['units']));bids['units'][uid]['manual_test_marker']='changed later'
    assert 'manual_test_marker' not in submitted['units'][uid]
    assert (snap/'config/modes.yaml').read_bytes()==(cfg.root/'config/modes.yaml').read_bytes()
    with pytest.raises(FileExistsError):freeze_inputs(cfg.root,tmp_path,bids)


def test_launch_freezes_inputs_and_blocks_duplicate(tmp_path, monkeypatch):
    import sys,os
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
    import common
    from smm import scenario
    monkeypatch.setattr(scenario,'SCEN_DIR',tmp_path/'scenarios')
    scenario.ensure_default()
    monkeypatch.setattr(scenario,'RUNS_DIR',tmp_path/'runs')
    captured=[]
    real_spawn=common.subprocess.Popen
    def fake_spawn(*args,**kwargs):
        if args and args[0][0]=="git":return real_spawn(*args,**kwargs)
        captured.append(args);return SimpleNamespace(pid=os.getpid())
    monkeypatch.setattr(common.subprocess,'Popen',fake_spawn)
    cfg=common.scen_config(scenario.DEFAULT)
    bids=default_bids(cfg)
    d=common.start_run(scenario.DEFAULT,'full',bids['version'],4,bids)
    with pytest.raises(RuntimeError,match='已有运行'):
        common.start_run(scenario.DEFAULT,'full',bids['version'],4,bids)
    assert len(captured)==1
    assert (d/'submitted_bids.json').exists()
    common.stop_run({'dir':str(d)})
    meta=json.loads((d/'meta.json').read_text());meta['dir']=str(d)
    assert common.run_status(meta)=='cancelling'
    assert (d/'cancel.request').exists()


def test_streamlit_displays_partial_without_settlement(tmp_path,monkeypatch):
    import sys,os
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from smm import scenario
    monkeypatch.setattr(scenario,'SCEN_DIR',tmp_path/'scenarios')
    monkeypatch.setattr(scenario,'RUNS_DIR',tmp_path/'runs')
    d=scenario.RUNS_DIR/'fixture'/'partial';d.mkdir(parents=True)
    atomic_json(d/'meta.json',{'scenario':'fixture','mode':'full','status':'running','pid':os.getpid(),
                             'started':'2099-01-01','periods':96,'stage':'RT','bid_version':2})
    atomic_json(d/'live.json',{'phase':'rt_window','executed_periods':1,'total_periods':96,'settled':False,
      'executed':{'prices':[{'period':0,'lmp':300.,'lmp_raw':300.}],
                  'units':[{'period':0,'unit':'C1','type':'coal','P_mw':20.}],
                  'system':[{'period':0,'load_mw':20.}]},
      'lookahead':[{'period':1,'power_mw':21.,'status':'前瞻计划（未执行）'}]})
    root=Path(__file__).resolve().parents[1]
    at=AppTest.from_file(str(root/'app/pages/2_运行.py'),default_timeout=60).run()
    assert not at.exception
    assert any('实时已执行：1 / 96' in x.value for x in at.subheader)
    assert any('不包含完整日最终结算' in x.value for x in at.caption)
    assert next(x for x in at.button if x.label=='▶ 开始运行').disabled
    assert len(at.dataframe)>=3
    assert len(at.get("vega_lite_chart"))>=2  # System charts plus selected-unit diagnostics.


def test_streamlit_simulation_updates_only_selected_draft_without_touching_v2(tmp_path, monkeypatch):
    import copy
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from smm import scenario
    from smm.bids import load_bids
    monkeypatch.setattr(scenario, 'SCEN_DIR', tmp_path / 'scenarios')
    monkeypatch.setattr(scenario, 'RUNS_DIR', tmp_path / 'runs')
    scenario.ensure_default()
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root=scenario.scenario_path('默认'))
    saved = load_bids(cfg)
    before = {p: p.read_bytes() for p in cfg.root.rglob('*') if p.is_file()}
    at = AppTest.from_file(str(root / 'app/pages/2_运行.py'), default_timeout=60).run()
    key = next(x for x in at.number_input if x.label == '空载报价（元/h）').key.removeprefix('quote_edit:').removesuffix(':C1:noload')
    initial = copy.deepcopy(at.session_state[key])
    next(x for x in at.button if x.label == '生成随机报价预览').click().run()
    assert at.session_state[key] == initial
    next(x for x in at.button if x.label == '采用预览报价').click().run()
    assert not at.exception
    changed = at.session_state[key]
    assert changed['units']['C1'] != initial['units']['C1']
    assert all(changed['units'][uid] == bid for uid, bid in initial['units'].items() if uid != 'C1')
    assert all(p.read_bytes() == data for p, data in before.items())
    assert not scenario.RUNS_DIR.exists()


def test_invalid_regulation_rejected_before_snapshot_or_launch(tmp_path):
    cfg=load_config()
    with pytest.raises(ValueError,match='缺少字段'):
        freeze_inputs(cfg.root,tmp_path,regulation={'enabled':True})
    assert not (tmp_path/'scenario_snapshot').exists()


def test_current_ui_is_energy_only_and_historical_regulation_cannot_start(tmp_path,monkeypatch):
    import sys,copy
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from smm import scenario
    root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'app'))
    import common
    monkeypatch.setattr(scenario,'SCEN_DIR',tmp_path/'scenarios')
    monkeypatch.setattr(scenario,'RUNS_DIR',tmp_path/'runs')
    at=AppTest.from_file(str(root/'app/pages/2_运行.py'),default_timeout=60).run()
    assert not at.exception
    assert not any('调频' in x.label for x in at.number_input)
    assert not any('调频' in x.label for x in at.checkbox)
    with pytest.raises(ValueError,match='历史归档'):
        common.start_run(scenario.DEFAULT,'full',2,4,regulation={'enabled':True})


def test_other_pid_namespace_does_not_mark_live_run_failed(tmp_path):
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
    import common
    meta={'dir':str(tmp_path),'status':'running','pid':999999999,'pid_namespace':'different-namespace'}
    atomic_json(tmp_path/'meta.json',meta)
    assert common.run_status(meta)=='running'
    assert json.loads((tmp_path/'meta.json').read_text())['status']=='running'


def test_stage_diagnostics_remain_separate_from_executed_prefix(tmp_path,monkeypatch):
    stages={stage:{'units':[{'period':0,'unit':'C1','type':'coal','P_mw':power,'on':1,'q_reg_mw':None}],
                   'summary':{'objective':power,'objective_kind':kind}}
            for stage,power,kind in [('scuc_preplan',100.,'energy'),('agc_auxiliary',120.,'score_only'),('energy_sced',110.,'energy')]}
    writer=ProgressWriter(tmp_path)
    writer({'phase':'da_stage','stage_diagnostics':stages})
    assert read_live(tmp_path)['da_stages']['agc_auxiliary']['units'][0]['P_mw']==120.
    monkeypatch.setattr('smm.rolling.rt_tables',lambda da,rr:{'units':pd.DataFrame([{'period':0,'unit':'C1','P_mw':110.}])})
    rr=SimpleNamespace(rt=[{'t':0,'snap':{'P':{'C1':[110.]}},'prices':{},'stage_diagnostics':stages}])
    writer({'phase':'rt_window','rr':rr,'da':object()})
    live=read_live(tmp_path)
    assert live['executed']['units'][0]['P_mw']==110.
    assert live['stage_history']['0']['scuc_preplan']['units'][0]['P_mw']==100.
    assert live['stage_history']['0']['agc_auxiliary']['units'][0]['q_reg_mw'] is None


def test_cancel_at_completed_da_stage_preserves_stage_snapshot(tmp_path):
    p=ProgressWriter(tmp_path)
    (tmp_path/'cancel.request').touch()
    with pytest.raises(RunCancelled):
        p({'phase':'da_stage','stage_diagnostics':{'scuc_preplan':{'units':[],'summary':{'status':'done'}}}})
    assert read_live(tmp_path)['da_stages']['scuc_preplan']['summary']['status']=='done'
    assert read_live(tmp_path)['settled'] is False


def test_immediate_restart_uses_distinct_run_directory(tmp_path,monkeypatch):
    from smm import scenario
    monkeypatch.setattr(scenario,'RUNS_DIR',tmp_path)
    first=scenario.new_run_dir('retry','full')
    second=scenario.new_run_dir('retry','full')
    assert first != second and first.exists() and second.exists()
