import hashlib
import json
from pathlib import Path
import sys

import pandas as pd
from streamlit.testing.v1 import AppTest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
from output_browser import indexed_artifacts


def archive(directory):
    data=pd.DataFrame({'period':[0,1], 'unit':['TIE','C1'], 'P_mw':[10.,20.]})
    data.to_csv(directory/'rt_units.csv',index=False)
    item={'file':'rt_units.csv','scope':'实时已执行首段','sha256':hashlib.sha256((directory/'rt_units.csv').read_bytes()).hexdigest()}
    (directory/'output_manifest.json').write_text(json.dumps({'artifacts':[item]}))


def test_index_rejects_traversal_and_missing_records(tmp_path):
    archive(tmp_path)
    manifest=json.loads((tmp_path/'output_manifest.json').read_text())
    manifest['artifacts'] += [{'file':'../private.csv'}, {'file':'missing.csv'}, {'file':'sub\\private.csv'}]
    (tmp_path/'output_manifest.json').write_text(json.dumps(manifest))
    base,items=indexed_artifacts(tmp_path)
    assert base == tmp_path and [x['file'] for x in items] == ['rt_units.csv']


def test_output_browser_filter_and_unknown_history(tmp_path):
    archive(tmp_path)
    app=AppTest.from_string(f"from output_browser import render_output_browser\nrender_output_browser({str(tmp_path)!r})").run()
    assert not app.exception
    assert len(app.dataframe[0].value)==2
    next(x for x in app.multiselect if x.label=='机组').set_value(['TIE']).run()
    assert not app.exception
    assert len(app.dataframe[0].value)==1
    assert '理想化研究假设' in ' '.join(x.value for x in app.caption)
    assert len(app.get('download_button'))==3
    # Altering an archived file is reported, not silently trusted.
    (tmp_path/'rt_units.csv').write_text('period,unit,P_mw\n0,TIE,99\n')
    app.run()
    assert any('不一致' in x.value for x in app.warning)
    old=tmp_path/'old';old.mkdir()
    history=AppTest.from_string(f"from output_browser import render_output_browser\nrender_output_browser({str(old)!r})").run()
    assert not history.exception and not history.selectbox


def test_broken_optional_csv_keeps_selector_and_raw_download(tmp_path):
    archive(tmp_path)
    broken = tmp_path / 'diagnostic.csv'
    broken.write_text('period,unit,value\n0,C1,"unclosed')
    manifest = json.loads((tmp_path / 'output_manifest.json').read_text())
    manifest['artifacts'].insert(0, dict(file=broken.name, sha256=hashlib.sha256(broken.read_bytes()).hexdigest()))
    (tmp_path / 'output_manifest.json').write_text(json.dumps(manifest))
    app = AppTest.from_string(f"from output_browser import render_output_browser\nrender_output_browser({str(tmp_path)!r})").run()
    assert not app.exception
    assert any('此文件无法解析' in x.value for x in app.error)
    assert len(app.get('download_button')) == 2  # raw file and index, not a fabricated parsed table
    app.selectbox[0].set_value('rt_units.csv').run()
    assert not app.exception and not app.error
    assert len(app.dataframe[0].value) == 2
