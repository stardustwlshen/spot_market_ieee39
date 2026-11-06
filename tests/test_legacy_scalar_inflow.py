from smm import scuc
from smm.natural_inflow import model_natural
from test_hydro_rolling import hydro_case

def test_legacy_absolute_window_keeps_scalar_without_clipping_at_day_end():
    case=hydro_case(T=24);case.t0=1;case.T=None
    md=scuc.prepare(case)
    assert md.T==24
    assert 'qin_series' not in md.hydro['H1']
    assert all(model_natural(md,'H1',t)==50. for t in range(24))
    m=scuc.build_model(case)
    assert len(m.c_water)==24
