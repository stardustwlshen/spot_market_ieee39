"""Real Streamlit form transitions and values, not source-string assertions."""
import copy
import json
import sys

import pytest
from streamlit.testing.v1 import AppTest

from smm.config import ROOT,load_config
from smm.unit_editor import catalog
from test_unit_editor import scene,input_bytes
from test_unit_editor_ui import item,app as unit_app
sys.path.insert(0,str(ROOT/'app'))


def app(scene,disabled=False):
    return AppTest.from_string(f'''
from pathlib import Path
from smm.config import load_config
from advanced_parameters import render_advanced_parameters,render_effective_parameters
cfg=load_config(root=Path({str(scene)!r}))
render_advanced_parameters(cfg,'测试',disabled={disabled!r})
render_effective_parameters(cfg)
''',default_timeout=60).run()


def test_form_check_save_reload_cancel_and_price_override(scene):
    at=app(scene);assert not at.exception
    item(at.button,'编辑高级参数').click().run();assert not at.exception
    before=input_bytes(scene)
    item(at.number_input,'日前求解预算（秒）').set_value(37.)
    item(at.number_input,'全局报价下限（元/MWh）').set_value(-100.)
    item(at.number_input,'出清结算下限（元/MWh）').set_value(-20.)
    item(at.checkbox,'设置煤电覆盖下限（元/MWh）').check()
    item(at.number_input,'煤电覆盖下限（元/MWh）').set_value(-70.)
    item(at.button,'检查高级参数').click().run()
    assert not at.exception and not at.error
    assert input_bytes(scene)==before
    item(at.button,'保存高级参数').click().run()
    assert not at.exception and not at.error
    cfg=load_config(root=scene)
    assert cfg.base['solver']['time_limit_s']==37
    assert cfg.mode['price_limits']['clear_min']==-20
    assert cfg.mode['bid_rules']['by_type']['coal']['price_min']==-70
    at=app(scene)
    item(at.button,'编辑高级参数').click().run()
    assert item(at.number_input,'日前求解预算（秒）').value==37.
    saved=input_bytes(scene)
    item(at.number_input,'日前求解预算（秒）').set_value(99.)
    item(at.button,'取消高级参数编辑').click().run()
    assert input_bytes(scene)==saved
    item(at.button,'编辑高级参数').click().run()
    assert item(at.number_input,'日前求解预算（秒）').value==37.


def test_invalid_and_stale_save_remain_in_editor(scene):
    at=app(scene);item(at.button,'编辑高级参数').click().run()
    before=input_bytes(scene)
    item(at.number_input,'区内负荷倍率').set_value(-1.)
    item(at.button,'保存高级参数').click().run()
    assert not at.exception and at.error and input_bytes(scene)==before
    item(at.number_input,'区内负荷倍率').set_value(.7)
    path=scene/'config/base.yaml';path.write_bytes(path.read_bytes()+b'\n# concurrent\n')
    before=input_bytes(scene)
    item(at.button,'保存高级参数').click().run()
    assert not at.exception and any('另一页面' in x.value for x in at.error)
    assert input_bytes(scene)==before


def test_busy_disables_editor_and_pending_save(scene):
    at=app(scene,True)
    assert item(at.button,'编辑高级参数').disabled


def test_every_unit_field_help_and_hydro_unused_readonly(scene):
    # This regression intentionally opens an old, explicitly aggregate scene.
    import yaml
    path=scene/'config/units.yaml';units=yaml.safe_load(path.read_text())
    for h in units['hydro']:h.pop('commitment',None)
    path.write_text(yaml.safe_dump(units,allow_unicode=True))
    from unit_parameter_help import HELP,field_help,field_readonly
    def walk(value,path=()):
        if isinstance(value,dict):
            for key,child in value.items():yield from walk(child,path+(key,))
        else:yield path,value
    for family,unit in catalog(scene):
        for path,value in walk(unit):
            if path[-1] in ('id','type'):continue
            assert field_help(family,path,unit)
            if path[-1] not in HELP:assert field_readonly(family,path,unit)
    at=unit_app(scene)
    item(at.selectbox,'要编辑的机组').set_value('H1').run()
    item(at.button,'核对技术参数').click().run()
    assert item(at.number_input,'最小开机时间（h）').disabled
    assert item(at.number_input,'最小停机时间（h）').disabled
    assert '未实现' in item(at.number_input,'最小开机时间（h）').proto.help
    assert '耗水率' in item(at.number_input,'耗水率（m³/s/MW）').label
    assert item(at.number_input,'耗水率（m³/s/MW）').proto.help


