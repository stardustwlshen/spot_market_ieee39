"""The current value method is not mislabelled as a legacy migration target."""
import copy
from pathlib import Path
import sys

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from smm.config import load_config
from smm import scenario


@pytest.fixture
def saved_modes(monkeypatch):
    cfg = load_config()
    modes = {cfg.rule_set: copy.deepcopy(cfg.mode)}
    writes = []
    monkeypatch.setattr(scenario, 'read_yaml', lambda name, which: copy.deepcopy(modes))
    monkeypatch.setattr(scenario, 'write_yaml', lambda name, which, data: writes.append((name, which, copy.deepcopy(data))))
    return cfg, modes, writes


def render(*, mode='value', anchor='two_hour_eq', disabled=False):
    code = (f"import sys\nsys.path.insert(0,{str(ROOT / 'app')!r})\n"
            "from smm.config import load_config\nfrom anchor_policy_controls import render_anchor_policy\n"
            "cfg=load_config()\n"
            f"cfg.mode['realtime']['storage'].update(mode={mode!r}, anchor={anchor!r})\n"
            f"render_anchor_policy(cfg,'只读测试场景',disabled={disabled!r})")
    at = AppTest.from_string(code, default_timeout=30).run()
    assert not at.exception, [x.message for x in at.exception]
    return at


def test_value_method_is_current_and_alternative_is_explicitly_optional(saved_modes):
    cfg, modes, writes = saved_modes
    before = copy.deepcopy(modes)
    at = render()
    assert any('当前储能/抽蓄采用价值法' in x.value for x in at.info)
    assert not any('历史策略' in x.value for x in at.info)
    optional = next(x for x in at.expander if '可选研究策略' in x.label)
    assert optional.proto.expanded is False
    assert any('可选模型切换，不是升级要求' in x.value for x in optional.caption)
    assert next(iter(at.button)).label == '将本场景切换到两小时锚点策略'
    at.run()
    assert writes == [] and modes == before
    assert cfg.mode['realtime']['storage']['mode'] == 'value'


def test_explicit_click_only_changes_storage_policy_in_requested_scene(saved_modes):
    cfg, modes, writes = saved_modes
    before = copy.deepcopy(modes)
    at = render()
    at.button[0].click().run()
    assert not at.exception
    assert len(writes) == 1
    name, which, changed = writes[0]
    assert name == '只读测试场景' and which == 'modes'
    expected = copy.deepcopy(before)
    expected[cfg.rule_set]['realtime']['storage'].update(mode='sced_anchor', anchor='two_hour_eq')
    assert changed == expected
    assert modes == before


def test_active_run_disables_optional_policy_switch(saved_modes):
    at = render(disabled=True)
    assert at.button[0].disabled
    assert saved_modes[2] == []


@pytest.mark.parametrize('mode,anchor,label', [
    ('sced_anchor', 'hourly_eq', '每小时带罚软目标'),
    ('sced_anchor', 'window_end_pct', '窗口末带罚软区间'),
    ('fixed_da', 'two_hour_eq', '固定日前充放电'),
])
def test_legacy_modes_are_described_without_automatic_migration(saved_modes, mode, anchor, label):
    at = render(mode=mode, anchor=anchor)
    assert any(label in x.value for x in at.info)
    assert not any('采用价值法' in x.value for x in at.info)
    assert saved_modes[2] == []


def test_existing_two_hour_policy_is_not_offered_again(saved_modes):
    at = render(mode='sced_anchor')
    assert not at.button
    assert any('当前储能/抽蓄采用两小时锚点' in x.value for x in at.caption)
    assert any('明确不可行时' in x.value for x in at.caption)
    assert saved_modes[2] == []
