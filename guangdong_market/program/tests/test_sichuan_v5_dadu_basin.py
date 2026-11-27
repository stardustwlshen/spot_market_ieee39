from copy import deepcopy
from pathlib import Path

import pytest


@pytest.fixture
def model(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'app'))
    import sichuan_v5_dadu_basin
    return sichuan_v5_dadu_basin


def example():
    # Nonuniform initial queue: in the first two intervals B receives 180, 360 m³.
    # A and B sequentially turbine the same water. Only B's total release exits.
    units=[{'id':'A','name':'上库','model':'reservoir'},{'id':'B','name':'末站','model':'run_of_river'}]
    reservoirs=[{'id':'A','initial_storage_m3':1000,'regulation':'reservoir'},
                {'id':'B','initial_storage_m3':0,'regulation':'run_of_river'}]
    edges=[{'upstream':'A','downstream':'B','travel_quarters':2,'initial_arrivals_m3':[180,360]}]
    rows=[]
    q=[180.,360.]
    for t in range(4):
        prior=q[0]
        q=q[1:]+[900.]
        # Upper station releases 900m³; lower releases its arrival and 180m³ of local water.
        b_rate=(prior+180)/900
        upper={'storage_m3':1000,'turbine_m3s':.8,'eco_bypass_m3s':.1,'water_spill_m3s':.1,
               'release_m3s':1.,'upstream_arrival_m3':0.,'natural_inflow_m3s':1.}
        lower={'storage_m3':0,'turbine_m3s':b_rate-.1,'eco_bypass_m3s':0.,'water_spill_m3s':.1,
               'release_m3s':b_rate,'upstream_arrival_m3':prior,'natural_inflow_m3s':.2}
        rows.append({'quarter':t,'power':{'A':8.,'B':5.},'hydro':{'A':upper,'B':lower},
                     'water_handoff':{'in_transit':[{'upstream':'A','downstream':'B','next_arrivals_m3':q}],
                                      'basin_exit_m3':{'B':prior+180.}}})
    da=[{k:deepcopy(v) for k,v in row.items() if k!='water_handoff'} for row in rows]
    return {'inputs':{'stations':units,'available':[{'A':9.,'B':8.} for _ in rows],
                      'market_case':{'cascade':{'reservoirs':reservoirs,'reaches':edges}}},
            'rt':rows,'da':da}


def test_whole_basin_counts_local_inputs_and_outlet_once(model):
    data=model.basin_tables(example())
    for stage in data.values():
        assert [r['transit_end_m3'] for r in stage['periods']]==[1260,1800,1800,1800]
        b=model.basin_balance(stage['periods'])
        assert (b['n'],b['start'],b['end'])==(4,'00:00','01:00')
        assert (b['storage_begin_m3'],b['storage_end_m3'])==(1000.,1000.)
        assert (b['river_begin_m3'],b['river_end_m3'])==(540.,1800.)
        assert b['inflow_m3']==4320 and b['outlet_m3']==3060
        assert b['residual_m3']==0
        assert b['spill_m3']==720. and b['generation_mwh']==13 and b['capacity_mwh']==17
    # Independently preserved RT queue corruption must show up in the audit.
    r=example()
    r['rt'][-1]['water_handoff']['in_transit'][0]['next_arrivals_m3'][0] += 45
    results=model.basin_tables(r)
    assert model.basin_balance(results['rt']['periods'])['residual_m3']==-45
    assert model.basin_balance(results['da']['periods'])['residual_m3']==0


def test_short_missing_and_discontinuous_archive_never_fills_unknown_with_zero(model):
    r=example()
    r['rt']=r['rt'][:1]
    del r['rt'][0]['hydro']['B']['natural_inflow_m3s']
    r['rt'][0]['power'].pop('B')
    del r['inputs']['available'][0]['B']
    del r['inputs']['market_case']['cascade']['reaches'][0]['initial_arrivals_m3']
    phases=model.basin_tables(r)
    d=model.basin_balance(phases['rt']['periods'])
    assert d['end']=='00:15' and d['generation_mwh'] is None and d['capacity_mwh'] is None
    assert d['inflow_m3'] is None and d['residual_m3'] is None and d['river_begin_m3'] is None
    assert d['river_end_m3']==1260
    assert [p['transit_end_m3'] for p in phases['da']['periods']]==[None,1800,1800,1800]
    r=example()
    del r['rt'][1]
    assert model.basin_balance(model.basin_tables(r)['rt']['periods'])['residual_m3'] is None


def test_disconnected_topology_cannot_close_the_river(model):
    r=example()
    r['inputs']['market_case']['cascade']['reaches']=[]
    data=model.basin_tables(r)
    assert model.basin_balance(data['da']['periods'])['outlet_m3'] is None
    # The preserved RT outlet meter is still usable, but not sufficient to infer
    # river stock when the topology is missing.
    rt=model.basin_balance(data['rt']['periods'])
    assert rt['outlet_m3']==3060 and rt['river_begin_m3'] is None and rt['residual_m3'] is None
