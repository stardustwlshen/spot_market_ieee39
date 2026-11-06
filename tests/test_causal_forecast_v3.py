from dataclasses import replace
import numpy as np
import pytest
from smm.causal_types import *
from smm.causal_processes import *
from smm.causal_forecast import CausalForecaster
from smm.causal_presets import capabilities

START='2026-07-15T00:00:00Z'
RES=(ResourceSpec('W1','wind',100.,'wind_A'),ResourceSpec('W2','wind',80.,'wind_A'),
     ResourceSpec('PV1','pv',100.,'pv_A'),ResourceSpec('load','load',1000.),ResourceSpec('H1','natural_inflow',200.,'catchment_A'))

@pytest.fixture(scope='module')
def store(): return generate_actual(ProcessPlan(START,3,'UTC',RES,root_seed='42'))

def forecaster(store,events=()):
    return CausalForecaster(store.plan.resources,store.plan.timezone,store.plan.root_seed,store.plan.realization_id,
        history_start_utc=utc_seconds(START)-PRE_ROLL_DAYS*86400,public_events=events)

def issue(f,store,t=0,stage='DA',policy='baseline',end=96):
    a=utc_seconds(START)
    req=PredictionRequest(utc_iso(a+t*900),utc_iso(a+t*900),utc_iso(a+end*900),stage,policy)
    return f.issue(req,store.observations(req.issue_time_utc,f.extra_delays(policy)))

def test_process_bounds_and_night(store):
    for r in RES:
        a=store.actual(r.resource_id,START,store.plan.end_utc)
        assert np.isfinite(a).all() and (a>=0).all()
        if r.kind in ('wind','pv'): assert (a<=r.scale).all()
    starts=np.arange(utc_seconds(START),utc_seconds(store.plan.end_utc),900)
    clear=solar_envelope(RES[2],(starts[:,None]+[150,450,750]).ravel(),'UTC').reshape(-1,3).mean(axis=1)
    actual=store.actual('PV1',START,store.plan.end_utc)
    assert (actual[clear==0]==0).all() and (actual<=clear).all()
    assert np.corrcoef(store.values['W1'][:-1],store.values['W1'][1:])[0,1]>.8
    assert np.corrcoef(store.values['W1'],store.values['W2'])[0,1]>.2
    water=store.values['H1']; assert np.max(abs(np.diff(water)))<5

def test_future_private_cards_do_not_change_forecast(store):
    changed=generate_actual(replace(store.plan,day_specs={'2026-07-16':{'wind':'wind_high','pv':'pv_overcast','load':'load_high','natural_inflow':'inflow_pulse'}}))
    f1,f2=forecaster(store),forecaster(changed)
    a,b=issue(f1,store),issue(f2,changed)
    assert a.content_hash==b.content_hash
    assert np.array_equal(store.actual('W1',START,'2026-07-16T00:00:00Z'),changed.actual('W1',START,'2026-07-16T00:00:00Z'))
    assert not np.array_equal(store.values['W1'],changed.values['W1'])

def test_extension_and_resource_order_independence(store):
    more=generate_actual(replace(store.plan,day_count=4,resources=tuple(reversed(RES))))
    for r in RES: assert np.array_equal(store.values[r.resource_id],more.values[r.resource_id][:len(store.starts)])
    extra=generate_actual(replace(store.plan,resources=RES+(ResourceSpec('Wnew','wind',17.,'other'),)))
    assert np.array_equal(extra.values['W1'],store.values['W1'])

def test_absolute_random_stream_chunks():
    a=random_slots('42','0','actual.test','W1',1000,20)
    b=random_slots('42','0','actual.test','W1',1007,5)
    assert np.array_equal(a[0][7:12],b[0]) and np.array_equal(a[1][7:12],b[1])

def test_policy_does_not_mutate_actual_and_stage_is_not_noise(store):
    before=store.content_hash; f=forecaster(store)
    a,b=issue(f,store,stage='DA'),issue(f,store,stage='RT')
    assert a.content_hash==b.content_hash
    changed=issue(f,store,policy='delayed')
    assert changed.content_hash!=a.content_hash and store.content_hash==before

def test_ordered_and_reverse_issues_same_outputs(store):
    f=forecaster(store); first=issue(f,store,t=0)
    later=issue(f,store,t=48)
    again=issue(f,store,t=0)
    assert first.content_hash==again.content_hash
    assert issue(forecaster(store),store,t=48).content_hash==later.content_hash
    assert f.stats['cache_extensions']>0 and f.stats['replayed_observations']>0
    state=f.filter_state_evidence()
    assert all(utc_seconds(s['effective_at'])<utc_seconds(START) for s in state.values())

