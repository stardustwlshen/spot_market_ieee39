"""Run the Sichuan page as a user, including an explicit correction and reopening."""
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

PROGRAM=Path(__file__).resolve().parents[1]


def test_review_and_reconcile_on_native_sichuan_page(tmp_path,monkeypatch):
    monkeypatch.syspath_prepend(str(PROGRAM/'app'))
    from market_rule_profile import MarketProfile, SICHUAN_V5
    monkeypatch.setattr(MarketProfile,'default_results',lambda self,_:tmp_path/self.results_folder)
    root=SICHUAN_V5.default_results(tmp_path)
    app=AppTest.from_file(str(PROGRAM/'app/四川.py'),default_timeout=90).run()
    assert app.checkbox(key='scv5_equal_offer').value
    app.button(key='scv5_run').click().run()
    assert not app.exception and not app.error
    archived,=root.iterdir()
    assert not list(archived.glob('RT_PRICE_REVIEW*.json'))
    original={p.name:p.read_bytes() for p in archived.iterdir()}
    assert json.loads(original['INPUT.json'])['equal_offer_priority'] is True
    assert any(x.label=='下载实时量价对账 JSON' for x in app.get('download_button'))
    def submit():
        next(b for b in app.button if b.label=='保存本时段价格复核').click().run()
    submit()
    assert any('异常记录编号' in e.value for e in app.error)
    assert not list(archived.glob('RT_PRICE_REVIEW*.json'))
    next(t for t in app.text_input if t.label.startswith('异常记录编号')).set_value('SYNTHETIC-Q1')
    next(t for t in app.text_input if t.label.startswith('无法重算')).set_value('本例未保留重算边界')
    submit()
    assert not app.exception and not app.error
    sidecar,=archived.glob('RT_PRICE_REVIEW*.json')
    assert json.loads(sidecar.read_bytes())['source']=='da_proxy_rt'
    assert all((archived/k).read_bytes()==v for k,v in original.items())
    fresh=AppTest.from_file(str(PROGRAM/'app/四川.py'),default_timeout=30).run()
    fresh.selectbox(key='scv5_archive').select(archived.name).run()
    assert not fresh.exception and not fresh.error
    assert any('同段日前参考作为实时代理' in i.value for i in fresh.info)
    assert not any(b.label=='保存本时段价格复核' for b in fresh.button)
    fresh.selectbox(key='scv5_period_'+archived.name).select(1).run()
    assert not fresh.exception and not fresh.error
    assert not any('同段日前参考作为实时代理' in i.value for i in fresh.info)
    assert len(list(archived.glob('RT_PRICE_REVIEW*.json')))==1


def test_upload_requires_exact_run_digest_quarter_and_unique_nodes(monkeypatch):
    from decimal import Decimal
    monkeypatch.syspath_prepend(str(PROGRAM/'app'))
    from sichuan_v5_page import _uploaded_rerun
    raw=b'{"run_id":"a","rt_sha256":"b","quarter":1,"record_id":"run1","nodal_yuan_mwh":{"8":33.333}}'
    assert _uploaded_rerun(raw,'a','b',1).vector.nodal_yuan_mwh=={8:Decimal('33.333')}
    with pytest.raises(ValueError,match='未绑定'):
        _uploaded_rerun(raw,'wrong','b',1)
    with pytest.raises(ValueError,match='重复字段'):
        _uploaded_rerun(raw.replace(b'33.333',b'33.333,"8":34'), 'a','b',1)
