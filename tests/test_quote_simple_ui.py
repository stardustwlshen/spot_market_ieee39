"""Simple primary quote entry preserves optional random workflows and safeguards."""
import copy
import json

import pytest

from test_input_recovery_ui import scenario, item, open_app, key_for, files


def test_primary_quote_is_manual_plus_one_cost_action_with_optional_details(scenario):
    at=open_app()
    assert not any(widget.label in ('生成方式','叠加随机策略') for widget in [*at.radio,*at.checkbox])
    assert item(at.button,'按成本生成预览')
    for label in ('随机报价（可选）','机组参数与成本（只读）','成本来源与研究假设（可选）','更多申报信息（只读）','报价保存与来源'):
        section=item(at.expander,label)
        assert not section.proto.expanded
    random=item(at.expander,'随机报价（可选）')
    assert item(random.radio,'随机报价依据').options==['当前报价','成本报价']
    assert item(random.number_input,'报价随机种子')
    assert item(random.button,'生成随机报价预览')
    assert any('功率区间' in value.value for value in at.caption)
    assert not scenario.RUNS_DIR.exists()


@pytest.mark.parametrize('basis',['当前报价','成本报价'])
@pytest.mark.parametrize('uid',['C1','CCGT','GT','W1','PV1','H1','ES1','PS1'])
def test_optional_random_previews_each_type_preserve_quantities_and_other_inputs(scenario,uid,basis):
    at=open_app()
    item(at.selectbox,'模拟机组').select(uid).run()
    key='quote_current:默认'
    original=copy.deepcopy(at.session_state[key])
    before=files(scenario.scenario_path('默认'))
    item(at.radio,'随机报价依据').set_value(basis).run()
    item(at.button,'生成随机报价预览').click().run()
    assert not at.exception
    assert key+':preview' in at.session_state, [x.value for x in at.error]
    candidate=copy.deepcopy(at.session_state[key+':preview']['candidate'])
    assert at.session_state[key] == original
    assert all(candidate['units'][u]==data for u,data in original['units'].items() if u!=uid)
    curves=[name for name in ('segments','ch_segments','dis_segments','gen_segments','pump_segments') if name in original['units'][uid]]
    for curve in curves:
        assert [(x['p_start'],x['p_end']) for x in candidate['units'][uid][curve]]==[(x['p_start'],x['p_end']) for x in original['units'][uid][curve]]
    assert candidate['_simulation']['selected_unit_ids']==[uid]
    assert ('_cost_basis' in candidate)==(basis=='成本报价')
    item(at.button,'生成随机报价预览').click().run()
    assert at.session_state[key+':preview']['candidate']==candidate
    item(at.button,'采用预览报价').click().run()
    assert not at.exception
    assert at.session_state[key]==candidate
    assert json.loads((scenario.scenario_path('默认')/'current_quote.json').read_text())['draft']==candidate
    assert files(scenario.scenario_path('默认')) == before
    assert not scenario.RUNS_DIR.exists()


def test_cost_preview_does_not_use_hidden_random_options(scenario):
    at=open_app();key=key_for(at)
    item(at.radio,'随机报价依据').set_value('成本报价').run()
    item(at.number_input,'报价随机种子').set_value(12345).run()
    item(at.button,'按成本生成预览').click().run()
    candidate=copy.deepcopy(at.session_state[key+':preview']['candidate'])
    assert candidate['_cost_basis']['strategy_seed'] is None
    assert '_simulation' not in candidate
    item(at.number_input,'报价随机种子').set_value(12346).run()
    assert not item(at.button,'采用预览报价').disabled
    assert at.session_state[key+':preview']['candidate']==candidate


def test_random_basis_change_makes_preview_stale_without_touching_current_quote(scenario):
    at=open_app();key=key_for(at);original=copy.deepcopy(at.session_state[key])
    item(at.button,'生成随机报价预览').click().run()
    assert not item(at.button,'采用预览报价').disabled
    item(at.radio,'随机报价依据').set_value('成本报价').run()
    assert item(at.button,'采用预览报价').disabled
    assert at.session_state[key]==original


def test_old_session_preview_requires_regeneration(scenario):
    at=open_app();key=key_for(at)
    item(at.button,'按成本生成预览').click().run()
    old=copy.deepcopy(at.session_state[key+':preview']);old['request'].pop('entry')
    at.session_state[key+':preview']=old
    at.run()
    assert item(at.button,'采用预览报价').disabled
    item(at.button,'按成本生成预览').click().run()
    assert not item(at.button,'采用预览报价').disabled
