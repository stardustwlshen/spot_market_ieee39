"""The expert ZIP is one verified run, not a recursive folder download."""
import hashlib
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

PROGRAM = Path(__file__).resolve().parents[1]


def test_widget_reopen_package_and_readback(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(PROGRAM/'app'))
    from market_rule_profile import MarketProfile, SICHUAN_V5
    from sichuan_v5_price_review import Incident, ReviewRequest
    from sichuan_v5_workflow import evidence_zip, load, review
    root = tmp_path/'original'/SICHUAN_V5.results_folder
    monkeypatch.setattr(MarketProfile, 'default_results', lambda self, _:root)
    captured = {}
    original = st.download_button

    def capture(label, *args, **kw):
        if label=='下载原始档案 ZIP' and kw.get('mime')=='application/zip':
            captured[kw['file_name']]=kw['data']
        return original(label, *args, **kw)

    monkeypatch.setattr(st,'download_button',capture)
    page = str(PROGRAM/'app/四川.py')
    first = AppTest.from_file(page,default_timeout=90).run()
    first.button(key='scv5_run').click().run()
    assert not first.error and not first.exception
    archive, = root.iterdir()
    name = archive.name
    before = load(root,name)
    hashes = before['receipt']['data_files']
    reviewed = review(root,name,ReviewRequest(name,hashes['RT_EXECUTED.json'],0,
                        Incident('program_fault','test-zip-q0','synthetic no rerun')))
    (archive/'private.txt').write_text('unlisted should never enter ZIP')
    (root/'another-run').mkdir()
    fresh = AppTest.from_file(page,default_timeout=40).run()
    fresh.selectbox(key='scv5_archive').select(name).run()
    assert fresh.button(key='scv5_run').disabled and not captured
    fresh.button(key='scv5_evidence_prepare_'+name).click().run()
    assert not fresh.error and not fresh.exception
    assert any(x.label=='下载原始档案 ZIP' for x in fresh.get('download_button'))
    data = captured[name+'_原始复核档案.zip']
    members = set(hashes)|{'RECEIPT.json','RT_PRICE_REVIEW_Q00.json'}
    dest = tmp_path/'fresh'/SICHUAN_V5.results_folder
    (dest/name).mkdir(parents=True)
    with ZipFile(BytesIO(data)) as zipped:
        assert zipped.namelist()==[f'{name}/{p}' for p in sorted(members)]
        assert zipped.testzip() is None
        for filename in members:
            raw=zipped.read(f'{name}/{filename}')
            assert raw==(archive/filename).read_bytes()
            if filename in hashes:
                assert hashlib.sha256(raw).hexdigest()==hashes[filename]
            (dest/name/filename).write_bytes(raw)
    assert load(dest,name)==reviewed
    assert len(list(root.iterdir()))==2
    p=archive/'RT_EXECUTED.json'
    p.write_bytes(p.read_bytes()+b'\n')
    with pytest.raises(ValueError,match='结果校验不匹配'):
        evidence_zip(root,name)
