"""Independent contract tests. Baseline abd1cb7 is expected to FAIL these cases.
Run with PYTHONPATH=<repo>/src:<repo>/tests python -m pytest -q this_file.
No market optimization is replaced in the extraction test; it exercises the actual
state_values extractor with a structural model/dual mapping.
"""
from types import SimpleNamespace as NS
import numpy as np
import pytest
from smm import rolling
from smm.config import load_config
from smm.input_validation import validate_time_grid
from test_handoff_integrity import context, id_round


def model_for(cfg, da, size):
    model = NS(_md=NS(T=size,
        pumped={u['id']:{'e_gen':u['e_gen']} for u in cfg.units['pumped']},
        hydro={u['id']:{'water_rate':u['water_rate']} for u in cfg.units['hydro']}),dual={})
    for kind, group, con, key in [('E','S','c_soc','E'),('Vu','PS','c_pres','Vu'),('V','HS','c_water','Vh')]:
        subjects=list(da[key]);setattr(model, group, subjects)
        constraints={(u,t):(kind,u,t) for u in subjects for t in range(size)}
        setattr(model, con, constraints)
        model.dual.update({constraint:-42. for constraint in constraints.values()})
    return model


@pytest.mark.parametrize('bad_dual',['missing','none','nan','inf','text'])
def test_one_invalid_subject_does_not_discard_other_valid_id_values(context,bad_dual):
    cfg, da=context;tau=4
    r,_=id_round(da,tau)
    r['model']=m=model_for(cfg,da,cfg.T-tau)
    damaged,unaffected=list(da['E'])[:2]
    constraint=m.c_soc[damaged,0]
    if bad_dual=='missing':del m.dual[constraint]
    else:m.dual[constraint]={'none':None,'nan':np.nan,'inf':np.inf,'text':'bad'}[bad_dual]
    result=rolling.intraday_reference(da,da,r,cfg.T,cfg=cfg)
    assert result['reference_metadata']['E'][damaged]['trajectory_source']=='day_ahead'
    assert result['reference_metadata']['E'][unaffected]['trajectory_source']==f'intraday@{tau}'
    assert result['state_values']['E'][unaffected]['value_native'][tau]==42.


@pytest.mark.parametrize('lead',[-1,1.9,True,'2'])
def test_invalid_intraday_issue_lead_must_fail_closed(lead):
    cfg=load_config();cfg.mode['intraday'].update(enabled=True,lead_issue_periods=lead)
    with pytest.raises(ValueError,match='lead_issue_periods'):
        validate_time_grid(cfg)


@pytest.mark.parametrize('lead',[0,1,4])
def test_valid_intraday_issue_lead_still_accepted(lead):
    cfg=load_config();cfg.mode['intraday'].update(enabled=True,lead_issue_periods=lead)
    validate_time_grid(cfg)


def test_intraday_table_clock_and_first_hour_follow_validated_dt():
    cfg=load_config();cfg.base.update(dt_h=.5,periods=48)
    cfg.mode['realtime'].update(interval_min=30,lookahead_h=2)
    cfg.mode['intraday'].update(enabled=True,interval_h=1)
    validate_time_grid(cfg)
    snap={name:{} for name in ['P','Pw','Ph','Pd','delta']}
    snap.update(surplus=np.zeros(4),load_bus={1:np.full(4,100.)})
    r=dict(tau=4,snap=snap,prices={'lmp_clamped':np.array([[10.,20.,300.,400.]])},
        mip={'time_s':0.,'gap':0.,'objective':0.},repriced=False,slack={})
    rr=NS(id_rounds=[r])
    tables=rolling.intraday_tables(NS(case=NS(cfg=cfg,net=NS(buses=[1]))),rr)
    assert tables['summary'].iloc[0]['round']=='02:00'
    assert tables['summary'].iloc[0]['lmp_avg_first_hour']==15.


@pytest.mark.parametrize('lead',[-1,1.9,True,'2',None,float('nan'),float('inf')])
def test_direct_id_call_rejects_bad_lead_even_if_disabled(context,lead):
    cfg,da=context;cfg.mode['intraday'].update(enabled=False,lead_issue_periods=lead)
    with pytest.raises(ValueError,match='lead_issue_periods'):
        rolling.run_intraday_round(NS(cfg=cfg),da,cfg,rolling.initial_state(cfg),0,log=lambda *a:None)


def test_da_value_extraction_still_fails_closed(context):
    from smm import scuc
    cfg,da=context;m=model_for(cfg,da,4)
    del m.dual[next(iter(m.dual))]
    with pytest.raises(KeyError):scuc.state_values(m)
