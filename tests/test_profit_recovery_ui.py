"""A single explicit click creates a persistent, clearly labelled cost supplement."""
import json
from pathlib import Path
import sys

from streamlit.testing.v1 import AppTest

from test_profit_recalculation import fixture_run, hashes

ROOT = Path(__file__).resolve().parents[1]


def open_app(runs):
    code = f'''import sys
sys.path.insert(0, {str(ROOT / 'app')!r})
import json
from pathlib import Path
import streamlit as st
from profit_recovery import historical_profit_recovery
from market_views import operating_profit_dashboard
selected = st.selectbox('运行', {list(map(str, runs))!r})
run = Path(selected)
result = dict(dir=selected, full=True, summary=json.loads((run / 'summary.json').read_text()))
report = historical_profit_recovery(result)
if report:
    st.metric('补算成本', report['summary']['cash_operating_cost_yuan'])
    st.metric('补算利润', report['summary']['simulated_operating_profit_yuan'])
    operating_profit_dashboard(report['by_unit'], report['by_period'], prefix='test:' + selected)
'''
    return AppTest.from_string(code, default_timeout=30).run()


def test_one_click_recovery_keeps_originals_and_survives_new_session(tmp_path, monkeypatch):
    from smm import config
    run, source = fixture_run(tmp_path)
    monkeypatch.setattr(config, 'ROOT', source)
    before = hashes(run)
    at = open_app([run])
    assert not at.exception and hashes(run) == before
    assert not (run / 'cost_recalculations').exists()
    assert any('非实测' in c.value and '原始结果' in c.value for c in at.caption)
    at.button[0].click().run()
    assert not at.exception and not at.error
    assert next(m for m in at.metric if m.label == '补算成本').value == '130.0'
    assert next(m for m in at.metric if m.label == '补算利润').value == '110.0'
    assert any('2/4' in m.value for m in at.success)
    assert not any('全天' in m.value for m in at.success)
    assert hashes(run) == before
    reports = list((run / 'cost_recalculations').glob('*/manifest.json'))
    assert len(reports) == 1
    new = open_app([run])
    assert not new.exception and new.success
    assert not any(b.label == '补算本次成本与利润' for b in new.button)
    assert len(list((run / 'cost_recalculations').glob('*/manifest.json'))) == 1
    assert hashes(run) == before


def test_switching_run_does_not_reuse_other_run_supplement(tmp_path, monkeypatch):
    from smm import config
    first, source = fixture_run(tmp_path / 'a')
    second, _ = fixture_run(tmp_path / 'b')
    monkeypatch.setattr(config, 'ROOT', source)
    at = open_app([first, second])
    at.button[0].click().run()
    assert not at.exception and at.success
    at.selectbox[0].select(str(second)).run()
    assert not at.exception
    assert not at.success
    assert not (second / 'cost_recalculations').exists()
    assert any(b.label == '补算本次成本与利润' for b in at.button)


def test_frozen_supplement_survives_later_cost_source_change(tmp_path, monkeypatch):
    from smm import config
    run, source = fixture_run(tmp_path)
    monkeypatch.setattr(config, 'ROOT', source)
    at = open_app([run])
    at.button[0].click().run()
    assert not at.exception
    (source / 'config/fixed_costs.yaml').unlink()
    new = open_app([run])
    assert not new.exception and new.success
    assert next(m for m in new.metric if m.label == '补算利润').value == '110.0'


def test_complete_original_does_not_offer_replacement(tmp_path, monkeypatch):
    from smm import config
    run, source = fixture_run(tmp_path)
    monkeypatch.setattr(config, 'ROOT', source)
    path = run / 'summary.json'
    summary = json.loads(path.read_text())
    summary['operating_profit'] = dict(all_unit_costs_available=True,
        cash_operating_cost_yuan=10, simulated_operating_profit_yuan=20)
    path.write_text(json.dumps(summary))
    at = open_app([run])
    assert not at.exception and not at.button and not at.metric
    assert not (run / 'cost_recalculations').exists()


def test_missing_evidence_has_clear_failure_and_no_fake_cost(tmp_path, monkeypatch):
    from smm import config
    import streamlit as st
    run, source = fixture_run(tmp_path)
    monkeypatch.setattr(config, 'ROOT', source)
    # This isolated AppTest is not a multipage app; the real route exists in app/pages.
    monkeypatch.setattr(st, 'page_link', lambda *args, **kwargs: None)
    (run / 'operating_profit_by_period.csv').unlink()
    before = hashes(run)
    at = open_app([run])
    assert not at.exception
    assert any('不足以安全补算' in x.value for x in at.error)
    assert not at.metric and not at.button
    assert not (run / 'cost_recalculations').exists()
    assert hashes(run) == before
