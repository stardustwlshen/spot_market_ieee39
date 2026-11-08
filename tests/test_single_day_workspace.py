"""Single-day default: fewer inputs without changing quotes, states or run scope."""
from pathlib import Path
from copy import deepcopy
import json
import sys
from types import SimpleNamespace
import os

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'app'))
PAGE = ROOT/'app/pages/6_单日模拟.py'


def item(elements, label):
    return next(x for x in elements if x.label == label)


@pytest.fixture
def scene(tmp_path, monkeypatch):
    from smm import scenario as sc
    monkeypatch.setattr(sc, 'SCEN_DIR', tmp_path/'scenarios')
    monkeypatch.setattr(sc, 'RUNS_DIR', tmp_path/'runs')
    sc.ensure_default()
    return sc


def app():
    return AppTest.from_file(str(ROOT/'app/主页.py'), default_timeout=60).run()


def step(at, value):
    return item(at.radio, '执行流程').set_value(value).run()


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_home_and_sidebar_default_to_single_day():
    import common
    assert common.PAGES[0][0] == 'pages/6_单日模拟.py'
    assert 'pages/6_单日模拟.py' in (ROOT/'app/主页.py').read_text(encoding='utf-8')
    assert (ROOT/'app/pages/5_出清任务.py').exists()


def test_default_scene_has_no_required_numeric_inputs_and_browsing_is_readonly(scene):
    before = files(scene.SCEN_DIR)
    at = app()
    assert not at.exception
    assert item(at.radio, '执行流程').value == '1 场景'
    assert len(at.number_input) == 0
    assert not any(x.label == '模拟几天' for x in at.number_input)
    for s in ('2 机组', '3 报价', '4 运行', '5 结果', '1 场景'):
        step(at, s)
        assert not at.exception
    assert files(scene.SCEN_DIR) == before
    assert not scene.RUNS_DIR.exists()
    assert not (scene.SCEN_DIR.parent/'series_tasks').exists()


def test_initial_state_is_explicit_and_editable_not_measured(scene):
    at = step(app(), '2 机组')
    assert not at.exception
    assert any('不自动接续前一天' in c.value and '未核验为实测' in c.value for c in at.caption)
    assert item(at.selectbox, '当前机组').value == 'C1'
    assert any('初始状态 / 在线' in str(frame.value) for frame in at.dataframe)
    item(at.checkbox, '加载全网汇总').check().run()
    rows = item(at.expander, '全网资源汇总与日初状态').dataframe[-1].value
    assert {'火电','水电','电池','抽蓄'} <= set(rows['类型'])
    assert not item(at.expander, '编辑或新增机组').proto.expanded
    item(at.button, '核对技术参数').click().run()
    assert not at.exception
    assert item(at.radio, '执行流程').disabled
    item(at.button, '取消编辑').click().run()
    assert not at.exception and not item(at.radio, '执行流程').disabled


def test_quote_editor_is_direct_and_preserves_exact_current_source(scene):
    at = step(app(), '3 报价')
    assert not at.exception
    assert item(at.selectbox, '模拟机组').value == 'C1'
    assert not any(x.label == '编辑机组报价' for x in at.checkbox)
    assert any('当前报价：' in c.value for c in at.caption)
    original = deepcopy(at.session_state['quote_current:默认'])
    item(at.number_input, '空载报价（元/h）').set_value(6100.).run()
    expected = deepcopy(original); expected['units']['C1']['noload_cost'] = 6100.
    assert at.session_state['quote_current:默认'] == expected
    step(at, '4 运行'); step(at, '3 报价')
    assert not at.exception
    assert at.session_state['quote_current:默认'] == expected
    assert item(at.number_input, '空载报价（元/h）').value == 6100.


def test_full_day_launch_uses_exact_quote_and_navigates_to_monitor(scene, monkeypatch):
    import single_day_workspace as ui
    calls=[]
    def start(*args, **kwargs):
        calls.append((args, deepcopy(kwargs)))
        return scene.RUNS_DIR/'new'
    monkeypatch.setattr(ui, 'start_run', start)
    at = step(app(), '4 运行')
    expected = deepcopy(at.session_state['quote_current:默认'])
    assert not item(at.expander, '高级设置').proto.expanded
    item(at.button, '开始单日出清').click().run()
    assert not at.exception
    assert len(calls) == 1
    assert calls[0][0] == ('默认', 'full', expected['version'], None)
    assert calls[0][1]['bids'] == expected
    assert item(at.radio, '执行流程').value == '5 结果'


