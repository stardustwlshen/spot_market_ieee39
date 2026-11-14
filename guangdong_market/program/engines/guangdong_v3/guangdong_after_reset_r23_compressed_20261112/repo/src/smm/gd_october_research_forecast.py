"""Deterministic exogenous priors, not fitted to historical future actuals.

Seeded SHA256 phases make the curves reproducible without PRNG/version state.
All knots, including tomorrow's tail, are generated from one declared formula.
No observations, historical profiles, CSV row repetition or model results enter.
"""
from datetime import timedelta
from hashlib import sha256
import math
from .gd_october_forecast_inputs import (SNAPSHOT_SCHEMA, LINEAGE_SCHEMA, canonical_bytes,
    source_ref, _time, _iso, _keys, _require, parse_bytes)

METHOD='synthetic_exogenous_harmonic_prior_v1'
PRIOR_SCHEMA='gd.october.exogenous_prior.v1'


def prior_value(prior, resource, at):
    p=prior['resources'][resource];h=(_time(at,'target')-_time(prior['epoch'],'epoch')).total_seconds()/3600
    # An explicit clock-time model. The tiny trend is included in every hour,
    # not a hidden last-row extension at midnight.
    phase=int(sha256(f"{prior['seed']}:{resource}".encode()).hexdigest()[:8],16)/2**32*2*math.pi
    f=prior['family'];scale=p['scale_mw']
    if f=='load_mw':v=scale*(.91+.09*math.cos(2*math.pi*(h-18)/24)+.025*math.sin(4*math.pi*h/24+phase)+.0002*h)
    elif f=='wind_mw':v=scale*(.38+.10*math.sin(2*math.pi*h/18+phase)+.035*math.cos(2*math.pi*h/7+phase))
    elif f=='solar_mw':v=scale*.72*max(0.,math.sin(math.pi*((h%24)-6)/12)) if 6<=h%24<=18 else 0.
    else:v=scale*(.70+.05*math.cos(2*math.pi*h/24+phase)+.0001*h)
    return round(v,9)


def verify_prior_snapshot(snapshot, artifacts):
    settings=snapshot['construction']['settings'];_keys(settings,('prior_ref',),'research forecast settings')
    ref=settings['prior_ref'];_keys(ref,('source_id','source_sha256'),'prior reference')
    raw=artifacts[ref['source_id']]
    _require(sha256(raw).hexdigest()==ref['source_sha256'],'source_hash_mismatch','prior','prior bytes changed')
    prior=parse_bytes(raw,'prior')
    _keys(prior,('schema_version','source_kind','method','seed','epoch','family','resources','published_at',
        'available_at','information_end','uses_future_actual','original_refs'),'prior')
    _require(prior['schema_version']==PRIOR_SCHEMA and prior['source_kind']=='research_assumption' and
        prior['method']==METHOD and type(prior['seed']) is int and prior['uses_future_actual'] is False,
        'invalid_prior','prior','explicit synthetic prior required')
    _require(snapshot['provenance']['kind']=='synthetic_research_forecast' and snapshot['value_semantics']=='instantaneous_power',
        'invalid_prior','snapshot','research instantaneous hypotheses required')
    _require(prior['family']==snapshot['family'] and set(prior['resources'])==set(snapshot['resource_ids']),
        'invalid_prior','prior','resource mismatch')
    _require(all(_time(prior[k],k)<=_time(snapshot['provenance']['conditioning_information_cutoff'],'conditioning') for k in ('published_at','available_at','information_end')),
        'future_conditioning','prior','prior unavailable before issue')
    # Independent numerical reconstruction, not a trusted producer flag.
    for row in snapshot['rows']:
        _require(row['value']==prior_value(prior,row['resource_id'],row['target_at']),
            'derived_value_mismatch','prior','synthetic knot differs from declared formula')


def make_prior_snapshot(*, family, resources, day_start, seed, static_refs, static_artifacts):
    start=_time(day_start,'day_start');declared=start-timedelta(days=1);issue=start-timedelta(minutes=30)
    end=start+timedelta(days=1,minutes=105)
    prior=dict(schema_version=PRIOR_SCHEMA,source_kind='research_assumption',method=METHOD,seed=seed,
        epoch=_iso(start),family=family,resources=resources,published_at=_iso(declared),available_at=_iso(declared),
        information_end=_iso(declared),uses_future_actual=False,original_refs=static_refs)
    sid='research-prior:'+family;raw=canonical_bytes(prior);artifacts=dict(static_artifacts);artifacts[sid]=raw
    lineage=dict(schema_version=LINEAGE_SCHEMA,kind='fixed_exogenous_research',source_uri='research://ieee39/exogenous-prior',
        published_at=_iso(declared),available_at=_iso(declared),information_end=_iso(declared),uses_future_actual=False,
        original_refs=[source_ref(sid,raw)])
    lid='research-lineage:'+family;artifacts[lid]=canonical_bytes(lineage)
    snapshot=dict(schema_version=SNAPSHOT_SCHEMA,snapshot_id='research-snapshot:'+family,family=family,unit='MW',
        resource_ids=list(resources),source_uri='research://ieee39/synthetic-exogenous-prior',issued_at=_iso(issue),available_at=_iso(issue),
        provenance=dict(kind='synthetic_research_forecast',uses_future_actual=False,conditioning_information_cutoff=_iso(declared)),
        input_sources=[source_ref(lid,artifacts[lid])],construction=dict(method=METHOD,settings=dict(prior_ref=source_ref(sid,raw))),
        native_interval_minutes=15,value_semantics='instantaneous_power',coverage=[dict(delivery_start=_iso(start-timedelta(minutes=30)),delivery_end=_iso(end))],
        rows=[dict(resource_id=r,target_at=_iso(start+timedelta(minutes=k)),value=prior_value(prior,r,start+timedelta(minutes=k)))
            for k in range(-30,1546,15) for r in resources])
    raw=canonical_bytes(snapshot);artifacts[snapshot['snapshot_id']]=raw
    return dict(snapshot_ref=source_ref(snapshot['snapshot_id'],raw),source_artifacts=artifacts,prior=prior)
