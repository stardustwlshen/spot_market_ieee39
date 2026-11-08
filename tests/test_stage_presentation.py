"""Presentation observes saved results; never turns ID/auxiliary plans into execution."""
from pathlib import Path
from types import SimpleNamespace as NS
import json
import sys
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
from stage_results import execution_rows,intraday_inventory,intraday_state_rows
from smm.live_runner import ProgressWriter,read_live


def test_only_formal_rt_first_period_is_execution():
    rows=[dict(period=4,P_mw=100),dict(period=5,P_mw=200)]
    for stage in ('scuc_preplan','energy_sced','auxiliary_pricing'):
        for market in ('DA','ID','RT'):
            f=execution_rows(rows,stage,market,4)
            assert int(f['结果范围'].eq('本窗口已执行首段').sum())==int(stage=='energy_sced' and market=='RT')
    assert '结果范围' not in rows[0]


def test_failed_only_id_inventory_and_unknown_old_evidence():
    failures=[dict(tau=0,phase='scuc',reason='infeasible',fallback='day_ahead')]
    inv=intraday_inventory({'failed_rounds':failures},pd.DataFrame())
    assert len(inv)==1 and not inv.success.iloc[0]
    assert intraday_inventory({},None).empty
    both=intraday_inventory({'failed_rounds':failures},pd.DataFrame([dict(tau=4,round='01:00')]))
    assert both.tau.tolist()==[0,4] and both.success.tolist()==[False,True]


def test_four_business_sections_for_enabled_id_and_failure_only(tmp_path):
    for intraday in (True,False):
        code=f"import sys\nsys.path.insert(0,{str(ROOT/'app')!r})\nfrom result_navigation import build_result_navigation\nbuild_result_navigation({{}},{{}},directory={str(tmp_path)!r},intraday={intraday})"
        at=AppTest.from_string(code,default_timeout=30).run()
        assert not at.exception
        labels=[x.label for x in at.tabs]
        assert labels == ['系统总览', '机组分析', '价格分析', '网络与安全']
        assert all(not x.proto.expanded for x in at.expander)
        assert any(x.label == '完整存档与来源' for x in at.expander)


def test_id_progress_does_not_execute_or_settle(tmp_path):
    w=ProgressWriter(tmp_path)
    stages={'energy_sced':{'units':[{'period':4,'P_mw':50}], 'summary':{'price_role':'indicative_not_settled'}}}
    rr=NS(id_rounds=[dict(tau=4,issue_t=3,stage_diagnostics=stages)],id_failures=[])
    w({'phase':'id_round','rr':rr,'tau':4})
    live=read_live(tmp_path)
    assert live['executed_periods']==0 and not live['settled']
    assert live['id_stage_history']['4']==stages
    rr.id_failures=[dict(tau=8,phase='formal_sced',reason='infeasible')]
    w({'phase':'id_round','rr':rr,'tau':8})
    assert read_live(tmp_path)['id_failures']==rr.id_failures
    assert 'executed' not in read_live(tmp_path)


def test_old_id_water_evidence_uses_saved_values_only():
    evidence={'id_rounds':[dict(tau=4,snapshot={'Vu':{'PS':[7.,8.]},'Vh':{'H':[9.,10.]}})]}
    f=intraday_state_rows(evidence,4)
    assert f.period.tolist()==[4,5,4,5] and f['数值'].tolist()==[7.,8.,9.,10.]
    assert intraday_state_rows(evidence,0).empty


def test_bad_optional_id_stages_do_not_hide_da_and_rt(tmp_path):
    from result_policy_notes import saved_stage_diagnostics
    (tmp_path/'stage_diagnostics.json').write_text(json.dumps({'da':{'energy_sced':{}},'rt_windows':[],'id_rounds':42}))
    data,error=saved_stage_diagnostics(tmp_path)
    assert data['da']=={'energy_sced':{}} and '日内' in error and 'id_rounds' not in data


