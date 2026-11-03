from types import SimpleNamespace as NS
import pytest
from smm.results import hydro_results


@pytest.mark.parametrize('limit',[None,0.,1219.2])
def test_total_hydro_outflow_limit_preserves_none_zero_and_source(limit):
    d=dict(units=['H'],zones=[[0,100]],water_rate=2.,qin=50.,qout_max=limit,
           qout_max_source=None if limit is None else 'user approved engineering scenario')
    m=NS(_md=NS(T=1,hydro={'H':d}),Ph={('H',0):10.},delta={('H',0,0):1},
         V={('H',0):100.},Qspill={('H',0):3.})
    row=hydro_results(m).iloc[0]
    assert row.qout_m3s==23. # generation AND spill, not merely turbine flow
    assert row.qout_limit_active==(limit is not None)
    if limit is None:
        assert row.qout_max_m3s is None
        assert row.qout_limit_margin_m3s is None
        assert row.qout_max_source is None
    else:
        assert row.qout_max_m3s==limit
        assert row.qout_limit_margin_m3s==limit-23.
        assert row.qout_max_source=='user approved engineering scenario'