def test_published_frame_immutable(store):
    f=forecaster(store); a=issue(f,store); old=a.content_hash
    issue(f,store,t=80)
    assert a.content_hash==old
    with pytest.raises(Exception): a.series[0].values[0]=1
    with pytest.raises(ValueError): store.values['W1'].setflags(write=True)

def test_availability_gate_and_missing_resource(store):
    f=forecaster(store); req=PredictionRequest(START,START,'2026-07-16T00:00:00Z')
    unfiltered=store.observations(START)
    with pytest.raises(ValueError,match='delay'): f.issue(req,unfiltered)
    with pytest.raises(ValueError,match='future'): ObservationPrefix(START,(ObservationSeries('X',[utc_seconds(START)],[1.],[utc_seconds(START)+900]),))
    p=store.observations(START,f.extra_delays('baseline'))
    with pytest.raises(ValueError,match='resource'): f.issue(req,replace(p,series=p.series[:-1]))

def test_late_observation_replay(store):
    f=forecaster(store); req=PredictionRequest('2026-07-15T06:00:00Z','2026-07-15T06:00:00Z','2026-07-16T00:00:00Z')
    full=store.observations(req.issue_time_utc,f.extra_delays('baseline'))
    # A middle observation arrives after a later-effective record; gateway returns sorted prefix.
    s=full.series[0]; mask=np.ones(len(s.starts),bool); mask[-3]=False
    missing=ObservationSeries(s.resource_id,s.starts[mask],s.values[mask],s.available_at[mask])
    f.issue(req,replace(full,series=(missing,)+full.series[1:]))
    corrected=f.issue(req,full)
    assert corrected.content_hash==forecaster(store).issue(req,full).content_hash
    assert f.stats['replayed_observations']>0

def test_public_event_release_boundary(store):
    e=PublicEvent('wx1','W1','2026-07-15T06:00:00Z','2026-07-15T08:00:00Z','2026-07-16T00:00:00Z',.8)
    f=forecaster(store,(e,))
    assert issue(f,store).content_hash==issue(forecaster(store),store).content_hash
    assert next(s for s in issue(f,store,t=24).series if s.resource_id=='W1')!=next(s for s in issue(forecaster(store),store,t=24).series if s.resource_id=='W1')

def test_selected_inflow_and_current_observation(store):
    frame=issue(forecaster(store),store); s=frame.select(START,'2026-07-15T02:00:00Z')
    assert len(s.values('H1',kind='natural_inflow'))==8
    with pytest.raises(ValueError): s.values('H_missing',kind='natural_inflow')
    with pytest.raises(ValueError): s.values('H1',end='2026-07-16T00:00:00Z')
    obs=store.current_measurement(START); current=s.with_current_measurement(obs)
    assert current.values('H1')[0]==obs['H1']
    assert np.array_equal(current.values('H1')[1:],s.values('H1')[1:])
    assert current.sources[0]=='current_interval_perfect_measurement'

def test_validation_grid_params_and_dst():
    with pytest.raises(ValueError): ProcessPlan(START,1,'UTC',(ResourceSpec('W','wind',100),),{'2026-07-15':{'wind':{'preset':'wind_normal','parameters':{'slow_h':0}}}})
    with pytest.raises(ValueError,match='DST'): ProcessPlan('2026-03-08T05:00:00Z',1,'America/New_York',RES)
    with pytest.raises(ValueError,match='15-minute'): PredictionRequest('2026-07-15T00:01:00Z',START,'2026-07-16T00:00:00Z')
    assert capabilities()['calibrated'] is False


def test_independent_resource_policies(store):
    f=forecaster(store)
    req=PredictionRequest(START,START,'2026-07-16T00:00:00Z',policy_overrides={'wind':'persistent_high','pv':'delayed'})
    frame=f.issue(req,store.observations(START,f.extra_delays(req)))
    base=issue(forecaster(store),store)
    left={s.resource_id:s.values for s in frame.series}; right={s.resource_id:s.values for s in base.series}
    assert left['W1']!=right['W1'] and left['PV1']!=right['PV1']
    assert left['load']==right['load'] and left['H1']==right['H1']


def test_absolute_time_normalization_and_unit_validation(store):
    f=forecaster(store)
    req=PredictionRequest('2026-07-15T08:00:00+08:00','2026-07-15T00:00:00+00:00','2026-07-16T00:00:00Z')
    got=f.issue(req,store.observations(req.issue_time_utc,f.extra_delays(req)))
    assert got.content_hash==issue(forecaster(store),store).content_hash
    with pytest.raises(ValueError,match='unit'): ResourceSeries('H','natural_inflow','local_natural_qin_m3s','MW',(1.,))
    with pytest.raises(ValueError,match='boolean'): ResourceSeries('H','natural_inflow','local_natural_qin_m3s','m3/s',(True,))
    rows=list(got.series); copy=replace(got,series=rows);rows.clear()
    assert len(copy.series)==len(got.series)