def test_full_day_real_launch_freezes_current_quote(scene, monkeypatch):
    import common
    real=common.subprocess.Popen
    calls=[]
    def spawn(*args, **kwargs):
        if args and args[0][0] == 'git':
            return real(*args, **kwargs)
        calls.append((args,kwargs))
        return SimpleNamespace(pid=os.getpid())
    monkeypatch.setattr(common.subprocess, 'Popen', spawn)
    at=step(app(),'3 报价')
    item(at.number_input,'空载报价（元/h）').set_value(6100.).run()
    expected=deepcopy(at.session_state['quote_current:默认'])
    step(at,'4 运行')
    item(at.button,'开始单日出清').click().run()
    assert not at.exception and len(calls)==1
    directory=Path(at.session_state['watch_run'])
    assert json.loads((directory/'submitted_bids.json').read_text(encoding='utf-8'))==expected
    assert json.loads((directory/'meta.json').read_text(encoding='utf-8'))['periods'] is None
    step(at,'4 运行')
    assert item(at.button,'开始单日出清').disabled
    assert item(at.selectbox,'单日场景').disabled


@pytest.mark.parametrize('mode,short,expected', [('da',False,None),('full',True,8)])
def test_debug_scope_is_visible_and_explicit(scene, monkeypatch, mode, short, expected):
    import single_day_workspace as ui
    calls=[]
    monkeypatch.setattr(ui,'start_run',lambda *args,**kwargs: calls.append(args) or scene.RUNS_DIR/'new')
    at=step(app(),'4 运行')
    item(at.radio,'运行范围').set_value(mode)
    item(at.checkbox,'仅试跑前几个实时时段').set_value(short).run()
    assert not at.exception
    assert any('不是完整日结算' in w.value or '没有实时执行' in w.value for w in at.warning)
    item(at.button,'开始单日出清').click().run()
    assert calls[0][1] == mode and calls[0][3] == expected


def test_corrupt_quote_is_not_replaced_by_defaults(scene):
    root=scene.scenario_path('默认')
    (root/'current_quote.json').write_text('{broken',encoding='utf-8')
    before=files(root)
    at=step(app(),'4 运行')
    assert not at.exception
    assert item(at.button,'开始单日出清').disabled
    assert any('不会改用默认报价' in e.value for e in at.error)
    assert files(root)==before


def test_bad_initial_state_does_not_claim_ready(scene):
    data=scene.read_yaml('默认','units')
    data['storage'][0]['soc0']=9
    scene.write_yaml('默认','units',data)
    at=step(app(),'4 运行')
    assert not at.exception
    assert not any(b.label=='开始单日出清' and not b.disabled for b in at.button)
    assert any('配置不能读取' in e.value or '检查未通过' in e.value for e in at.error)


def test_intraday_description_follows_configuration_without_changing_it(scene):
    path=scene.scenario_path('默认')/'config/modes.yaml'
    import yaml
    data=yaml.safe_load(path.read_text(encoding='utf-8'))
    data['MKT']['intraday']['enabled']=True
    path.write_text(yaml.safe_dump(data,allow_unicode=True,sort_keys=False),encoding='utf-8')
    before=path.read_bytes()
    at=step(app(),'4 运行')
    assert not at.exception
    assert any('日内已启用' in c.value and '不单独结算' in c.value for c in at.caption)
    assert path.read_bytes()==before


def test_advanced_editor_locks_navigation_until_cancel(scene):
    at=step(app(),'4 运行')
    item(at.button,'编辑高级参数').click().run()
    assert not at.exception
    assert item(at.radio,'执行流程').disabled
    assert item(at.button,'开始单日出清').disabled
    item(at.button,'取消高级参数编辑').click().run()
    assert not at.exception
    assert not item(at.radio,'执行流程').disabled


def test_result_monitor_does_not_depend_on_current_config(scene):
    root=scene.scenario_path('默认')
    (root/'config/units.yaml').write_text('{broken',encoding='utf-8')
    at=step(app(),'5 结果')
    assert not at.exception
    assert any('暂无运行记录' in x.value for x in at.info)
    assert not any('配置不能读取' in e.value for e in at.error)


def test_existing_advanced_editor_routes_to_editable_step(scene):
    at=app()
    at.switch_page('pages/2_运行.py').run()
    item(at.button,'编辑高级参数').click().run()
    at.switch_page('pages/6_单日模拟.py').run()
    assert not at.exception
    assert item(at.radio,'执行流程').value=='4 运行'
    assert item(at.radio,'执行流程').disabled
    item(at.button,'取消高级参数编辑').click().run()
    assert not at.exception and not item(at.radio,'执行流程').disabled


