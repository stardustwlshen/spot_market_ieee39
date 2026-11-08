"""Form reset must replace widget identities, not just clear Python state."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from smm import spot_costs as costs
from test_spot_cost_ui import item
from test_spot_costs import scene


FUEL = '燃料发电成本（元/MWh）'
SOURCE = '成本来源/假设说明'


def app(root, uid='C1'):
    return AppTest.from_string(f'''
from pathlib import Path
from smm.config import load_config
from spot_cost_editor import render_spot_cost_editor
render_spot_cost_editor(load_config(root=Path({str(root)!r})), {uid!r})
''', default_timeout=30).run()


def files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for folder in ('config', 'bids')
            for p in (root / folder).rglob('*') if p.is_file()}


def widgets(at):
    return {x.label: x.id for kind in ('number_input', 'text_input', 'selectbox', 'checkbox')
            for x in getattr(at, kind)}


def clean(at):
    assert not at.exception and not at.error


def test_cancel_discards_repeated_browser_widget_identity(tmp_path):
    cfg = scene(tmp_path)
    at = app(tmp_path)
    item(at.number_input, FUEL).set_value(271.)
    item(at.button, '保存现货成本').click().run()
    clean(at)
    saved = files(tmp_path)
    source = costs.load_profile(cfg)['units']['C1']['source']
    for draft in (272., 273., 274.):
        old_widgets = widgets(at)
        item(at.number_input, FUEL).set_value(draft)
        item(at.text_input, SOURCE).set_value('cancel this draft')
        item(at.selectbox, '在线费定义').set_value('no_load_fuel')
        item(at.button, '取消成本编辑').click().run()
        clean(at)
        assert files(tmp_path) == saved
        assert item(at.number_input, FUEL).value == 271.
        assert item(at.text_input, SOURCE).value == source
        assert item(at.selectbox, '在线费定义').value == costs.load_profile(cfg)['units']['C1']['online_cost_basis']
        assert all(widgets(at)[label] != identity for label, identity in old_widgets.items())
        # An extra rerun must not resurrect discarded frontend form values.
        at.run()
        assert item(at.number_input, FUEL).value == 271.
        item(at.button, '保存现货成本').click().run()
        clean(at)
        assert files(tmp_path) == saved


def test_repeated_noop_save_preserves_bytes_and_remounts(tmp_path):
    cfg = scene(tmp_path)
    before = files(tmp_path)
    original = costs.load_profile(cfg)['units']['C1']
    at = app(tmp_path)
    for _ in range(3):
        identities = widgets(at)
        item(at.button, '保存现货成本').click().run()
        clean(at)
        assert files(tmp_path) == before
        assert costs.load_profile(cfg)['units']['C1'] == original
        assert item(at.number_input, FUEL).value == original['fuel_cost_yuan_per_mwh']
        assert all(widgets(at)[label] != identity for label, identity in identities.items())


def test_check_keeps_draft_then_save_remounts_from_exact_value(tmp_path):
    cfg = scene(tmp_path)
    before = files(tmp_path)
    at = app(tmp_path)
    for value in (270.025, 270.026, 270.000001):
        identities = widgets(at)
        item(at.number_input, FUEL).set_value(value)
        item(at.button, '检查成本').click().run()
        clean(at)
        assert widgets(at) == identities
        assert item(at.number_input, FUEL).value == value
        item(at.button, '保存现货成本').click().run()
        clean(at)
        assert item(at.number_input, FUEL).value == value
        assert costs.load_profile(cfg)['units']['C1']['fuel_cost_yuan_per_mwh'] == value
        assert all(widgets(at)[label] != identity for label, identity in identities.items())
        assert {key for key, data in files(tmp_path).items() if before.get(key) != data} == {'config/' + costs.FILENAME}
        assert item(app(tmp_path).number_input, FUEL).value == value


@pytest.mark.parametrize('uid', ['PS1', 'ES1'])
def test_cancel_remounts_nonthermal_cost_fields(tmp_path, uid):
    scene(tmp_path)
    before = files(tmp_path)
    at = app(tmp_path, uid)
    originals = {x.label: x.value for kind in ('number_input', 'text_input', 'checkbox')
                 for x in getattr(at, kind)}
    for _ in range(2):
        identities = widgets(at)
        for number in at.number_input:
            number.set_value(number.value + 1.)
        for checkbox in at.checkbox:
            checkbox.set_value(not checkbox.value)
        item(at.text_input, SOURCE).set_value('discard this')
        item(at.button, '取消成本编辑').click().run()
        clean(at)
        assert files(tmp_path) == before
        for kind in ('number_input', 'text_input', 'checkbox'):
            assert all(x.value == originals[x.label] for x in getattr(at, kind))
        assert all(widgets(at)[label] != identity for label, identity in identities.items())


def test_cancel_refreshes_concurrent_file_token_and_value(tmp_path):
    cfg = scene(tmp_path)
    at = app(tmp_path)
    record = costs.load_profile(cfg)['units']['C1'].copy()
    record['fuel_cost_yuan_per_mwh'] = 275.
    costs.apply_edit(costs.prepare_edit(cfg, {'C1': record}))
    before = files(tmp_path)
    item(at.number_input, FUEL).set_value(999.)
    item(at.button, '保存现货成本').click().run()
    assert at.error and files(tmp_path) == before
    item(at.button, '取消成本编辑').click().run()
    clean(at)
    assert item(at.number_input, FUEL).value == 275.
    item(at.button, '保存现货成本').click().run()
    clean(at)
    assert files(tmp_path) == before


def test_explicit_defaults_discard_empty_form_draft(tmp_path):
    cfg = scene(tmp_path)
    path = tmp_path / 'config' / costs.FILENAME
    path.unlink()
    at = app(tmp_path)
    identities = widgets(at)
    item(at.number_input, FUEL).set_value(999.)
    item(at.text_input, SOURCE).set_value('do not save this')
    item(at.button, '采用本机组现货研究成本').click().run()
    clean(at)
    saved = costs.load_profile(cfg)['units']['C1']
    assert item(at.number_input, FUEL).value == saved['fuel_cost_yuan_per_mwh'] == 270.
    assert item(at.text_input, SOURCE).value == saved['source']
    assert all(widgets(at)[label] != identity for label, identity in identities.items())
    before = files(tmp_path)
    item(at.button, '保存现货成本').click().run()
    clean(at)
    assert files(tmp_path) == before