def test_real_id_prices_and_snapshots_are_indicative_and_live(tmp_path,monkeypatch):
    from test_causal_chain import thermal_case
    from smm.da import run_da
    from smm.rolling import run_rolling
    case=thermal_case();w=ProgressWriter(tmp_path,total=12)
    da=run_da(case,verbose=False);w({'phase':'da_done','da':da})
    events=[]
    def progress(e):events.append((e['phase'],e.get('completed')));w(e)
    rr=run_rolling(da,periods=2,log=lambda *x:None,progress_callback=progress)
    assert events[0]==('id_round',0) and events[1]==('rt_window',1)
    data=rr.id_rounds[0]['stage_diagnostics']['energy_sced']
    assert data['prices'] and not data['summary']['prices_are_market_prices']
    assert data['summary']['price_role']=='indicative_not_settled'
    assert data['summary']['market_stage']=='ID'
    assert len(read_live(tmp_path)['id_rounds'])==1
    assert read_live(tmp_path)['executed_periods']==2 and not read_live(tmp_path)['settled']
    # No altered physical snapshot from the read-only exporter.
    assert data['units'][0]['P_mw']==rr.id_rounds[0]['snap']['P'][data['units'][0]['unit']][0]
    # Exercise the actual full writer through stage persistence, then stop before
    # unrelated financial/report exports (the complete writer also has a real IEEE39 run).
    from smm.full import write_full_outputs
    import smm.value_outputs
    def stop_after_stages(*args,**kwargs):raise RuntimeError('stage export checkpoint')
    monkeypatch.setattr(smm.value_outputs,'write_value_outputs',stop_after_stages)
    with pytest.raises(RuntimeError,match='stage export checkpoint'):
        write_full_outputs({'da':da,'rr':rr,'settle':{},'rt':{},'id':{}},tmp_path/'archive',make_plots=False)
    saved=json.loads((tmp_path/'archive/stage_diagnostics.json').read_text())
    assert saved['id_rounds'][0]['stages']==rr.id_rounds[0]['stage_diagnostics']
    assert len(saved['rt_windows'])==2 and saved['id_failures']==[]
    assert pd.read_csv(tmp_path/'archive/solver_windows.csv').stage.tolist()==['DA','RT','RT','ID']


def test_failed_formal_id_retains_only_completed_scuc_without_fake_success(monkeypatch,tmp_path):
    from test_causal_chain import thermal_case
    from smm.da import run_da
    from smm import rolling,scuc
    case=thermal_case();da=run_da(case,verbose=False)
    real=scuc.solve_sced
    def fail_id(m,*args,**kwargs):
        if m._case.stage=='ID':
            raise scuc.InfeasibleModelError('controlled formal ID failure',phase='formal_sced',solver_status='infeasible')
        return real(m,*args,**kwargs)
    monkeypatch.setattr(scuc,'solve_sced',fail_id)
    w=ProgressWriter(tmp_path);w({'phase':'da_done','da':da})
    rr=rolling.run_rolling(da,periods=1,log=lambda *x:None,progress_callback=w)
    assert not rr.id_rounds and len(rr.id_failures)==1
    assert set(rr.id_failed_stages[0]['stages'])=={'scuc_preplan'}
    assert rr.rt[0]['reference_source']=='day_ahead'
    live=read_live(tmp_path)
    assert set(live['id_failed_stage_history']['0'])=={'scuc_preplan'}
    assert live['executed_periods']==1
    code=f"import sys,json\nsys.path.insert(0,{str(ROOT/'app')!r})\nfrom stage_results import live_intraday_view\nlive_intraday_view(json.load(open({str(tmp_path/'live.json')!r})),prefix='test')"
    at=AppTest.from_string(code,default_timeout=30).run()
    assert not at.exception and any('失败 1 轮' in x.value for x in at.caption)
    assert next(x for x in at.selectbox if x.label=='查看求解阶段').options==['SCUC 启停预计划']


def test_feasible_id_with_missed_gap_is_not_failed_or_full_precision():
    table=pd.DataFrame([dict(tau=16,round='04:00',gap=.0292,gap_target=.005,gap_target_met=False)])
    inv=intraday_inventory({},table)
    assert inv.success.iloc[0] and '未达目标精度' in inv.status.iloc[0]
