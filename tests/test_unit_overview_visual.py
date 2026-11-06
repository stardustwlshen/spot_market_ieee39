from pathlib import Path
import sys
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
from unit_overview import comparison_figure, POWER
from common import DA_COLOR, RT_COLOR

def test_same_stage_style_and_missing_values_remain_unconnected():
    table=pd.DataFrame({'时刻':['00:00','00:15','00:30'],POWER['da']:[1.,2.,3.],POWER['id']:[np.nan,4.,5.],POWER['rt']:[6.,7.,np.nan]})
    original=table.copy(deep=True)
    figure=comparison_figure(table)
    assert [trace.name for trace in figure.data]==list(POWER.values())
    assert [trace.line.color for trace in figure.data]==[DA_COLOR,'#2e8b80',RT_COLOR]
    assert [trace.line.dash for trace in figure.data]==['solid','dot','dash']
    for trace,label in zip(figure.data,POWER.values()):
        np.testing.assert_allclose(trace.y,table[label],equal_nan=True)
        assert list(trace.x)==table['时刻'].tolist()
        assert trace.connectgaps is False
    pd.testing.assert_frame_equal(table,original)
