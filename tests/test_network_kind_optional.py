from pathlib import Path
import shutil
import sys
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
from network_view import network_data,topology_figure,branch_table
from smm.config import load_config
from smm.network import build_network

@pytest.mark.parametrize('kind',[None,'unknown'])
def test_optional_kind_preserves_physics_and_marks_unknown(tmp_path,kind):
    for folder in ('config','data'):
        shutil.copytree(ROOT/folder,tmp_path/folder)
    path=tmp_path/'data/case39/branches.csv'
    table=pd.read_csv(path)
    if kind is None:
        table=table.drop(columns='kind')
    else:
        table['kind']=kind
    table.to_csv(path,index=False)
    before=path.read_bytes()
    model=build_network(load_config(root=tmp_path))
    data=network_data(tmp_path)
    assert (data['branches'].kind=='unknown').all()
    pd.testing.assert_series_equal(data['branches'].rate_eff,model.branches.rate_mw,check_names=False)
    assert set(branch_table(data['branches'])['类型'])=={'类型未记录'}
    figure=topology_figure(data)
    branch_traces=[trace for trace in figure.data if trace.legendgroup=='支路（类型未记录，点线）']
    assert len(branch_traces)==len(table)
    assert all(trace.line.dash=='dot' and '类型未记录' in trace.name for trace in branch_traces)
    app=AppTest.from_string(f"""
from pathlib import Path
from smm.config import load_config
from network_view import render_input_network
render_input_network(load_config(root=Path({str(tmp_path)!r})),'optional_kind')
""",default_timeout=60).run()
    assert not app.exception
    assert any('46 条支路未记录设备类型' in x.value for x in app.caption)
    assert path.read_bytes()==before
