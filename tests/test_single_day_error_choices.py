"""Error recipes are optional, explicit, independently applied and source-safe."""
from copy import deepcopy
import json

import numpy as np
import pytest

from test_single_day_workspace import scene, app, item, step, files


def profiles(sc, name):
    from smm.config import load_config
    from smm.forecast import generate_profiles, apply_profile_overrides
    cfg = load_config(root=sc.scenario_path(name))
    return cfg, apply_profile_overrides(cfg, generate_profiles(cfg, cfg.base['seed']))


def test_three_error_choices_are_direct_optional_and_readonly(scene):
    before = files(scene.SCEN_DIR)
    at = app()
    assert not at.exception and not at.number_input
    for label in ('负荷预测误差', '风电预测误差', '光伏预测误差'):
        assert item(at.selectbox, label).value == 'configured'
    details = item(at.expander, '误差方法与参数')
    assert not details.proto.expanded
    assert any('未按历史数据标定' in c.value for c in details.caption)
    detail_captions = {c.value for c in details.caption}
    assert not any('未按历史数据标定' in c.value for c in at.caption if c.value not in detail_captions)
    assert not any('误差情景为' in c.value for c in at.caption)
    for label in ('负荷预测误差', '风电预测误差', '光伏预测误差'):
        assert '上传预测和实际曲线保持不变' in item(at.selectbox, label).proto.help
    item(at.button, '下一步').click().run()
    assert not at.exception and files(scene.SCEN_DIR) == before


@pytest.mark.parametrize('kind,label,prefix', [('load','负荷预测误差','load'),
                                            ('wind','风电预测误差','W1'),
                                            ('pv','光伏预测误差','PV1')])
def test_each_error_choice_applies_only_on_next_preserves_source(scene, kind, label, prefix):
    cfg0, original = profiles(scene, '默认')
    before = files(scene.SCEN_DIR)
    at = app()
    quote = deepcopy(at.session_state['quote_current:默认'])
    item(at.selectbox, label).set_value('underestimate').run()
    assert not at.exception and files(scene.SCEN_DIR) == before
    item(at.button, '下一步').click().run()
    assert not at.exception
    name = at.session_state['scenario']
    assert name != '默认'
    cfg, result = profiles(scene, name)
    assert cfg.base['forecast']['error_scenarios'][kind] == 'underestimate'
    original_after = {k: v for k, v in files(scene.SCEN_DIR).items() if k.startswith('默认\\') or k.startswith('默认/')}
    assert original_after == before
    for column in original:
        if column.endswith('_actual'):
            np.testing.assert_allclose(result[column], original[column], rtol=0, atol=1e-12)
    for stage in ('da', 'id', 'rt'):
        assert not np.allclose(result[f'{prefix}_{stage}'], original[f'{prefix}_{stage}'])
    assert at.session_state['quote_current:'+name]['units'] == quote['units']
    step(at, '1 场景')
    assert item(at.selectbox, label).value == 'underestimate'
    saved = files(scene.SCEN_DIR)
    item(at.button, '下一步').click().run()
    assert not at.exception and files(scene.SCEN_DIR) == saved


def test_unapplied_error_selection_cannot_run_old_inputs(scene, monkeypatch):
    import single_day_workspace as ui
    calls = []
    monkeypatch.setattr(ui, 'start_run', lambda *a, **k: calls.append(a))
    at = app()
    item(at.selectbox, '负荷预测误差').set_value('lag').run()
    step(at, '4 运行')
    assert not at.exception and item(at.button, '开始单日出清').disabled
    item(at.button, '返回场景').click().run()
    assert item(at.selectbox, '负荷预测误差').value == 'lag'
    item(at.selectbox, '负荷预测误差').set_value('configured').run()
    step(at, '4 运行')
    assert not item(at.button, '开始单日出清').disabled and not calls


def test_uploaded_forecast_limit_is_visible_before_apply(scene):
    cfg, original = profiles(scene, '默认')
    upload = cfg.root/'data/profile_overrides.csv'
    upload.parent.mkdir(parents=True, exist_ok=True)
    original[['load_da']].to_csv(upload, index=False)
    before = upload.read_bytes()
    at = app()
    item(at.selectbox, '负荷预测误差').set_value('overestimate').run()
    assert not at.exception
    assert any('负荷·日前' in w.value and ('不' in w.value or '保留' in w.value) for w in at.warning)
    assert not any('load_da' in w.value for w in at.warning)
    assert upload.read_bytes() == before


def test_malformed_error_configuration_blocks_apply_without_replacing_it(scene):
    data = scene.read_yaml('默认', 'base')
    data.setdefault('forecast', {})['error_scenarios'] = {'load': 'made_up'}
    scene.write_yaml('默认', 'base', data)
    before = files(scene.SCEN_DIR)
    at = app()
    assert not at.exception
    assert any('预测误差配置不能读取' in e.value for e in at.error)
    assert item(at.button, '下一步').disabled
    assert files(scene.SCEN_DIR) == before


@pytest.mark.parametrize('choice', ['normal', 'overestimate', 'underestimate', 'lag', 'volatile'])
def test_error_details_remain_optional_with_baselines_and_source_columns(scene, choice):
    before = files(scene.SCEN_DIR)
    at = app()
    for label in ('负荷预测误差', '风电预测误差', '光伏预测误差'):
        item(at.selectbox, label).set_value(choice)
    at.run()
    assert not at.exception
    details = item(at.expander, '误差方法与参数')
    assert not details.proto.expanded
    table = details.dataframe[0].value
    assert {'日前幅度基准', '日内幅度基准', '实时幅度基准', '15分钟持续性基准'} <= set(table.columns)
    assert set(table['对象']) == {'负荷', '风电', '光伏'}
    assert any('实际误差可能与上表幅度不同' in c.value for c in details.caption)
    assert len(details.dataframe) >= 2  # Keep parameter baselines and preview error summary.
    assert len(item(at.expander, "曲线数据与来源").dataframe) >= 2
    assert not any('误差情景为' in c.value for c in at.caption)
    at.run()
    assert not at.exception and not item(at.expander, '误差方法与参数').proto.expanded
    assert files(scene.SCEN_DIR) == before
