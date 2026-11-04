"""Saved output quality is prominent, scoped, and never inferred from defaults."""
import json
import os
import sys
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
from market_views import result_quality_messages


def freeze(path):
    config=path/'scenario_snapshot/config';config.mkdir(parents=True)
    (config/'base.yaml').write_text('rule_set: MKT\nsolver:\n  mip_rel_gap: 0.01\n')
    (config/'modes.yaml').write_text('MKT:\n  realtime:\n    mip_rel_gap: 0.01\n')


def render(summary,da,path):
    code=f"import sys\nsys.path.insert(0,{str(ROOT/'app')!r})\nfrom market_views import result_health_dashboard\nresult_health_dashboard({summary!r},{da!r},directory={str(path)!r})"
    return AppTest.from_string(code,default_timeout=30).run()


def test_actual_quality_values_prominent_and_soft_targets_separate(tmp_path):
    freeze(tmp_path)
    summary={'checks':{'physical_feasible_checked_constraints':False,'network_feasible':False,
                       'rt_line_overload_max_mw':8.0277038707,'rt_line_overload_count':1,
                       'storage_anchor_slack_max_mwh':12.024275,'pumped_anchor_slack_max_1e4m3':24.367559},
             'reserve':{'rt_executed_reserve_up_short_max_mw':.2686118014}}
    at=render(summary,{'scuc_gap':.0239733055},tmp_path)
    assert not at.exception
    assert any('2.397%' in x.value and '1.000%' in x.value and '超过' in x.value for x in at.warning)
    assert any('线路越限' in x.value and '8.0277 MW' in x.value for x in at.error)
    assert any('已执行首时段上备用缺额' in x.value and '0.2686 MW' in x.value for x in at.error)
    assert any('12.0243 MWh' in x.value and '24.3676 万m³' in x.value and '不等于物理违规' in x.value for x in at.info)
    assert not at.success and not at.expander


def test_missing_legacy_outputs_do_not_claim_pass_or_compare_current_defaults(tmp_path):
    at=render({}, {'scuc_gap':.025},tmp_path)
    assert not at.exception and not at.error and not at.success
    assert any('目标精度未记录' in x.value for x in at.info)
    assert any('不能按零' in x.value for x in at.info)
    (tmp_path/'stage_diagnostics.json').write_text('{partial')
    assert result_quality_messages({}, {},directory=tmp_path)


def test_only_executed_shortage_used_not_lookahead(tmp_path):
    (tmp_path/'rt_reserve_system.csv').write_text('period,R_up_short,R_dn_short\n2,0,0\n')
    messages=result_quality_messages({'reserve':{'passed':True}}, {},directory=tmp_path,
        live={'executed':{'system':[{'period':2,'R_up_short':0,'reserve_up_short_max_win':99}]}})
    assert not any(level=='error' and '备用缺额' in text for level,text in messages)


def test_reserve_audit_pass_does_not_hide_positive_shortage(tmp_path):
    (tmp_path/'rt_reserve_system.csv').write_text('period,R_up_short\n64,0.2686\n')
    messages=result_quality_messages({'reserve':{'passed':True}}, {},directory=tmp_path)
    assert any(level=='error' and '0.2686 MW' in text and '64' in text for level,text in messages)


def test_saved_da_only_limits_are_checked_without_current_network(tmp_path):
    (tmp_path/'da_line_flows.csv').write_text('period,line,flow_mw,rate_mw\n0,L1,105,100\n')
    messages=result_quality_messages({}, {},directory=tmp_path)
    assert any(level=='error' and '日前正式调度线路越限' in text and '5.0000 MW' in text for level,text in messages)


def test_rt_gap_failures_and_unknown_records_stay_distinct(tmp_path):
    freeze(tmp_path)
    windows=[{'stages':{'scuc_preplan':{'summary':{'gap':gap}}}} for gap in [.005,.02,None]]
    (tmp_path/'stage_diagnostics.json').write_text(json.dumps({'rt_windows':windows}))
    messages=result_quality_messages({}, {},directory=tmp_path)
    assert any(level=='warning' and '1/3' in text and '2.000%' in text for level,text in messages)
    assert any('部分实时窗口缺少' in text for _,text in messages)


def test_soft_deviation_alone_is_not_a_physical_error(tmp_path):
    summary={'checks':{'physical_feasible_checked_constraints':True,'network_feasible':True,
                       'storage_anchor_slack_max_mwh':3.,'rules_satisfied_no_slack':False}}
    at=render(summary,{},tmp_path)
    assert not at.exception and not at.error
    assert any('软目标偏差不等于物理违规' in x.value for x in at.info)


def test_real_gap1_saved_outputs_if_available():
    directory=Path(os.environ.get('IEEE39_SAVED_GAP1_RUN', str(ROOT.parent/'full96_gap1')))
    if not (directory/'summary.json').exists():pytest.skip('local validation run not distributed')
    summary=json.loads((directory/'summary.json').read_text())
    messages=result_quality_messages(summary,summary['da_summary'],directory=directory)
    assert any('日前求解间隙 2.397%' in text and '超过' in text for _,text in messages)
    assert any('实时已执行线路越限' in text and '8.0277 MW' in text for _,text in messages)
    assert any('0.2686 MW' in text for _,text in messages)
    assert any('96/96' in text and '0.994%' in text for _,text in messages)


@pytest.mark.parametrize('page',['pages/2_运行.py','pages/3_结果.py'])
def test_real_gap1_alerts_visible_in_both_pages(monkeypatch,tmp_path,page):
    import hashlib
    import common
    from smm import scenario
    directory=Path(os.environ.get('IEEE39_SAVED_GAP1_RUN', str(ROOT.parent/'full96_gap1')))
    if not (directory/'summary.json').exists():pytest.skip('local validation run not distributed')
    watched=['summary.json','stage_diagnostics.json','rt_lines.csv','rt_reserve_system.csv','meta.json']
    before={name:hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in watched}
    monkeypatch.setattr(scenario,'SCEN_DIR',tmp_path/'scenarios')
    monkeypatch.setattr(scenario,'RUNS_DIR',tmp_path/'runs')
    scenario.ensure_default()
    meta=json.loads((directory/'meta.json').read_text())
    meta.update(dir=str(directory),scenario='真实1%验收',started='')
    monkeypatch.setattr(common,'all_results',lambda:[dict(meta)])
    monkeypatch.setattr(scenario,'list_runs',lambda:[dict(meta)])
    at=AppTest.from_file(str(ROOT/'app/主页.py'),default_timeout=60).run()
    at.switch_page(page).run()
    assert not at.exception,[e.value for e in at.exception]
    assert any('2.397%' in x.value and '1.000%' in x.value for x in at.warning)
    assert any('8.0277 MW' in x.value and 'L2-3' in x.value and '64' in x.value for x in at.error)
    assert any('0.2686 MW' in x.value and '52' in x.value for x in at.error)
    assert not any('完成，可到' in x.value for x in at.success)
    # These core alerts are outside collapsible technical diagnostics.
    assert not any('8.0277 MW' in x.value for e in at.expander for x in e.error)
    assert before=={name:hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in watched}
