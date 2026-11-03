"""Read-only stage evidence and callback boundary tests; tiny single-period market."""
from types import SimpleNamespace
from unittest.mock import Mock

import pyomo.environ as pyo
import pytest

from smm import da, scuc, regulation
from smm.rolling import snapshot
from smm.stage_diagnostics import capture
from test_regulation import case_with_agc


@pytest.fixture(scope='module')
def stage_result():
    return da.run_da(case_with_agc(), verbose=False)


def test_pre_aux_final_cover_all_units_and_final_is_authoritative(stage_result):
    result=stage_result
    assert set(result.stage_diagnostics)=={'scuc_preplan','agc_auxiliary','energy_sced'}
    expected={'CHEAP','LOCAL'}
    for name, data in result.stage_diagnostics.items():
        assert {r['unit'] for r in data['units']}==expected
        assert {r['period'] for r in data['units']}=={0}
        assert data['summary']['prices_are_market_prices']==(name=='energy_sced')
        if name!='energy_sced':
            assert '不执行、不结算' in data['summary']['status']
    aux=result.stage_diagnostics['agc_auxiliary']['summary']
    assert aux['objective_kind']=='regulation_score_only_not_payment'
    sn=snapshot(result.model)
    for row in result.stage_diagnostics['energy_sced']['units']:
        assert row['P_mw']==pytest.approx(sn['P'][row['unit']][0])
        assert row['on']==int(sn['u'][row['unit']][0])
        assert row['q_reg_mw']==pytest.approx(sn['regulation']['q_mw'][row['unit']][0])


def _model_fingerprint(m):
    variables=[(v.name,v.value,v.fixed,str(v.lb),str(v.ub),str(v.domain))
               for v in m.component_data_objects(pyo.Var,active=None)]
    constraints=[(c.name,str(c.expr),c.active) for c in m.component_data_objects(pyo.Constraint,active=None)]
    objectives=[(o.name,str(o.expr),o.active,o.sense) for o in m.component_data_objects(pyo.Objective,active=None)]
    return variables,constraints,objectives


def test_capture_does_not_mutate_any_variable_constraint_or_objective(stage_result):
    m=stage_result.model
    before=_model_fingerprint(m)
    info={'objective':123.,'termination':'test'}
    data=capture(m,'agc_auxiliary',info)
    assert _model_fingerprint(m)==before
    assert info=={'objective':123.,'termination':'test'}
    data['units'][0]['P_mw']=-999
    assert _model_fingerprint(m)==before  # numeric evidence is detached


def test_capture_all_resource_families_and_null_noneligible_values():
    md=SimpleNamespace(thermal={'G':{'type':'coal'}},ren={'W':{'type':'wind'}},
                       hydro_units={'H':{}},storage={'S':{}},pumped={'PS':{}})
    m=SimpleNamespace(_md=md,_case=SimpleNamespace(t0=12),_agc={'enabled':True},T=[0],AGC={'G'},
        q_agc={('G',0):3.},P={('G',0):10.},u={('G',0):1.},Pw={('W',0):20.},
        Ph={('H',0):30.},JZ=[('H',0)],delta={('H',0,0):1.},
        Pd={('S',0):5.},Pc={('S',0):0.},u_dis={('S',0):1.},u_ch={('S',0):0.},E={('S',0):40.},
        Pg={('PS',0):0.},Pp={('PS',0):50.},xg={('PS',0):0.},xp={('PS',0):1.},Vu={('PS',0):60.})
    # The exporter now retains full integer metadata; model the mock with actual
    # VarData instead of plain floats so it has the same interface as production.
    missing = {'v': {('G',0):0.}, 'w': {('G',0):0.},
               'ngen': {('PS',0):0.}, 'npump': {('PS',0):1.}}
    for name in scuc.INT_VARS:
        values = getattr(m,name,missing.get(name,{}))
        variables = {}
        for index,value in values.items():
            var = pyo.Var(initialize=value); var.construct(); variables[index] = var
        setattr(m,name,variables)
    diagnostic=capture(m,'energy_sced')
    assert {row['variable'] for row in diagnostic['integer_values']} == {name for name in scuc.INT_VARS if len(getattr(m,name))}
    rows={r['unit']:r for r in diagnostic['units']}
    assert set(rows)=={'G','W','H','S','PS'}
    assert all(r['period']==12 for r in rows.values())
    assert rows['W']['on'] is None
    assert rows['G']['q_reg_mw']==3.
    assert all(rows[u]['q_reg_mw'] is None for u in ('W','H','S','PS'))
    assert rows['S']['E_mwh']==40. and rows['S']['P_dis_mw']==5.
    assert rows['PS']['V_up_1e4m3']==60. and rows['PS']['P_mw']==-50.


@pytest.mark.parametrize('stop_stage', ['scuc_preplan','agc_auxiliary','energy_sced'])
def test_stage_callback_failure_propagates_at_exact_boundary(monkeypatch,stage_result,stop_stage):
    m=stage_result.model.clone()
    monkeypatch.setattr(scuc,'build_model',lambda case:m)
    monkeypatch.setattr(regulation,'prepare_energy',lambda m:None)
    monkeypatch.setattr(scuc,'solve_scuc',lambda *a:dict(termination='optimal',time_s=0.,objective=pyo.value(m.obj),gap=0.))
    auction=Mock(return_value={'objective':1.})
    solve_sced=Mock(return_value={'objective':pyo.value(m.obj),'time_s':0.})
    monkeypatch.setattr(regulation,'clear_reservations',auction)
    monkeypatch.setattr(scuc,'solve_sced',solve_sced)
    stages=[]
    def callback(event):
        assert event['phase']=='da_stage'
        stages.append(event['stage'])
        assert set(event['stage_diagnostics'])==set(stages)
        if event['stage']==stop_stage:raise OSError('stage evidence write failed')
    with pytest.raises(OSError,match='stage evidence write failed'):
        da.run_da(stage_result.case,verbose=False,stage_callback=callback)
    expected=['scuc_preplan','agc_auxiliary','energy_sced']
    assert stages==expected[:expected.index(stop_stage)+1]
    assert auction.call_count==int(stop_stage!='scuc_preplan')
    assert solve_sced.call_count==int(stop_stage=='energy_sced')
