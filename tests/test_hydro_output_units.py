"""Hydro stage exporters and old/new UI labels preserve native m3/s values."""
import sys
from pathlib import Path
import pytest
import pyomo.environ as pyo
from smm.stage_diagnostics import capture
from test_hydro_reserve_water import water_case
from test_storage_reserve import solve
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from stage_results import intraday_state_rows
from chinese_labels import COLUMNS


@pytest.mark.parametrize('dt',[.25,.5,1.])
def test_stage_flow_is_m3s_and_does_not_receive_dt_or_volume_conversion(dt):
    c=water_case(power=20.,water_rate=1.2,dt=dt,headroom=100.)
    m=solve(c)
    q=1.2*pyo.value(m.Ph['H1',0])+pyo.value(m.Qspill['H1',0])
    row=capture(m,'scuc_preplan')['hydro_states'][0]
    assert row['Qout_m3s']==pytest.approx(q)
    assert 'Qout_1e4m3_h' not in row
    assert row['V_1e4m3']==pytest.approx(pyo.value(m.V['H1',0]))


def test_intraday_saved_flow_unit_and_value_are_both_native():
    evidence={'id_rounds':[{'tau':4,'snapshot':{'Qout':{'H1':[365.76,560.]}}}]}
    rows=intraday_state_rows(evidence,4)
    assert rows['单位'].tolist()==['m³/s','m³/s']
    assert rows['数值'].tolist()==[365.76,560.]
    assert rows.period.tolist()==[4,5]
    assert evidence['id_rounds'][0]['snapshot']['Qout']['H1']==[365.76,560.]


@pytest.mark.parametrize('key',['Qout_m3s','Qout_1e4m3_h'])
def test_current_and_legacy_stage_keys_have_correct_display_unit(key):
    assert 'm³/s' in COLUMNS[key]
    assert '万m³/h' not in COLUMNS[key]
    if key=='Qout_1e4m3_h':
        assert '历史字段名误写' in COLUMNS[key]
