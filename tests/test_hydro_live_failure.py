"""Real tiny hydro infeasibility through the CLI worker's failure/UI lifecycle.

Only input preparation is injected with the 2-bus fixture; actual production DA,
rolling SCUC/SCED and hard-water constraints are solved. No full IEEE39 solve.
"""
import copy
import json
from pathlib import Path
import runpy
import sys

import pytest
import yaml


@pytest.mark.parametrize("termination",["infeasible", "cancel"])
def test_hydro_worker_failure_or_cancel_allows_new_run(tmp_path, monkeypatch, termination):
    from test_hydro_rolling import hydro_case
    from smm import scenario, pipeline, rolling
    from smm.live_runner import read_live
    from streamlit.testing.v1 import AppTest
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root/'app'))
    import common
    monkeypatch.setattr(scenario,'SCEN_DIR',tmp_path/'scenarios')
    monkeypatch.setattr(scenario,'RUNS_DIR',tmp_path/'runs')
    scenario.ensure_default()
    case=hydro_case(T=10);case.bids['version']=2
    # Store matching tiny config and the fixture identity in an isolated scene.
    name='tiny_hydro_unreachable';scene=scenario.scenario_path(name)
    (scene/'config').mkdir(parents=True);(scene/'data').mkdir()
    for filename,data in [('base',case.cfg.base),('units',case.cfg.units),('modes',{case.cfg.rule_set:case.cfg.mode})]:
        (scene/'config'/f'{filename}.yaml').write_text(yaml.safe_dump(data,allow_unicode=True))
    def prepare(cfg,*args,**kwargs):
        case.cfg=cfg
        return case
    monkeypatch.setattr(pipeline,'prepare',prepare)
    real_advance=rolling.advance
    injected=[]
    def depleted_actual_state(state,snap,cfg):
        result=real_advance(state,snap,cfg)
        if not injected:
            # Deliberately unreachable actual boundary for the next window:
            # V=10 and no inflow cannot reach the ~100 DA terminal target.
            if termination=='infeasible':
                result.hydro['H1']=10.
                cfg.units['hydro'][0]['seasons']['wet']['qin']=0.
            else:
                (out/'cancel.request').write_text('test cancel after first real solved window')
            injected.append(True)
        return result
    monkeypatch.setattr(rolling,'advance',depleted_actual_state)
    # Explicit CLI paths must be new; pre-existing folders require a live UI reservation.
    # Keep the real hydro solve/failure/cancel behavior, without bypassing admission.
    out=scenario.RUNS_DIR/name/'explicit_cli_worker'
    assert not out.exists()
    monkeypatch.setattr(sys,'argv',['run_scenario.py','--scenario',name,'--mode','full','--periods','4','--run-dir',str(out)])
    if termination=='infeasible':
        with pytest.raises(SystemExit) as stop:
            runpy.run_path(str(root/'scripts/run_scenario.py'),run_name='__main__')
        assert stop.value.code==1
    else:
        runpy.run_path(str(root/'scripts/run_scenario.py'),run_name='__main__')
    meta=json.loads((out/'meta.json').read_text());live=read_live(out)
    expected='failed' if termination=='infeasible' else 'stopped'
    assert meta['status']==expected
    assert ('水电' if termination=='infeasible' else '用户取消') in meta['error']
    assert live['phase']==expected and live['settled'] is False
    assert live['executed_periods']==1
    assert live['hydro_window_history']['0'][0]['terminal_error_1e4m3']==pytest.approx(0,abs=1e-6)
    assert not (out/'summary.json').exists()
    assert not list(out.glob('settlement*.csv'))
    at=AppTest.from_file(str(root/'app/主页.py'),default_timeout=30).run()
    at.session_state['watch_run']=str(out);at.switch_page('pages/2_运行.py').run()
    assert not at.exception,[e.value for e in at.exception]
    if termination=='infeasible':
        assert any('水电' in e.value and '失败' in e.value for e in at.error)
        assert any('不能视为完整日结果' in e.value for e in at.error)
        assert not any('RuntimeError' in e.value for e in at.error)
        diagnostics=next(e for e in at.expander if e.label=='技术详情（原始诊断，供排查）')
        assert diagnostics.proto.expanded is False
        assert any(meta['error'] in c.value for c in diagnostics.code)
    assert not any('完成，可到' in e.value for e in at.success)
    assert next(b for b in at.button if b.label=='▶ 开始运行').disabled is False
    # Failed record cannot block a fresh explicit launch; a repeated active launch
    # remains prohibited. Process spawning is stubbed here, not the failed solve.
    import os
    from types import SimpleNamespace
    real_spawn=common.subprocess.Popen
    def fake_spawn(*a,**k):
        if a and isinstance(a[0],list) and a[0] and a[0][0]=='git':return real_spawn(*a,**k)
        return SimpleNamespace(pid=os.getpid())
    monkeypatch.setattr(common.subprocess,'Popen',fake_spawn)
    retry=common.start_run(scenario.DEFAULT,'full',2,4)
    assert retry != out and (retry/'scenario_snapshot').exists()
    with pytest.raises(RuntimeError,match='已有运行'):
        common.start_run(scenario.DEFAULT,'full',2,4)

    evidence_dir=os.environ.get('SMM_UI_EVIDENCE_DIR')
    if evidence_dir:
        from smm.live_runner import atomic_json
        folder=Path(evidence_dir);folder.mkdir(parents=True,exist_ok=True)
        atomic_json(folder/f'hydro_{termination}_ui.json',{
            'scope':'real tiny 2-bus production DA/RT solve; fixture preparation injected; not a full IEEE39 run',
            'termination':termination,'meta':meta,'live_phase':live['phase'],'settled':live['settled'],
            'executed_periods':live['executed_periods'],'has_final_summary':(out/'summary.json').exists(),
            'has_settlement_files':bool(list(out.glob('settlement*.csv'))),
            'ui_errors':[e.value for e in at.error],'ui_exceptions':[e.value for e in at.exception],
            'restart_enabled':not next(b for b in at.button if b.label=='▶ 开始运行').disabled,
            'new_directory_created':retry!=out,'repeated_active_start_rejected':True,
            'hydro_window_history':live['hydro_window_history']})