def test_effective_quote_source_is_real_not_template(scene):
    from unit_parameter_help import effective_quote_rows
    from smm.unit_editor import _current_record
    cfg=load_config(root=scene);record=_current_record(scene,cfg)
    record['draft']['units']['ES1']['soc_end']=.6
    (scene/'current_quote.json').write_text(json.dumps(record))
    unit=next(u for u in cfg.units['storage'] if u['id']=='ES1')
    assert unit['soc_end_bid']==.5
    row=next(r for r in effective_quote_rows(cfg,unit,'storage') if r['参数']=='日前日末SOC')
    assert row['当前入模值']=='0.6' and row['来源']=='当前报价'


def test_ui_saved_parameters_freeze_and_complete_real_short_da_rt(scene,tmp_path):
    """Two-node 8-period integration; not an IEEE39 full-day certificate."""
    import shutil
    import yaml
    import numpy as np
    from smm.unit_editor import unit_template,prepare_unit_edit,apply_unit_edit
    from smm.network import build_network
    from smm.forecast import generate_profiles
    from smm.case import build_da_case
    from smm.full import run_full
    from smm.live_runner import freeze_inputs
    from smm.scuc import _solver
    shutil.rmtree(scene/'bids');(scene/'bids').mkdir()
    (scene/'config/units.yaml').write_text(yaml.safe_dump({k:[] for k in ('thermal','hydro','storage','pumped','renewable','tie')}))
    base=yaml.safe_load((scene/'config/base.yaml').read_bytes())
    base.update(periods=8,load_scale=.02,line_scale={},line_capacity_multiplier=1.)
    base['solver'].update(time_limit_s=20,mip_rel_gap=0.)
    (scene/'config/base.yaml').write_text(yaml.safe_dump(base))
    modes=yaml.safe_load((scene/'config/modes.yaml').read_bytes())
    modes['MKT']['reserve'].update(up_requirement_mode='formula',include_ctg=False,rho_L=0.,rho_RE=0.,rho_L_dn=0.,rho_RE_dn=0.)
    modes['MKT']['intraday']['enabled']=False
    (scene/'config/modes.yaml').write_text(yaml.safe_dump(modes))
    (scene/'data/case39/buses.csv').write_text('bus,load_mw\n31,50\n32,50\n')
    (scene/'data/case39/branches.csv').write_text('branch_id,from_bus,to_bus,x_pu,rate_mw\nTEST,31,32,0.1,1000\n')
    for family,kind,uid,capacity in [('thermal','gas_gt','EDIT_G',300.),('storage','storage','EDIT_E',30.)]:
        apply_unit_edit(prepare_unit_edit(scene,family,unit_template(kind,uid,31,capacity,energy=60.)))
    before_quote=(scene/'current_quote.json').read_bytes()
    at=app(scene);item(at.button,'编辑高级参数').click().run()
    for label,value in [('区内负荷倍率',.025),('随机种子',71),('TEST 限额倍率',.8),
                        ('日前求解预算（秒）',17.),('实时求解预算（秒）',11.),
                        ('实时相对间隙',0.),('上备用负荷比例',.04),
                        ('出清结算下限（元/MWh）',0.),('出清结算上限（元/MWh）',1.)]:
        item(at.number_input,label).set_value(value)
    item(at.button,'保存高级参数').click().run()
    assert not at.exception and not at.error
    assert (scene/'current_quote.json').read_bytes()==before_quote
    cfg=load_config(root=scene)
    bids=json.loads(before_quote)['draft']
    snap=freeze_inputs(scene,tmp_path/'run',bids=bids)
    frozen=load_config(root=snap)
    assert frozen.base['seed']==71 and frozen.mode['realtime']['time_limit_s']==11.
    assert _solver(frozen).highs_options['time_limit']==17.
    case=build_da_case(frozen,build_network(frozen),generate_profiles(frozen),bids)
    assert case.net.branches.rate_mw.iloc[0]==800.
    assert np.allclose(case.R_up,sum(case.load_bus.values())*.04)
    result=run_full(frozen,case,log=lambda *a:None)
    assert len(result['rr'].rt)==8
    raw=result['da'].prices['lmp'];clamped=result['da'].prices['lmp_clamped']
    assert (raw>1.).any()  # Original marginal prices remain above the settlement cap.
    assert np.allclose(clamped,np.clip(raw,0.,1.))
    for record in result['rr'].rt:
        assert np.allclose(record['prices']['lmp_clamped'],np.clip(record['prices']['lmp'],0.,1.))
    detail=result['settle']['detail']
    assert detail.lmp_da.max()<=1. and detail.lmp_rt.max()<=1.
    assert np.allclose(detail.total,(detail.q_da*detail.lmp_da+detail.dq*detail.lmp_rt)*frozen.dt)
    assert result['settle']['reconciliation'].residual.abs().max()<1e-6
    assert result['settled'] is False and result['settlement_scope']=='illustrative_prefix'
    report={'scope':'two_node_8_period_UI_save_freeze_DA_RT_not_full_day',
            'seed':frozen.base['seed'],'load_scale':frozen.base['load_scale'],
            'line_limit_mw':float(case.net.branches.rate_mw.iloc[0]),'da_budget_s':17,'rt_budget_s':11,
            'reserve_ratio':.04,'raw_da_lmp_max':float(raw.max()),'clamped_da_max':float(clamped.max()),
            'executed_rt_periods':len(result['rr'].rt),
            'settlement_residual_max':float(result['settle']['reconciliation'].residual.abs().max())}
    print('ADVANCED_EVIDENCE='+json.dumps(report,ensure_ascii=False))


