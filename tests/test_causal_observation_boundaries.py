"""Independent adversarial cases: never truncate availability or skip public events."""
import math
import numpy as np
import pytest
from smm.causal_types import *
from smm.causal_forecast import CausalForecaster,FilterState,propagate
from smm.causal_presets import FORECAST,POLICIES

START='2026-07-15T00:00:00Z'

@pytest.mark.parametrize('key,value',[('starts',1784073600.5),('available_at',1784074500.9),
    ('available_at',float('nan')),('available_at',float('inf')),('available_at',True),
    ('available_at',2**64),('available_at','1784074500')])
def test_invalid_time_rejected_before_coercion(key,value):
    data=dict(resource_id='W',starts=[1784073600],values=[50.],available_at=[1784074500])
    data[key]=[value]
    with pytest.raises(ValueError,match='whole seconds'): ObservationSeries(**data)


def test_integer_valued_seconds_and_arrival_boundary():
    s=ObservationSeries('W',[1784073600.],[50.],[1784074501.])
    with pytest.raises(ValueError,match='unavailable'): ObservationPrefix(utc_iso(1784074500),(s,))
    assert ObservationPrefix(utc_iso(1784074501),(s,)).series[0].available_at[0]==1784074501


def test_public_event_inside_observation_gap_is_not_erased():
    base=utc_seconds(START);r=ResourceSpec('W','wind',100.)
    event=PublicEvent('published','W',START,utc_iso(base+3600),utc_iso(base+9*3600),.9)
    f=CausalForecaster((r,),'UTC',history_start_utc=START,public_events=(event,))
    # Interval 10:00-10:15 becomes available before the delayed issue at 12:00.
    at=base+10*3600+450
    s=ObservationSeries('W',[base+10*3600],[50.],[base+11*3600+900])
    state=f._state(r,s,'delayed',(event,))
    fp=FORECAST['wind'];m=math.log(.9/.1)
    expected=propagate(FilterState(base,0.,0.),base+3600,0.,fp)
    expected=propagate(expected,base+9*3600,m,fp)
    expected=propagate(expected,at,0.,fp)
    expected_level=expected.level*(1-POLICIES['delayed']['level'])
    assert expected_level>0.7
    assert state.level==pytest.approx(expected_level,abs=1e-12)
    # The exact same prefix request replay gives the same immutable publication.
    req=PredictionRequest(utc_iso(base+12*3600),utc_iso(base+12*3600),utc_iso(base+14*3600),policy_id='delayed')
    a=f.issue(req,ObservationPrefix(req.issue_time_utc,(s,)))
    g=CausalForecaster((r,),'UTC',history_start_utc=START,public_events=(event,))
    assert a.content_hash==g.issue(req,ObservationPrefix(req.issue_time_utc,(s,))).content_hash


def test_fractional_observation_delays_round_up_at_every_gateway():
    from smm.causal_processes import ProcessPlan,generate_actual
    from dataclasses import replace
    r=ResourceSpec('W','wind',100.,public_params={'observation_delay_minutes':.01})
    store=generate_actual(ProcessPlan(START,1,'UTC',(r,)))
    end=utc_seconds(START)+900
    assert max(store.observations(end).series[0].starts)<utc_seconds(START)
    assert max(store.observations(end+1).series[0].starts)==utc_seconds(START)
    assert max(store.observations(end+1,{'W':.01}).series[0].starts)<utc_seconds(START)
    assert max(store.observations(end+2,{'W':.01}).series[0].starts)==utc_seconds(START)
    with pytest.raises(ValueError,match='nonnegative'):store.observations(end,{'W':-.01})
    f=CausalForecaster((r,),'UTC',history_start_utc=START)
    req=PredictionRequest(utc_iso(end),utc_iso(end),utc_iso(end+900),policy_id='responsive')
    too_early=ObservationPrefix(req.issue_time_utc,(ObservationSeries('W',[utc_seconds(START)],[50.],[end]),))
    with pytest.raises(ValueError,match='delay'):f.issue(req,too_early)
