"""Business result hierarchy: diagnostics retained but closed, never replacing quantities."""
from pathlib import Path
import sys
import hashlib
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))

def test_actual_results_business_first_and_technical_collapsed(monkeypatch, tmp_path):
    import common
    import os
    supplied=os.environ.get("IEEE39_SAVED_RUN_FOR_UI")
    if supplied:
        monkeypatch.setattr(common,"all_results",lambda:[dict(dir=supplied,scenario="验收只读存档",mode="full",status="done")])
    from smm import scenario as sc
    monkeypatch.setattr(sc, "SCEN_DIR", tmp_path / "scenarios")
    monkeypatch.setattr(sc, "RUNS_DIR", tmp_path / "runs")
    catalog=common.all_results()
    if not catalog:pytest.skip('real saved run unavailable')
    import json
    def _aux(m):  # 只选记录了辅助定价来源的完整存档（早期存档缺该字段，不能据此断言说明文字）
        try:return json.loads(Path(m['dir']).joinpath('summary.json').read_text(encoding='utf-8')).get('da_summary',{}).get('pricing_source')=='bounded_auxiliary_pricing'
        except Exception:return False
    # 本用例选择无日内存档；四区结构也由独立的启用日内用例覆盖
    def _intraday(m):  # 同 3_结果.py 的 INTRADAY 判定
        try:return bool((json.loads(Path(m['dir']).joinpath('summary.json').read_text(encoding='utf-8')).get('intraday') or {}).get('enabled'))
        except Exception:return False
    full=next((m for m in catalog if Path(m['dir']).joinpath('rt_prices.csv').exists() and _aux(m) and not _intraday(m)),None)
    if full is None:pytest.skip('full saved result unavailable')
    archive_before = archive_hashes(Path(full['dir']))
    prices=pd.read_csv(Path(full['dir'])/'da_nodal_prices.csv')
    before=prices.copy(deep=True)
    at=AppTest.from_file(str(ROOT/'app/主页.py'),default_timeout=60).run()
    at.session_state['result_sel']=full['dir']
    at.switch_page('pages/3_结果.py').run()
    assert not at.exception
    assert [t.label for t in at.tabs] == ['系统总览', '机组分析', '价格分析', '网络与安全']
    assert at.radio(key='advanced_stage').options == ['DA', 'RT']
    assert any('全天结算' in c.value for c in at.caption)
    advanced=next(e for e in at.expander if e.label=='高级诊断与技术记录（按需查看）')
    assert advanced.proto.expanded is False
    assert any('混合整数相对间隙' in c.value for c in advanced.caption)
    assert any('辅助模型数量不执行' in c.value for c in advanced.caption)
    table=next(d.value for d in at.dataframe if set(d.value.columns)=={'时段序号','时刻','节点','限价后结算电价 元/MWh'})
    pd.testing.assert_series_equal(table['限价后结算电价 元/MWh'].reset_index(drop=True),before.lmp,check_names=False)
    pd.testing.assert_frame_equal(pd.read_csv(Path(full['dir'])/'da_nodal_prices.csv'),before)
    assert not any('间隙' in m.label for m in at.metric)
    assert not at.get('download_button')

    assert any('机组收入、成本与利润' in x.value for x in at.subheader)
    paired = next(d.value for d in at.dataframe if any('日前' in str(c) and '收入' in str(c) for c in d.value.columns))
    assert len(paired) > 0
    assert any('利润' in str(c) for c in paired.columns)
    for name in ('系统资金核对与结算明细', '价格明细', '完整存档与来源', '高级诊断与技术记录（按需查看）'):
        assert not next(e for e in at.expander if e.label == name).proto.expanded
    assert '日前系统接线图' in {x.value for x in at.subheader}
    for key, values in [('overview_stage', ['rt', 'da']), ('price_stage', ['rt', 'da', 'compare']),
                        ('network_stage', ['rt', 'da']), ('advanced_stage', ['RT', 'DA'])]:
        for value in values:
            at.radio(key=key).set_value(value).run()
            assert not at.exception, [x.message for x in at.exception]
            assert [t.label for t in at.tabs] == ['系统总览', '机组分析', '价格分析', '网络与安全']
            if key == 'network_stage':
                assert ('实时系统接线图' if value == 'rt' else '日前系统接线图') in {x.value for x in at.subheader}
    pd.testing.assert_frame_equal(pd.read_csv(Path(full['dir'])/'da_nodal_prices.csv'), before)
    assert archive_hashes(Path(full['dir'])) == archive_before


def archive_hashes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