def test_current_soc_bounds_can_be_edited_without_repricing_or_cost_change(scene):
    from bid_workspace import get_scene_working_draft,persist_scene_working_draft
    source=f'''
from pathlib import Path
import streamlit as st
from smm.config import load_config
from quote_simulation import quotation_workspace
cfg=load_config(root=Path({str(scene)!r}))
quotation_workspace(cfg,'测试')
'''
    at=AppTest.from_string(source,default_timeout=60).run()
    item(at.selectbox,'模拟机组').set_value('ES1').run()
    before=json.loads((scene/'current_quote.json').read_bytes())['draft']
    costs=(scene/'config/fixed_costs.yaml').read_bytes()
    item(at.number_input,'申报SOC下限（0–1）').set_value(.15).run()
    assert not at.exception
    after=json.loads((scene/'current_quote.json').read_bytes())['draft']
    expected=copy.deepcopy(before)
    expected['units']['ES1']['soc_min']=.15
    assert after==expected
    assert (scene/'config/fixed_costs.yaml').read_bytes()==costs


def quote_app(scene):
    return AppTest.from_string(f'''
from pathlib import Path
from smm.config import load_config
from quote_simulation import quotation_workspace
cfg=load_config(root=Path({str(scene)!r}))
quotation_workspace(cfg,'测试')
''',default_timeout=60).run()


def test_missing_soc_override_browse_does_not_materialize_defaults(scene):
    from smm.unit_editor import _current_record
    cfg=load_config(root=scene);record=_current_record(scene,cfg)
    record['draft']['units']['ES1'].pop('soc_min')
    record['draft']['units']['ES1'].pop('soc_max')
    (scene/'current_quote.json').write_text(json.dumps(record))
    at=quote_app(scene);item(at.selectbox,'模拟机组').set_value('ES1').run()
    assert item(at.number_input,'申报SOC下限（0–1）').value==.1
    assert item(at.number_input,'申报SOC上限（0–1）').value==.9
    at.run()
    saved=json.loads((scene/'current_quote.json').read_bytes())['draft']
    assert saved==record['draft']
    assert 'soc_min' not in saved['units']['ES1'] and 'soc_max' not in saved['units']['ES1']