def test_concurrent_quote_write_blocks_launch(scene,monkeypatch):
    import single_day_workspace as ui
    calls=[]
    monkeypatch.setattr(ui,'start_run',lambda *a,**k: calls.append(a))
    at=step(app(),'3 报价')
    item(at.number_input, '空载报价（元/h）').set_value(6100.).run()
    step(at,'4 运行')
    p=scene.scenario_path('默认')/'current_quote.json'
    other=json.loads(p.read_text(encoding='utf-8'))
    other['draft']['units']['C1']['noload_cost']=6001
    p.write_text(json.dumps(other,ensure_ascii=False),encoding='utf-8')
    item(at.button,'开始单日出清').click().run()
    assert not at.exception and not calls
    assert any('启动前输入校验未通过' in e.value for e in at.error)
    assert json.loads(p.read_text(encoding='utf-8'))['draft']['units']['C1']['noload_cost']==6001


def test_next_keeps_inputs_and_does_not_require_editors(scene):
    before=files(scene.SCEN_DIR)
    at=app()
    item(at.button,'下一步').click().run()
    assert not at.exception
    assert item(at.radio,'执行流程').value=='2 机组'
    step(at, '4 运行')
    assert not item(at.button,'开始单日出清').disabled
    assert files(scene.SCEN_DIR)==before
    assert not scene.RUNS_DIR.exists()


def test_common_conditions_are_direct_without_rare_creation_or_rebase_tools(scene):
    at=app()
    assert not at.exception
    for label in ('水文', '风电', '光伏', '负荷'):
        assert item(at.selectbox, label)
    assert not any(x.label in ('显示场景调整工具', '沿用当前输入，直接去运行') for x in at.button)
    assert not any(x.label in ('从新版默认配置新建场景', '场景基准与特殊条件', '当前输入作为新基准')
                   for x in [*at.expander, *at.checkbox, *at.button])
    assert not at.number_input
    assert not any('日前计划' in x.value for x in at.markdown)


def test_condition_selection_is_readonly_until_next_then_preserves_source(scene):
    original=files(scene.SCEN_DIR)
    at=app()
    source=deepcopy(at.session_state['quote_current:默认'])
    item(at.selectbox,'水文').set_value('dry')
    item(at.selectbox,'风电').set_value(1.2)
    item(at.selectbox,'光伏').set_value(.8)
    item(at.selectbox,'负荷').set_value(.9).run()
    assert not at.exception
    assert files(scene.SCEN_DIR)==original
    item(at.button,'下一步').click().run()
    assert not at.exception
    assert item(at.radio,'执行流程').value=='2 机组'
    name=at.session_state['scenario']
    assert name != '默认'
    assert {k:v for k,v in files(scene.SCEN_DIR).items() if Path(k).parts[0]=='默认'} == original
    manifest=json.loads((scene.scenario_path(name)/'config/typical_scenario.json').read_text(encoding='utf-8'))
    assert (manifest['season'],manifest['wind_multiplier'],manifest['pv_multiplier'],manifest['load_multiplier']) == ('dry',1.2,.8,.9)
    current=at.session_state['quote_current:'+name]
    for uid,bid in source['units'].items():
        assert {k:v for k,v in current['units'][uid].items() if k != 'avail_pmax'} == {k:v for k,v in bid.items() if k != 'avail_pmax'}
    step(at, '1 场景')
    before=files(scene.SCEN_DIR)
    item(at.button,'下一步').click().run()
    assert not at.exception and files(scene.SCEN_DIR)==before


def test_scene_condition_multiplier_does_not_compound(scene):
    at=app()
    item(at.selectbox,'风电').set_value(1.2).run()
    item(at.button,'下一步').click().run()
    assert not at.exception
    step(at, '1 场景')
    assert item(at.selectbox,'风电').value==1.2
    item(at.selectbox,'光伏').set_value(.8).run()
    item(at.button,'下一步').click().run()
    assert not at.exception
    manifest=json.loads((scene.scenario_path(at.session_state['scenario'])/'config/typical_scenario.json').read_text(encoding='utf-8'))
    assert manifest['wind_multiplier']==1.2 and manifest['pv_multiplier']==.8
    assert manifest['baseline_name']=='默认'


