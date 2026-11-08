"""UTF-8 archives must survive Windows' GBK default without UTF-8 mode.

Only fixture reads are forced to GBK. AppTest and test source loading retain
Python's own source encoding; no solver, services, or user archives are touched.
"""
import ast
import hashlib
import json
from pathlib import Path
import sys

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from market_views import frozen_bids, _saved_json, result_quality_messages
from forecast_error_view import load_forecast_diagnostics


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def gbk_default(tmp_path, monkeypatch):
    original = Path.open
    original_read_text = Path.read_text

    def open_as_windows(path, mode='r', buffering=-1, encoding=None,
                        errors=None, newline=None):
        if path.is_relative_to(tmp_path) and 'b' not in mode and encoding in (None, 'locale'):
            encoding = 'gbk'
        return original(path, mode, buffering, encoding, errors, newline)

    def read_as_windows(path, encoding=None, errors=None, *args, **kwargs):
        # Intercept the caller's choice before read_text resolves None to UTF-8
        # in UTF-8 mode. Explicit utf-8/utf-8-sig and outside paths stay intact.
        if path.is_relative_to(tmp_path) and encoding in (None, 'locale'):
            encoding = 'gbk'
        return original_read_text(path, encoding, errors, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', open_as_windows)
    monkeypatch.setattr(Path, 'read_text', read_as_windows)
    # This fails exactly like the original read_text() even under PYTHONUTF8=1.
    probe = tmp_path / 'gbk-probe.json'
    write_json(probe, {'note': '开'})
    with pytest.raises(UnicodeDecodeError, match='gbk'):
        probe.read_text()
    assert json.loads(probe.read_text(encoding='utf-8')) == {'note': '开'}
    with pytest.raises(UnicodeDecodeError, match='gbk'):
        with probe.open() as stream:
            stream.read()
    return tmp_path


def test_gbk_fixture_is_scoped_and_preserves_explicit_encodings(gbk_default):
    bom = gbk_default / 'explicit-bom.json'
    bom.write_text('{"note": "开"}', encoding='utf-8-sig')
    assert bom.read_bytes().startswith(b'\xef\xbb\xbf')
    assert json.loads(bom.read_text(encoding='utf-8-sig')) == {'note': '开'}
    assert bom.read_text(encoding='utf-8').startswith('\ufeff')
    outside = gbk_default.parent / (gbk_default.name + '-outside.json')
    write_json(outside, {'note': '开'})

    def builtin_default():
        with open(outside) as stream:
            return stream.read()

    def outcome(read):
        try:
            return ('decoded', read())
        except UnicodeDecodeError as exc:
            return ('decode_error', exc.encoding)

    # Outside the fixture directory, match the real native default in either
    # UTF-8 mode or locale mode. The fixture must not affect app/source loading.
    assert outcome(outside.read_text) == outcome(builtin_default)


@pytest.fixture
def quote():
    return {'note': '开', 'version': 2, 'units': {'开': {
        'segments': [{'mw': 25.0, 'price': -50.0}, {'mw': 75.0, 'price': 123.5}],
        'startup': 17.25, '说明': '按冻结报价核对，不替换历史字段'}}}


@pytest.mark.parametrize('filename', ['resolved_bids.json', 'submitted_bids.json',
                                    'scenario_snapshot/resolved_bids.json'])
def test_primary_frozen_quotes_use_utf8(gbk_default, quote, filename):
    write_json(gbk_default / filename, quote)
    before = snapshot(gbk_default)
    assert frozen_bids(gbk_default) == quote
    assert snapshot(gbk_default) == before


@pytest.mark.parametrize('version,expected', [(2, 2), (None, 10), (999, 10)])
def test_legacy_quote_and_chinese_metadata_use_utf8(gbk_default, quote, version, expected):
    for number in (2, 10):
        write_json(gbk_default / f'scenario_snapshot/bids/日期/MKT/bids_v{number}.json',
                   dict(quote, version=number))
    if version is not None:
        write_json(gbk_default / 'meta.json', {'bid_version': version, 'note': '开'})
    before = snapshot(gbk_default)
    assert frozen_bids(gbk_default) == dict(quote, version=expected)
    assert snapshot(gbk_default) == before


def test_precedence_missing_and_corrupt_quotes_unchanged(gbk_default, quote):
    assert frozen_bids(gbk_default) is None
    write_json(gbk_default / 'submitted_bids.json', dict(quote, version=99))
    primary = gbk_default / 'resolved_bids.json'
    write_json(primary, quote)
    assert frozen_bids(gbk_default) == quote
    primary.write_text('{partial', encoding='utf-8')
    with pytest.raises(json.JSONDecodeError):
        frozen_bids(gbk_default)  # Never silently fall back to a different bid.
    primary.write_bytes(b'\x80')
    with pytest.raises(UnicodeDecodeError, match='utf-8'):
        frozen_bids(gbk_default)


def test_saved_json_keeps_chinese_evidence_and_fail_closed_contract(gbk_default):
    path = gbk_default / 'stage_diagnostics.json'
    value = {'note': '开', 'da': {'scuc_preplan': {'summary': {'gap': .02}}}}
    write_json(path, value)
    assert _saved_json(path) == value
    for raw in (b'{partial', b'[]', b'\x80'):
        path.write_bytes(raw)
        assert _saved_json(path) == {}
    assert _saved_json(gbk_default / 'missing.json') == {}


def test_quality_targets_from_chinese_frozen_yaml(gbk_default):
    config = gbk_default / 'scenario_snapshot/config'
    config.mkdir(parents=True)
    (config / 'base.yaml').write_text('note: 开\nrule_set: MKT\nsolver:\n  mip_rel_gap: 0.01\n', encoding='utf-8')
    (config / 'modes.yaml').write_text('note: 开\nMKT:\n  realtime:\n    mip_rel_gap: 0.01\n', encoding='utf-8')
    write_json(gbk_default / 'stage_diagnostics.json', {'note': '开', 'rt_windows': [
        {'stages': {'scuc_preplan': {'summary': {'gap': .02}}}}]})
    before = snapshot(gbk_default)
    messages = result_quality_messages({}, {'scuc_gap': .02}, directory=gbk_default)
    assert any(level == 'warning' and '日前求解间隙 2.000%' in text and '1.000%' in text
               for level, text in messages)
    assert any(level == 'warning' and '1/1' in text and '2.000%' in text
               for level, text in messages)
    assert not any('目标精度未记录' in text for _, text in messages)
    assert snapshot(gbk_default) == before


def test_forecast_diagnostics_preserve_chinese_failure_reason(gbk_default):
    message = '开：原始发布或实际执行证据不完整'
    path = gbk_default / 'forecast_error_status.json'
    write_json(path, {'valid': False, 'errors': [message]})
    before = snapshot(gbk_default)
    result = load_forecast_diagnostics(gbk_default)
    assert result == {'valid': False, 'reason': message}
    assert snapshot(gbk_default) == before
    path.write_bytes(b'\x80')
    result = load_forecast_diagnostics(gbk_default)
    assert not result['valid'] and 'utf-8' in result['reason']


def test_chinese_bid_fields_and_result_chart_render_exactly(gbk_default, quote, monkeypatch):
    write_json(gbk_default / 'resolved_bids.json', quote)
    before = snapshot(gbk_default)
    charts = []
    original = st.plotly_chart

    def capture(figure, *args, **kwargs):
        charts.append(figure)
        return original(figure, *args, **kwargs)

    monkeypatch.setattr(st, 'plotly_chart', capture)
    units = [{'unit': '开', 'type': 'coal', 'period': i, 'P_mw': value, 'on': 1}
             for i, value in enumerate([12.5, 25.0, 18.75])]
    script = (f'from market_views import frozen_bids, unit_dashboard\n'
              f'unit_dashboard({{"units": {units!r}}}, bids=frozen_bids({str(gbk_default)!r}))')
    at = AppTest.from_string(script, default_timeout=30).run()
    assert not at.exception, [e.message for e in at.exception]
    assert at.selectbox[0].value == '开'
    assert json.loads(at.json[0].value) == quote['units']['开']
    assert charts and len(at.get('plotly_chart')) == 1
    # Inspect the real figure before Plotly's JSON serializer encodes numpy arrays.
    trace = charts[0].data[0]
    assert list(trace.x) == [0, 1, 2]
    assert list(trace.y) == [12.5, 25.0, 18.75]
    assert snapshot(gbk_default) == before


def test_series_results_chinese_status_errors_and_financial_payload(gbk_default, monkeypatch):
    import series_workspace_ui as ui
    import series_financial_view as financial
    payload = {'note': '开', 'cash': 123.5}
    write_json(gbk_default / 'run/series_status.json', {'status': 'active', 'completed_days': 0,
               'terminal_pending_obligations': [{'unit': '开', 'remaining_h': 2}]})
    write_json(gbk_default / 'worker_outcome.json', {'status': 'failed', 'error': '开：原始运行错误'})
    write_json(gbk_default / 'run/series_financial.json', payload)
    monkeypatch.setattr(ui.adapter, 'completed_day_metadata', lambda *_: [])
    seen = []
    monkeypatch.setattr(financial, 'render_series_financial', lambda value: seen.append(value))
    manifest = {'days': [{}], 'task_id': '开', 'revision': 1, 'source': {'name': '开', 'hash': 'h'}, 'timezone': 'UTC'}
    before = snapshot(gbk_default)
    at = AppTest.from_string(f'from series_workspace_ui import results_panel\nresults_panel({str(gbk_default)!r}, {manifest!r})').run()
    assert not at.exception, [e.message for e in at.exception]
    assert seen == [payload]
    assert any('开 剩余 2 小时' in e.value for e in at.warning)
    assert any(e.value == '开：原始运行错误' for e in at.error)
    assert snapshot(gbk_default) == before


def test_result_archive_readers_declare_encoding():
    """Guard even the legacy copy branch without changing any saved task."""
    for name in ('market_views.py', 'forecast_error_view.py', 'series_workspace_ui.py'):
        tree = ast.parse((ROOT / 'app' / name).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'read_text'):
                continue
            # Legacy subprocess logs were written with the producer's locale.
            # They are not JSON/YAML and must not be reinterpreted as UTF-8.
            if name == 'series_workspace_ui.py' and any(k.arg == 'errors' for k in node.keywords):
                assert any(k.arg == 'errors' and isinstance(k.value, ast.Constant)
                           and k.value.value == 'replace' for k in node.keywords)
                continue
            assert any(k.arg == 'encoding' and isinstance(k.value, ast.Constant)
                       and k.value.value == 'utf-8' for k in node.keywords), (name, node.lineno)


def test_native_forecast_diagnostics_with_chinese_status(gbk_default):
    from test_forecast_error_view import _evidence
    from smm.forecast_diagnostics import write_post_execution_diagnostics
    assert write_post_execution_diagnostics(_evidence(), gbk_default)['valid']
    path = gbk_default / 'forecast_error_status.json'
    status = json.loads(path.read_text(encoding='utf-8'))
    status['note'] = '开'
    write_json(path, status)
    before = snapshot(gbk_default)
    result = load_forecast_diagnostics(gbk_default)
    assert result['valid'], result
    assert result['status']['note'] == '开'
    assert len(result['actual']) == 16
    assert result['detail'].period.max() == 3
    assert (result['summary'].query("stage=='RT' and resource_id=='W'").rmse > 0).all()
    assert snapshot(gbk_default) == before


def test_legacy_copy_base_quote_reads_chinese_payload(gbk_default, quote, monkeypatch):
    from types import SimpleNamespace
    import series_workspace_ui as ui
    import unit_input_view
    write_json(gbk_default / 'source/quote_candidate.json', quote)
    cfg = SimpleNamespace(units={'thermal': [{'id': '开', 'type': 'coal'}]})
    monkeypatch.setattr(ui.adapter, 'day_config', lambda *_: cfg)
    monkeypatch.setattr(unit_input_view, 'render_unit_summary', lambda *_: None)
    monkeypatch.setattr(unit_input_view, 'render_unit_information', lambda *_, **kw: None)
    monkeypatch.setattr(ui.ws, 'bid_status', lambda *_: '未报价')
    copied = []
    monkeypatch.setattr(ui.ws, 'replace_daily_bids', lambda m, d, package, **kw: copied.append(package))
    monkeypatch.setattr(ui, '_save', lambda *_, **kw: None)
    manifest = {'days': [{'date': '2026-07-15', 'bid_package': None}]}
    code = (f'from series_workspace_ui import bid_panel\n'
            f'bid_panel({str(gbk_default)!r}, {manifest!r}, "2026-07-15", False)')
    before = snapshot(gbk_default)
    at = AppTest.from_string(code).run()
    assert not at.exception, [e.message for e in at.exception]
    next(b for b in at.button if b.label == '复制基础方案报价到这天').click().run()
    assert not at.exception, [e.message for e in at.exception]
    assert copied == [quote]
    assert snapshot(gbk_default) == before
