"""A failed first configuration remains editable; JSON labels match their scope."""
import hashlib
import json
from pathlib import Path

import streamlit as st
from streamlit.testing.v1 import AppTest

PROGRAM = Path(__file__).resolve().parents[1]


def test_first_offer_recovery_and_result_download_scope(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(PROGRAM / 'app'))
    from market_rule_profile import MarketProfile, SICHUAN_V5
    root = tmp_path / SICHUAN_V5.results_folder
    monkeypatch.setattr(MarketProfile, 'default_results', lambda self, _: root)
    payloads = {}
    render_download = st.download_button

    def capture(label, *args, **kwargs):
        payloads[label] = (kwargs['file_name'], json.loads(kwargs['data']))
        return render_download(label, *args, **kwargs)

    monkeypatch.setattr(st, 'download_button', capture)
    app = AppTest.from_file(str(PROGRAM / 'app/四川.py'), default_timeout=100).run()
    app.checkbox(key='scv5_id_enabled').check().run()
    app.selectbox(key='scv5_month').select(5)
    app.number_input(key='scv5_bid').set_value(405)
    app.slider(key='scv5_id_pct').set_value(0)
    app.button(key='scv5_run').click().run()
    assert any('非零负荷变化' in error.value for error in app.error)
    assert not root.exists() and not payloads
    assert app.number_input(key='scv5_bid').value == 405
    app.checkbox(key='scv5_id_enabled').uncheck().run()
    app.button(key='scv5_run').click().run()
    assert not app.error and not app.exception
    (archive,) = list(root.iterdir())
    assert any(str(archive.relative_to(tmp_path)) in caption.value for caption in app.caption)
    assert any('均覆盖全部已执行段' in caption.value and '不包含完整' in caption.value
               for caption in app.caption)
    inputs = json.loads((archive / 'INPUT.json').read_bytes())
    assert inputs['month'] == 5 and inputs['coal_offer'] == 405
    assert inputs['declared_participation']['PV1'] == 'quantity_only'
    app.selectbox(key='scv5_period_' + archive.name).select(3).run()
    valuation_file, valuation = payloads['下载实时估值 JSON']
    recon_file, recon = payloads['下载实时量价对账 JSON']
    assert valuation_file.startswith(archive.name) and recon_file.startswith(archive.name)
    assert valuation['formal_settlement'] is recon['formal_settlement'] is False
    assert valuation['quarters'] == valuation['original_quarters'] == json.loads(
        (archive / 'RT_RESEARCH_VALUES.json').read_bytes())
    for result in (valuation, recon):
        assert [q['quarter'] for q in result['quarters']] == list(range(4))
    assert recon['summary']['full_day_coverage'] is False


    # A new session restores the same data, even if the recipient renames
    # the download. The original RT digest binds actual archive bytes.
    initial_payloads = dict(payloads)
    fresh = AppTest.from_file(str(PROGRAM / 'app/四川.py'), default_timeout=30).run()
    fresh.selectbox(key='scv5_archive').select(archive.name).run()
    assert fresh.button(key='scv5_run').disabled
    fresh.selectbox(key='scv5_period_' + archive.name).select(3).run()
    assert not fresh.error and not fresh.exception
    receipt = json.loads((archive / 'RECEIPT.json').read_bytes())
    rt_sha = hashlib.sha256((archive / 'RT_EXECUTED.json').read_bytes()).hexdigest()
    assert rt_sha == receipt['data_files']['RT_EXECUTED.json']
    for index, label in enumerate(('下载实时估值 JSON', '下载实时量价对账 JSON')):
        assert payloads[label] == initial_payloads[label]
        filename = tmp_path / f'专家收到并重命名{index}.json'
        filename.write_text(json.dumps(payloads[label][1]), encoding='utf-8')
        saved = json.loads(filename.read_bytes())
        expected = {'schema': 'ieee39.sichuan_v5.result_provenance.v1',
                    'run_id': archive.name, 'rules': receipt['rules'],
                    'executed_quarters': 4, 'expected_day_quarters': 96,
                    'full_day_coverage': False, 'original_rt_executed_sha256': rt_sha,
                    'price_review_quarters': [], 'complete_archive': False}
        assert saved['provenance'] == expected
    assert len(list(root.iterdir())) == 1