def test_condition_availability_change_is_disclosed(scene):
    from smm.config import load_config
    from smm.bids import default_bids
    cfg=load_config(root=scene.scenario_path('默认'))
    bids=default_bids(cfg)
    for u in cfg.units['renewable']:
        bids['units'][u['id']]['avail_pmax']=[float(u['cap'])*.5]*cfg.T
    record=dict(schema=1,draft=bids,baseline=deepcopy(bids),initial_latest=None,basis_label='自定义',history={},manual=[])
    (cfg.root/'current_quote.json').write_text(json.dumps(record,ensure_ascii=False),encoding='utf-8')
    at=app()
    item(at.selectbox,'风电').set_value(.8).run()
    assert not at.exception
    assert any('日前申报可用出力' in w.value and '价格与分段量不变' in w.value for w in at.warning)
    before=files(scene.SCEN_DIR)
    step(at,'2 机组')
    assert files(scene.SCEN_DIR)==before


def test_only_da_has_no_realtime_or_settlement_stage(scene):
    at=step(app(),'4 运行')
    item(at.radio,'运行范围').set_value('da').run()
    assert not at.exception
    stages=[x.value for x in at.markdown if x.value.startswith('**日前计划')]
    assert stages==['**日前计划**']
    assert any('没有实时执行' in w.value for w in at.warning)


def test_quote_edit_hides_summary_and_rare_tools_not_edits(scene):
    at=step(app(),'3 报价')
    assert not at.exception
    assert not item(at.expander,'全部机组报价概览').proto.expanded
    assert not item(at.expander,'恢复与重置').proto.expanded
    assert not item(at.expander,'生成模拟报价').proto.expanded
    item(at.number_input,'空载报价（元/h）').set_value(6234.).run()
    assert at.session_state['quote_current:默认']['units']['C1']['noload_cost']==6234.
    step(at,'2 机组'); step(at,'3 报价')
    assert item(at.number_input,'空载报价（元/h）').value==6234.


def test_compact_unit_editor_asks_bus_only_in_edit_form(scene):
    at=step(app(),'2 机组')
    assert not any(x.label=='接入节点' for x in at.selectbox)
    item(at.button,'核对技术参数').click().run()
    assert not at.exception
    assert item(at.radio,'执行流程').disabled
    item(at.button,'取消编辑').click().run()
    assert not at.exception


def test_unapplied_conditions_cannot_be_silently_bypassed(scene, monkeypatch):
    import single_day_workspace as ui
    calls=[]
    monkeypatch.setattr(ui,'start_run',lambda *a,**k:calls.append(a))
    at=app(); before=files(scene.SCEN_DIR)
    item(at.selectbox,'风电').set_value(1.2).run()
    step(at,'4 运行')
    assert not at.exception
    assert any('场景条件尚未应用' in w.value for w in at.warning)
    assert item(at.button,'开始单日出清').disabled
    assert files(scene.SCEN_DIR)==before and not calls
    item(at.button,'返回场景').click().run()
    item(at.selectbox,'风电').set_value(1.).run()
    step(at,'4 运行')
    assert not item(at.button,'开始单日出清').disabled
    assert files(scene.SCEN_DIR)==before


@pytest.mark.parametrize('bad', [[],None,'broken',{'schema':1,'baseline_availability':{},'wind_multiplier':'nan'}])
def test_bad_condition_manifest_does_not_crash_or_replace_it(scene,bad):
    root=scene.scenario_path('默认');path=root/'config/typical_scenario.json'
    path.write_text(json.dumps(bad),encoding='utf-8');before=files(root)
    at=app()
    assert not at.exception
    assert any('场景条件暂不能调整' in e.value for e in at.error)
    assert files(root)==before


def test_full_period_debug_selection_is_labeled_complete(scene):
    at=step(app(),'4 运行')
    item(at.checkbox,'仅试跑前几个实时时段').set_value(True).run()
    item(at.number_input,'实时时段数').set_value(96).run()
    assert not at.exception
    assert not any('不是完整日结算' in w.value for w in at.warning)
    assert any('完整单日' in c.value for c in at.caption)


def test_capacity_display_uses_chinese_na_without_changing_values(scene):
    import pandas as pd
    from pandas.testing import assert_frame_equal
    from smm.config import load_config
    from unit_input_view import resource_capacity_rows
    cfg = load_config(root=scene.scenario_path('默认'))
    expected = pd.DataFrame(resource_capacity_rows(cfg))
    before = files(scene.SCEN_DIR)
    at = step(app(), '2 机组')
    assert not at.exception
    item(at.checkbox, '加载全网汇总').check().run()
    table = item(at.expander, '全网资源汇总与日初状态').dataframe[0]
    displayed = table.value
    energy_columns = ('储能额定能量（MWh）', '抽蓄有效库容发电容量（MWh）')
    assert_frame_equal(displayed.drop(columns=list(energy_columns)),
                       expected.drop(columns=list(energy_columns)), check_exact=True)
    for column in energy_columns:
        missing = expected[column].isna()
        assert missing.any()
        assert set(displayed.loc[missing, column]) == {'不适用'}
        assert displayed.loc[~missing, column].map(float).tolist() == expected.loc[~missing, column].tolist()
    assert_frame_equal(pd.DataFrame(resource_capacity_rows(cfg)), expected, check_exact=True)
    assert json.loads(table.proto.columns)['状态']['width'] == 'medium'
    assert files(scene.SCEN_DIR) == before