@pytest.mark.parametrize('label,value',[
    ('申报SOC下限（0–1）',.05),('申报SOC上限（0–1）',.95),
    ('申报SOC下限（0–1）',.95),('申报SOC上限（0–1）',.4),
])
def test_invalid_soc_declarations_retained_for_repair_not_clipped_or_run(scene,label,value):
    from smm.bids import validate_bids
    at=quote_app(scene);item(at.selectbox,'模拟机组').set_value('ES1').run()
    technical=(scene/'config/units.yaml').read_bytes()
    item(at.number_input,label).set_value(value).run()
    assert not at.exception and any('报价存在违规' in x.value for x in at.error)
    saved=json.loads((scene/'current_quote.json').read_bytes())['draft']
    key='soc_min' if '下限' in label else 'soc_max'
    assert saved['units']['ES1'][key]==value
    assert [x for x in validate_bids(load_config(root=scene),saved) if x.level=='error']
    assert (scene/'config/units.yaml').read_bytes()==technical


def test_initial_soc_is_not_clipped_to_new_declared_band(scene):
    from smm.unit_editor import _current_record
    from smm.scuc import prepare
    from smm.case import build_da_case
    from smm.forecast import generate_profiles
    from smm.network import build_network
    cfg=load_config(root=scene);record=_current_record(scene,cfg)
    record['draft']['units']['ES1']['soc_end']=.6
    (scene/'current_quote.json').write_text(json.dumps(record))
    at=quote_app(scene);item(at.selectbox,'模拟机组').set_value('ES1').run()
    item(at.number_input,'申报SOC下限（0–1）').set_value(.55).run()
    assert not at.exception and not at.error
    bids=json.loads((scene/'current_quote.json').read_bytes())['draft']
    md=prepare(build_da_case(cfg,build_network(cfg),generate_profiles(cfg),bids))
    assert md.storage['ES1']['soc_min']==.55 and md.storage['ES1']['soc0']==.5
    # Existing model explicitly supports actual initial stock outside a tighter
    # declared band. Do not invent a rejection or reset actual stock to the bound.


def test_no_change_advanced_save_preserves_inherited_keys(scene):
    before=input_bytes(scene)
    at=app(scene);item(at.button,'编辑高级参数').click().run()
    item(at.button,'保存高级参数').click().run()
    assert not at.exception and not at.error
    assert input_bytes(scene)==before


def test_inactive_policy_controls_enable_after_explicit_check(scene):
    at=app(scene);item(at.button,'编辑高级参数').click().run()
    assert item(at.number_input,'下备用负荷比例').disabled
    assert item(at.number_input,'日内重算间隔（小时）').disabled
    assert item(at.selectbox,'日前锚点类型').disabled
    item(at.checkbox,'采购下备用').check()
    item(at.checkbox,'启用日内滚动').check()
    item(at.selectbox,'储能抽蓄实时策略').set_value('sced_anchor')
    before=input_bytes(scene)
    item(at.button,'检查高级参数').click().run()
    assert not at.exception and not at.error
    assert not item(at.number_input,'下备用负荷比例').disabled
    assert not item(at.number_input,'日内重算间隔（小时）').disabled
    assert not item(at.selectbox,'日前锚点类型').disabled
    assert input_bytes(scene)==before


def test_unknown_nested_fields_stay_readonly_even_with_familiar_leaf_names():
    from unit_parameter_help import field_readonly,field_help
    assert field_readonly('storage',('legacy','pmax'),{})
    assert field_readonly('storage',('ramp',),{})
    assert '历史附加' in field_help('storage',('legacy','pmax'),{})


@pytest.mark.parametrize('legacy,expected,source',[(201.,'201.0','技术旧申报vup_end'),(None,'200','未申报，回到初始上库')])
def test_pumped_effective_display_preserves_legacy_target_priority(scene,legacy,expected,source):
    from unit_parameter_help import effective_quote_rows
    from smm.unit_editor import _current_record
    cfg=load_config(root=scene);record=_current_record(scene,cfg)
    record['draft']['units']['PS1'].pop('vup_end')
    (scene/'current_quote.json').write_text(json.dumps(record))
    unit=next(u for u in cfg.units['pumped'] if u['id']=='PS1')
    unit['vup_end']=legacy
    row=next(r for r in effective_quote_rows(cfg,unit,'pumped') if r['参数']=='日前日末上库库容')
    assert row['当前入模值']==expected and row['来源']==source