def test_saved_da_only_uses_same_four_sections_without_realtime(monkeypatch, tmp_path):
    import os
    import common
    from smm import scenario as sc
    supplied = os.environ.get('IEEE39_SAVED_RUN_FOR_UI')
    if not supplied:
        pytest.skip('real saved run unavailable')
    archive = Path(supplied)
    directory = archive / 'da'
    if not directory.is_dir():
        # CLI archives store their actual DA tables beside the full-day summary.
        # Build a detached DA-only fixture without changing the saved archive.
        import json
        import shutil
        summary = json.loads((archive / 'summary.json').read_text(encoding='utf-8'))
        directory = tmp_path / 'da-only'
        directory.mkdir()
        tables = list(archive.glob('da_*.csv'))
        assert tables and isinstance(summary.get('da_summary'), dict)
        for source in tables:
            shutil.copyfile(source, directory / source.name)
        (directory / 'summary.json').write_text(
            json.dumps(summary['da_summary'], ensure_ascii=False), encoding='utf-8')
    assert (directory / 'summary.json').is_file()
    before = archive_hashes(archive)
    monkeypatch.setattr(sc, 'SCEN_DIR', tmp_path / 'scenarios')
    monkeypatch.setattr(sc, 'RUNS_DIR', tmp_path / 'runs')
    monkeypatch.setattr(common, 'all_results', lambda: [dict(dir=str(directory), scenario='只读日前', mode='da', status='done')])
    at = AppTest.from_file(str(ROOT/'app/主页.py'), default_timeout=90).run()
    at.switch_page('pages/3_结果.py').run()
    assert not at.exception, [x.message for x in at.exception]
    assert [t.label for t in at.tabs] == ['系统总览', '机组分析', '价格分析', '网络与安全']
    assert at.radio(key='advanced_stage').options == ['DA']
    for key in ('overview_stage', 'price_stage', 'network_stage'):
        assert len(at.radio(key=key).options) == 1
    assert any('仅日前计划' in c.value and '无实时执行与偏差结算' in c.value for c in at.caption)
    assert not any('全天结算' in c.value for c in at.caption)
    assert archive_hashes(archive) == before


@pytest.fixture
def saved_ui_archive(monkeypatch, tmp_path):
    import os
    import common
    from smm import scenario as sc
    supplied = os.environ.get('IEEE39_SAVED_RUN_FOR_UI')
    if not supplied:
        pytest.skip('real saved run unavailable')
    monkeypatch.setattr(sc, 'SCEN_DIR', tmp_path / 'scenarios')
    monkeypatch.setattr(sc, 'RUNS_DIR', tmp_path / 'runs')
    return Path(supplied)


def test_id_price_rounds_keep_failed_reference_missing(saved_ui_archive, monkeypatch):
    from copy import deepcopy
    import common
    from unit_overview import PRICE
    archive = saved_ui_archive
    before = archive_hashes(archive)
    meta = dict(dir=str(archive), scenario='日内价格只读夹具', mode='full', status='done')
    result = common.get_result(meta)
    result['summary']['intraday'] = dict(enabled=True, rounds=1,
        failed_rounds=[dict(tau=4, phase='formal_sced', reason='controlled missing round')])
    result['id_rounds_summary'] = pd.DataFrame([dict(tau=0, round=1)])
    result['id_prices_indicative'] = pd.DataFrame([dict(period=0, bus=1, round=1, lmp=321.25)])
    monkeypatch.setattr(common, 'all_results', lambda: [meta])
    monkeypatch.setattr(common, 'get_result', lambda _: deepcopy(result))
    at = AppTest.from_file(str(ROOT/'app/主页.py'), default_timeout=90).run()
    at.session_state['price_stage'] = 'id'
    at.switch_page('pages/3_结果.py').run()
    assert not at.exception, [x.message for x in at.exception]
    detail = next(e for e in at.expander if e.label == '日内价格明细')
    assert detail.dataframe[0].value[PRICE['id']].tolist() == [321.25]
    assert '不结算' in at.radio(key='price_stage').options[2]
    at.selectbox(key='price_id_tau').set_value(4).run()
    assert not at.exception, [x.message for x in at.exception]
    assert any('所选轮次失败' in x.value and '不使用旧轮次替代' in x.value for x in at.warning)
    assert not any(e.label == '日内价格明细' for e in at.expander)
    assert result['id_prices_indicative'].lmp.tolist() == [321.25]
    assert archive_hashes(archive) == before


def test_saved_prefix_overview_compares_peaks_on_executed_scope(saved_ui_archive, monkeypatch):
    import json
    import common
    from result_navigation import recorded_number
    archive = None
    for candidate in saved_ui_archive.parent.parent.glob('*/*/summary.json'):
        summary = json.loads(candidate.read_text(encoding='utf-8'))
        if summary.get('completed_scope') == 'debug_prefix' and (candidate.parent/'rt_prices.csv').is_file():
            archive = candidate.parent
            break
    if archive is None:
        pytest.skip('short-prefix saved archive unavailable')
    before = archive_hashes(archive)
    meta = dict(dir=str(archive), scenario='短前缀只读存档', mode='full', status='done')
    result = common.get_result(meta)
    periods = result['rt_prices'].period.unique()
    expected = ' / '.join(recorded_number(result[key].loc[result[key].period.isin(periods), 'load_mw'].max())
                          for key in ('da_system', 'rt_system'))
    monkeypatch.setattr(common, 'all_results', lambda: [meta])
    at = AppTest.from_file(str(ROOT/'app/主页.py'), default_timeout=90).run()
    at.switch_page('pages/3_结果.py').run()
    assert not at.exception, [x.message for x in at.exception]
    metric = next(x for x in at.metric if x.label == '日前 / 实时负荷峰值 · MW')
    assert metric.value == expected
    assert any('按已执行实时对应时段比较' in x.value for x in at.caption)
    assert any('未确认全天结算' in x.value for x in at.caption)
    assert [x.label for x in at.tabs] == ['系统总览', '机组分析', '价格分析', '网络与安全']
    assert archive_hashes(archive) == before