def failed_run(scene, status='failed'):
    d=scene.RUNS_DIR/'默认'/'old_failed';d.mkdir(parents=True)
    (d/'meta.json').write_text(json.dumps(dict(scenario='默认', mode='full', status=status,
        bid_version=1, periods=None, started='2026-11-08T11:50:19', error='admission rejected')), encoding='utf-8')
    (d/'scenario_snapshot').mkdir();(d/'scenario_snapshot'/'original.txt').write_text('frozen old input')
    return d


def test_failed_run_retry_uses_current_inputs_and_new_directory(scene, monkeypatch):
    import common
    from smm.process_control import process_identity
    old=failed_run(scene);before=files(old)
    at=step(app(),'3 报价')
    item(at.number_input, '空载报价（元/h）').set_value(6102.).run()
    expected=deepcopy(at.session_state['quote_current:默认'])
    step(at,'5 结果')
    assert any('当前场景' in c.value and '当前已保存报价' in c.value and '原快照保留' in c.value for c in at.caption)
    real=common.subprocess.Popen
    calls=[]
    def spawn(*args, **kwargs):
        if args and args[0][0]=='git': return real(*args, **kwargs)
        calls.append((args, kwargs));return SimpleNamespace(pid=os.getpid())
    monkeypatch.setattr(common.subprocess,'Popen',spawn)
    item(at.button,'按当前输入重新计算').click().run()
    assert not at.exception
    assert len(calls)==1
    new=Path(at.session_state['watch_run'])
    assert new != old and at.session_state['run_sel']==str(new)
    assert json.loads((new/'submitted_bids.json').read_text(encoding='utf-8')) == expected
    assert files(old)==before
    # An old failure cannot offer a second launch while the new worker owns admission.
    item(at.selectbox,'查看运行').set_value(str(old)).run()
    assert item(at.button,'按当前输入重新计算').disabled
    at.run(); assert len(calls)==1


@pytest.mark.parametrize('block', ['quote', 'conditions', 'physical'])
def test_retry_revalidates_current_inputs_without_touching_history(scene, block):
    old=failed_run(scene);before=files(old)
    at=step(app(),'5 结果')
    if block=='quote':
        (scene.scenario_path('默认')/'current_quote.json').write_text('{broken', encoding='utf-8')
        # A fresh session must not rescue the bad saved file from a cached draft.
        at=step(app(),'5 结果')
    elif block=='physical':
        data=scene.read_yaml('默认','units');data['storage'][0]['soc0']=9
        scene.write_yaml('默认','units',data)
    else:
        at.session_state['day_condition_pending:默认']=True
        at.run()
        assert item(at.button,'按当前输入重新计算').disabled
        assert files(old)==before
        return
    item(at.button,'按当前输入重新计算').click().run()
    assert not at.exception and at.error
    assert files(old)==before
    assert len(list(scene.RUNS_DIR.glob('*/*/meta.json')))==1


@pytest.mark.parametrize('incompatible', ['fixed_load', 'regulation'])
def test_retry_keeps_primary_start_compatibility_guards(scene, incompatible):
    old=failed_run(scene);before=files(old)
    at=step(app(),'5 结果')
    if incompatible=='fixed_load':
        data=scene.read_yaml('默认','base');data['tie_line_mode']='fixed_load'
        scene.write_yaml('默认','base',data)
    else:
        data=scene.read_yaml('默认','modes');data['MKT']['regulation']['enabled']=True
        scene.write_yaml('默认','modes',data)
    item(at.button,'按当前输入重新计算').click().run()
    assert not at.exception and at.error
    assert files(old)==before
    assert len(list(scene.RUNS_DIR.glob('*/*/meta.json')))==1


def test_admission_failure_card_identifies_pre_solver_stage(scene):
    old=failed_run(scene)
    (old/'run.log').write_text('smm.run_admission.RunDirectoryError: 运行预留中已有结果或未知文件，禁止覆盖', encoding='utf-8')
    at=step(app(),'5 结果')
    assert not at.exception
    assert any('尚未进入出清求解' in e.value for e in at.error)
