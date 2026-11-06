"""Compact copy keeps risk, stage boundaries and opt-in behavior visible."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from test_input_recovery_ui import scenario, files, item, open_app, key_for


def outside_expanders(at, kind):
    hidden = {element.value for expander in at.expander for element in getattr(expander, kind)}
    return '\n'.join(element.value for element in getattr(at, kind) if element.value not in hidden)


def test_browsing_compact_workspace_keeps_inputs_and_current_quote(scenario):
    root = scenario.scenario_path('默认')
    before = files(root)
    at = open_app()
    draft_key = key_for(at)
    draft = deepcopy(at.session_state[draft_key])
    assert '研究成本（非实测）' in outside_expanders(at, 'caption')
    assert '不是市场规则或结算付款' in outside_expanders(at, 'caption')
    assert '不保证日末偏差幅度' in outside_expanders(at, 'caption')
    details = item(at.expander, '价值法与日末目标说明')
    assert not details.proto.expanded
    assert any('不是硬目标失败后的应急兜底' in x.value for x in details.caption)
    assert not any('观察整个市场' in x.value or '也可按成本生成报价' in x.value for x in at.caption)
    for uid in ('H1', 'ES1', 'PS1', 'C1'):
        item(at.selectbox, '模拟机组').select(uid).run()
        assert not at.exception
    at.run()  # Repeated page visits cannot apply a policy, quote or input migration.
    assert at.session_state[draft_key] == draft
    assert files(root) == before
    assert not scenario.RUNS_DIR.exists()


@pytest.mark.parametrize('policy', [
    {'mode': 'soft', 'penalty_yuan_per_discharge_mwh': 300},
    {'mode': 'hard'},
    {'mode': 'unknown'},
])
def test_policy_description_only_reads_cfg(policy):
    code = f'''
import sys
sys.path.insert(0, {str(ROOT / 'app')!r})
from copy import deepcopy
from smm.config import load_config
from anchor_policy_controls import render_anchor_policy
cfg = load_config()
cfg.mode['realtime']['storage']['mode'] = 'value'
cfg.mode['realtime']['value_method']['terminal_policy'] = {policy!r}
before = deepcopy((cfg.base, cfg.mode, cfg.units))
render_anchor_policy(cfg, '只读说明', disabled=True)
assert (cfg.base, cfg.mode, cfg.units) == before
'''
    at = AppTest.from_string(code, default_timeout=30).run()
    assert not at.exception
    assert at.button[0].disabled
    if policy['mode'] == 'soft':
        assert '罚价 300 元/可放电MWh' in outside_expanders(at, 'caption')
    elif policy['mode'] == 'hard':
        assert '原硬目标政策' in outside_expanders(at, 'caption')
    else:
        assert '未识别' in outside_expanders(at, 'warning')


@pytest.mark.parametrize('full', [True, False])
def test_result_navigation_keeps_scope_and_failed_checks_outside_details(tmp_path, full):
    from test_result_quality_alerts import freeze
    freeze(tmp_path)
    summary = {'completed_scope': 'debug_prefix', 'executed_periods': 2, 'expected_periods': 96,
               'checks': {'network_feasible': False, 'rt_line_overload_max_mw': 5.,
                          'rt_line_overload_count': 1},
               'reserve': {'rt_executed_reserve_up_short_max_mw': .25}}
    code = f'''
import sys
sys.path.insert(0, {str(ROOT / 'app')!r})
from copy import deepcopy
from result_navigation import build_result_navigation
summary = {summary!r}
da = {{'scuc_gap': .03}}
before = deepcopy((summary, da))
build_result_navigation(summary, da, directory={str(tmp_path)!r}, full={full!r})
assert (summary, da) == before
'''
    at = AppTest.from_string(code, default_timeout=30).run()
    assert not at.exception
    captions = outside_expanders(at, 'caption')
    assert '日前为计划' in captions and '辅助模型仅定价' in captions
    assert '缺失记录不按零处理' in captions
    if full:
        assert '日内为参考、不结算' in captions and '实时仅执行首段' in captions
    alerts = outside_expanders(at, 'error') + outside_expanders(at, 'warning')
    for token in ('线路越限', '5.0000 MW', '备用缺额', '0.2500 MW', '3.000%', '1.000%'):
        assert token in alerts
    assert '阅读顺序' not in captions
