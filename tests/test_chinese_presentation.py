"""Display translation must not alter saved quantities, accounting, or raw exports."""
from pathlib import Path
import sys
import numpy as np
import pandas as pd
import plotly.graph_objects as go
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
from chinese_labels import display_frame,csv_bytes,localize_figure

def test_chinese_copy_preserves_numbers_missing_and_raw_export():
    raw=pd.DataFrame({'unit':['ES1','H1'],'type':['storage','hydro'],'cash_revenue_yuan':[-25.,np.nan],'cost_available':[True,False]})
    before=raw.copy(deep=True)
    shown=display_frame(raw)
    pd.testing.assert_frame_equal(raw,before)
    assert shown['资源类型'].tolist()==['储能','水电']
    assert shown['净能量现金结算 元'].iloc[0]==-25
    assert pd.isna(shown['净能量现金结算 元'].iloc[1])
    assert csv_bytes(raw,False)==raw.to_csv(index=False).encode('utf-8-sig')
    assert '净能量现金结算 元' in csv_bytes(raw).decode('utf-8-sig')

def test_settlement_power_vs_aggregate_energy_units():
    assert '日前结算电量 MWh' in display_frame(pd.DataFrame({'q_da':[12.]}))
    assert any('MW' in c and 'MWh' not in c for c in display_frame(pd.DataFrame({'period':[0],'q_da':[12.]})).columns)

def test_plot_labels_do_not_modify_series_or_secondary_axes():
    fig=go.Figure(go.Scatter(x=[0,1],y=[-5.,7.],name='storage',yaxis='y2'))
    fig.update_layout(yaxis=dict(title='P_mw'),yaxis2=dict(title='库容 万m³',overlaying='y',side='right',range=[0,10]))
    result=localize_figure(fig)
    assert list(result.data[0].y)==[-5.,7.] and result.data[0].yaxis=='y2'
    assert result.data[0].name=='储能'
    assert result.layout.yaxis.title.text=='净出力 MW'
    assert result.layout.yaxis2.title.text=='库容 万m³' and list(result.layout.yaxis2.range)==[0,10]

def test_static_chinese_heatmap_preserves_source_matrix(tmp_path,monkeypatch):
    from smm import plots
    import matplotlib.pyplot as plt
    import warnings
    prices=pd.DataFrame({'bus':[1,1,2,2],'period':[0,1,0,1],'lmp':[-5.,10.,15.,20.]})
    captured=[]
    original_close=plt.close
    monkeypatch.setattr(plt,'close',lambda fig=None: captured.append(fig) if hasattr(fig,'axes') else original_close(fig))
    with warnings.catch_warnings(record=True) as notes:
        warnings.simplefilter('always')
        plots.lmp_heatmap(prices,tmp_path/'中文节点电价.png')
    assert not [w for w in notes if 'Glyph' in str(w.message)]
    np.testing.assert_array_equal(captured[-1].axes[0].images[0].get_array(),[[-5.,10.],[15.,20.]])
    assert '节点电价' in captured[-1].axes[0].get_title()
    original_close(captured[-1])
