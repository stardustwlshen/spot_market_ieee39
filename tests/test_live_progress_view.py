"""Evidence-only progress and refresh regressions; no full-scale optimization."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'app'))
from run_progress_view import describe_progress, read_progress
from smm.live_runner import ProgressWriter, atomic_json


@pytest.mark.parametrize('activity,text', [('build','构建模型'),('scuc','SCUC 机组组合'),
    ('fix_integers','固定整数与准备'),('sced','固定整数 SCED'),('auxiliary_pricing','辅助定价')])
def test_da_actual_activity(activity, text):
    view = describe_progress({'status':'running'}, {}, {'phase':'da_activity','activity':activity})
    assert text in view['stage'] and view['count'] is None


@pytest.mark.parametrize('phase,label',[('id_start','日内 ID'),('rt_start','实时 RT')])
def test_ordinal_window_and_completed_counts_are_separate(phase,label):
    view=describe_progress({'status':'running'}, {'executed_periods':12,'total_periods':96},
        {'phase':phase,'period':12,'total':96})
    assert label in view['stage'] and '13 / 96' in view['stage']
    assert view['count']==(12,96)


@pytest.mark.parametrize('status,label',[('failed','运行失败'),('stopped','已取消'),
    ('timeout','超时'),('timed_out','超时'),('cancelling','等待安全边界'),('done','所选范围计算完成')])
def test_terminal_metadata_overrides_stale_solver_activity(status,label):
    view=describe_progress({'status':status},{},{'phase':'da_activity','activity':'scuc'})
    assert label in view['stage']


def test_scope_is_never_relabelled_as_full_day():
    assert '仅日前' in describe_progress({'status':'done','mode':'da'},{})['stage']
    assert '非完整日' in describe_progress({'status':'done','completed_scope':'debug_prefix'},{})['stage']
    assert '偏差结算' in describe_progress({'status':'running'},{},{'phase':'settlement'})['stage']


def test_legacy_and_missing_progress():
    assert '启动中' in describe_progress({'status':'starting'},{})['stage']
    assert '等待进度事件' in describe_progress({'status':'running'},{})['stage']
    old={'phase':'da_start','executed_periods':0,'total_periods':96}
    assert 'SCUC 机组组合' in describe_progress({'status':'running'},old,log='[DA] 模型构建 2.1s，开始 SCUC（HiGHS MILP）…')['stage']
    v=describe_progress({'status':'running'},old,log='[DA] SCUC timeLimit，用时 600s，目标 1，gap 1%')
    assert v['status']=='running' and '已返回' in v['stage'] and '失败' not in v['stage']
    assert describe_progress({'status':'running'},{'executed_periods':99,'total_periods':96})['count'] is None
    assert describe_progress({'status':'running'},{'executed_periods':True,'total_periods':96})['count'] is None


def test_small_start_events_do_not_rewrite_large_live_payload(tmp_path):
    writer=ProgressWriter(tmp_path)
    writer.state['large_existing_result']=['x'*2000]*100
    writer.write()
    before=(tmp_path/'live.json').read_bytes()
    writer.activity(dict(phase='rt_start',period=3,total=96,completed=3))
    assert (tmp_path/'live.json').read_bytes()==before
    record=json.loads((tmp_path/'progress.json').read_text())
    assert record['period']==3 and datetime.fromisoformat(record['updated_at']).tzinfo
    assert (tmp_path/'progress.json').stat().st_size<500
    writer.state['phase']='done';writer.write()
    assert read_progress(tmp_path)['activity']['phase']=='done'


def test_missing_and_corrupt_records_do_not_crash_or_rewrite(tmp_path):
    evidence=read_progress(tmp_path)
    assert evidence['live']=={} and evidence['activity']=={} and evidence['modified'] is None
    (tmp_path/'live.json').write_text('{broken')
    (tmp_path/'progress.json').write_text('[]')
    before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    evidence=read_progress(tmp_path)
    assert len(evidence['warnings'])==2
    assert before=={p.name:p.read_bytes() for p in tmp_path.iterdir()}


@pytest.mark.parametrize('status',['starting','running','cancelling','failed','stopped','timeout','done'])
def test_render_card_for_lifecycle(tmp_path,status):
    record=dict(dir=str(tmp_path),status=status,started=datetime.now().isoformat())
    source=f"from run_progress_view import render_progress_card\nrender_progress_card({record!r})"
    at=AppTest.from_string(source).run()
    assert not at.exception and len(at.metric)==3
    assert not at.get('progress')


def test_new_session_restores_active_run_but_manual_navigation_stays(tmp_path,monkeypatch):
    import single_day_workspace as ui
    import common
    sc=common.sc
    monkeypatch.setattr(sc,'SCEN_DIR',tmp_path/'scenarios')
    monkeypatch.setattr(sc,'RUNS_DIR',tmp_path/'runs')
    sc.ensure_default()
    run=sc.RUNS_DIR/'默认'/'active';run.mkdir(parents=True)
    atomic_json(run/'meta.json',dict(scenario='默认',mode='full',status='running',stage='日前出清',started=datetime.now().isoformat()))
    atomic_json(run/'live.json',dict(phase='da_start',executed_periods=0,total_periods=96))
    monkeypatch.setattr(ui,'run_status',lambda m:m['status'])
    monkeypatch.setattr(common,'run_status',lambda m:m['status'])
    at=AppTest.from_file(str(ROOT/'app/主页.py'),default_timeout=60).run()
    assert not at.exception
    nav=next(r for r in at.radio if r.label=='执行流程')
    assert nav.value=='5 结果' and at.session_state['watch_run']==str(run)
    assert 'network_run_options:默认' not in at.session_state
    nav.set_value('1 场景').run()
    assert not at.exception and next(r for r in at.radio if r.label=='执行流程').value=='1 场景'
    next(r for r in at.radio if r.label=='执行流程').set_value('4 运行').run()
    assert not at.exception and any('当前阶段' in i.value for i in at.info)
    assert 'network_run_options:默认' not in at.session_state


def test_missing_current_scope_and_missing_historical_scope_are_distinct(tmp_path):
    from test_network_input_version import frozen
    from smm.input_version import compare_source_version
    root,out,opts=frozen(tmp_path)
    status=compare_source_version(out,root,options=None)
    assert status['status']=='unknown' and '当前会话尚未确认运行范围' in status['message']
    assert '前 8 个实时时段' in status['message'] and '已保存场景与报价与本次冻结来源一致' in status['message']
    assert compare_source_version(out,root,options=opts)['status']=='equal'
    p=out/'frozen_input_provenance.json';raw=json.loads(p.read_text(encoding='utf8'))
    raw['source_version']['run_options']=None;atomic_json(p,raw)
    assert '历史运行缺少' in compare_source_version(out,root,options=opts)['message']


def test_tiny_da_activity_occurs_before_real_solver_calls(monkeypatch):
    from test_regulation import case_with_agc
    from smm import da,scuc
    events=[]
    original_scuc,original_sced=scuc.solve_scuc,scuc.solve_sced
    def solve_scuc(*a,**k):
        assert events[-1]['activity']=='scuc'
        return original_scuc(*a,**k)
    def solve_sced(*a,**k):
        assert events[-1]['activity']=='sced'
        return original_sced(*a,**k)
    monkeypatch.setattr(scuc,'solve_scuc',solve_scuc)
    monkeypatch.setattr(scuc,'solve_sced',solve_sced)
    completed=[]
    case=case_with_agc()
    case.cfg.mode['regulation']['enabled']=False
    result=da.run_da(case,verbose=False,activity_callback=events.append,stage_callback=completed.append)
    assert [e['activity'] for e in events]==['build','scuc','fix_integers','sced']
    assert all(e['phase']=='da_stage' for e in completed)
    assert result.sced_info


def test_small_card_avoids_large_live_reads_and_uses_verified_counts(tmp_path,monkeypatch):
    writer=ProgressWriter(tmp_path)
    writer.state['executed_periods']=41
    writer.activity(dict(phase='rt_start',period=41,total=96))
    original=Path.read_text
    def read(path,*a,**k):
        assert path.name!='live.json', 'small card must not read full results'
        return original(path,*a,**k)
    monkeypatch.setattr(Path,'read_text',read)
    evidence=read_progress(tmp_path,include_live=False)
    view=describe_progress({'status':'running'},evidence['live'],evidence['activity'])
    assert view['count']==(41,96) and '42 / 96' in view['stage']


def test_newer_live_evidence_overrides_a_blocked_stale_activity_file():
    live={'phase':'da_done','updated_at':'2026-11-07T12:05:00+00:00'}
    activity={'phase':'da_activity','activity':'scuc','updated_at':'2026-11-07T12:00:00+00:00'}
    assert '日前 DA 已完成' in describe_progress({'status':'running'},live,activity)['stage']


@pytest.mark.parametrize('intraday',[False,True])
def test_tiny_full_pipeline_emits_start_events_before_id_rt_and_settlement(monkeypatch,intraday):
    from test_hydro_rolling import hydro_case
    from smm import full,rolling,settlement
    case=hydro_case(T=8)
    case.cfg.mode['intraday']['enabled']=intraday
    activity,completed=[],[]
    original_rt,original_id,original_settle=rolling.run_rt_period,rolling.run_intraday_round,settlement.settle
    def rt(*args,**kwargs):
        assert activity[-1]['phase']=='rt_start' and activity[-1]['period']==args[5]
        return original_rt(*args,**kwargs)
    def id_round(*args,**kwargs):
        assert activity[-1]['phase']=='id_start' and activity[-1]['period']==args[4]
        return original_id(*args,**kwargs)
    def settle(*args,**kwargs):
        assert activity[-1]['phase']=='settlement'
        return original_settle(*args,**kwargs)
    monkeypatch.setattr(rolling,'run_rt_period',rt)
    monkeypatch.setattr(rolling,'run_intraday_round',id_round)
    monkeypatch.setattr(settlement,'settle',settle)
    result=full.run_full(case.cfg,case,periods=2,log=lambda *a:None,
        progress_callback=completed.append,activity_callback=activity.append)
    assert len(result['rr'].rt)==2 and result['settlement_scope']=='illustrative_prefix'
    assert activity[-1]['phase']=='full_complete'
    assert len([e for e in activity if e['phase']=='rt_start'])==2
    assert bool([e for e in activity if e['phase']=='id_start'])==intraday
    assert [e['phase'] for e in completed if e['phase']=='rt_window']==['rt_window']*2


def test_small_card_falls_back_if_live_is_newer_than_blocked_progress(tmp_path):
    import os
    atomic_json(tmp_path/'progress.json',{'phase':'da_activity','activity':'scuc','updated_at':'2026-11-07T12:00:00+00:00'})
    atomic_json(tmp_path/'live.json',{'phase':'da_done','updated_at':'2026-11-07T12:05:00+00:00'})
    os.utime(tmp_path/'progress.json',(100,100));os.utime(tmp_path/'live.json',(200,200))
    evidence=read_progress(tmp_path,include_live=False)
    assert '日前 DA 已完成' in describe_progress({'status':'running'},evidence['live'],evidence['activity'])['stage']


def test_full_output_stage_diagnostics_explicit_utf8_under_gbk_default(tmp_path,monkeypatch):
    from test_hydro_rolling import hydro_case
    from smm import full
    case=hydro_case(T=8)
    result=full.run_full(case.cfg,case,periods=2,log=lambda *a:None)
    original=Path.read_text
    observed=[]
    def gbk_read(path,*args,**kwargs):
        if path.name=='stage_diagnostics.json':
            encoding=kwargs.get('encoding',args[0] if args else None)
            observed.append(encoding)
            if encoding is None:
                kwargs['encoding']='gbk'
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',gbk_read)
    summary=full.write_full_outputs(result,tmp_path,make_plots=False)
    assert observed==['utf-8']
    text=original(tmp_path/'stage_diagnostics.json',encoding='utf-8')
    assert any(ord(c)>127 for c in text)
    assert summary['executed_periods']==2
