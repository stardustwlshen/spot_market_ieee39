"""Source-bound IEEE39 DA98 input envelope. Pure reads, no solver or old runs.

Forecasts, representative mappings and boundary choices are caller-authored.
This module neither repairs feasibility nor turns historical evidence into a
causal forecast, and never mints a solved SCED certificate.
"""
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import csv
import io
import json
import yaml
from .gd_r14_da_contract import (DA98Contract, canonical, number, require, stamp, text, validate_contract)

SCHEMA='gd.r14.da98.inputs.v1'
NORMALIZED_STATIC_SHA256='4bb2fe561aebdbe094784ef0ff87eade6045b15e0b1ac2528ab2bb8966b14823'
# Copied immutable original source identity, without importing any old builder.
STATIC_SHA256={
    'config/modes.yaml':'7b8b885474816e7eb2a39903a6f49147b2f0ec34d6f134d134450e2bac27bf3a',
    'config/base.yaml':'05e3c6cef9c2af2e64858db8deb181693a93059e8ab70fa9b50c2744ac50a62b',
    'config/units.yaml':'4045e99ef2a0f30a43e062410f5d2acd7dff42fd70910b9abac73aae2fa978c6',
    'data/case39/buses.csv':'cf865656bcf1417fe3c470c0529f96ec2be18601f7ef0a6b4a6d084a105e6b60',
    'data/case39/branches.csv':'3770230cf8a851cdfc694a049d524fade7e5703d4a313357e74b1afc9302a471',
    'resolved_bids.json':'85ae59a2607f2e2d9c8f9fefb54c11e85720e9f17afe29fdaf45c365eec5d0b7',
}


@dataclass(frozen=True)
class DA98Inputs:
    canonical_bytes: bytes
    sha256: str

    def __post_init__(self):
        require(type(self.canonical_bytes) is bytes and sha256(self.canonical_bytes).hexdigest()==self.sha256,'input envelope digest mismatch')
        d=json.loads(self.canonical_bytes)
        require(canonical(d)==self.canonical_bytes,'input envelope not canonical')
        validate_contract(d['contract'])
        require(d.get('schema')==SCHEMA and d.get('official_equivalence_verified') is False,'input envelope schema/scope')
        require(d.get('execution_authority') is False and d.get('solved_sced_certificate') is None,'input envelope cannot mint execution/solve authority')
        expected=_validated_envelope(static=d['static'],hashes=d['static_sha256'],contract=d['contract'],forecasts=d['forecasts'],boundary_policy=d['boundary_policy'],sources=d['sources'],boundary_source_id=d['boundary_source_id'],forecast_source_ids=d['forecast_source_ids'],reserve_requirements=d['reserve_requirements'],qualifications=d['qualifications'])
        require(canonical(expected)==self.canonical_bytes,'input envelope validation/derived metadata mismatch')

    def payload(self):
        return json.loads(self.canonical_bytes)


def validate_source(record, *, cutoff):
    """Self-contained source bytes/hash, with publication and information gates.

    Simulated issuance is explicitly research-only, not authenticated history.
    Hash equality proves integrity, not authenticity of the declared provenance.
    """
    require(type(record) is dict,'source record required')
    require(record.get('kind') in ('retained_static','synthetic_exogenous_prior','synthetic_da_vintage','research_boundary_assumption','research_mapping_assumption','published_causal_forecast'),'unsupported/actual-conditioned source kind')
    text(record.get('source'), 'source provenance')
    require('payload' in record,'source payload required')
    require(sha256(canonical(record['payload'])).hexdigest()==record.get('sha256'),'source payload hash mismatch')
    gate=stamp(cutoff,'cutoff')
    available=stamp(record.get('available_at'),'source available_at')
    published=stamp(record.get('published_at'),'source published_at')
    information_end=stamp(record.get('information_end'),'source information_end')
    require(published<=available<=gate and information_end<=gate,'source unavailable at cutoff or future information leakage')
    require(record.get('uses_future_actual') is False,'future actual-conditioned input prohibited')
    require(type(record.get('availability_is_simulated')) is bool,'source simulated availability flag required')
    if record['kind'].startswith(('synthetic_', 'research_')):
        require(record['availability_is_simulated'],'research source cannot claim actual issuance')
    return deepcopy(record)


def _load_original(root):
    folder=Path(root)/'inputs/ieee39_v6'
    raw={name:(folder/name).read_bytes() for name in STATIC_SHA256}
    actual={name:sha256(blob).hexdigest() for name,blob in raw.items()}
    require(actual==STATIC_SHA256,'original IEEE39 static byte identity changed')
    result={name:yaml.safe_load(raw[name]) for name in ('config/modes.yaml','config/base.yaml','config/units.yaml')}
    result['resolved_bids.json']=json.loads(raw['resolved_bids.json'])
    for name in ('data/case39/buses.csv','data/case39/branches.csv'):
        result[name]=list(csv.DictReader(io.StringIO(raw[name].decode())))
    return result,actual


def _boundary(policy, units, source):
    require(type(policy) is dict and policy.get('kind') in ('original_initial','independent_preonline_research'),'explicit original or independent preonline boundary required')
    require(source['kind']=='research_boundary_assumption','boundary needs declared research source')
    require(source['payload']==policy,'boundary declaration must match its bound source exactly')
    require(policy.get('original_all_gas_off_reachability_proven') is False,'independent boundary cannot prove original reachability')
    original={u['id']:deepcopy(u['init']) for u in units['thermal']}
    selected=policy.get('thermal')
    require(type(selected) is dict and set(selected)==set(original),'complete thermal boundary inventory required')
    diffs={}
    for u in units['thermal']:
        uid=u['id']; row=selected[uid]
        require(set(row)==set(original[uid]),'retain complete original thermal state fields')
        require(type(row['online']) in (int,bool) and row['online'] in (0,1),'boundary online must be binary')
        number(row['p0'],'boundary power'); number(row['hours'],'boundary residence')
        require((row['online']==0 and row['p0']==0) or (row['online']==1 and float(u['pmin'])<=row['p0']<=float(u['pmax'])),'boundary stable power inconsistent with status')
        if row!=original[uid]:diffs[uid]={'original':original[uid],'selected':deepcopy(row)}
    if policy['kind']=='original_initial': require(not diffs,'original initial boundary changed')
    else: text(policy.get('assumption'),'independent preonline assumption')
    return dict(kind=policy['kind'],original=original,selected=deepcopy(selected),differences=diffs,original_all_gas_off_reachability_proven=False,source_sha256=source['sha256'])


def prepare_ieee39_da98(root, *, contract, forecasts, boundary_policy,
                        sources, boundary_source_id, forecast_source_ids,
                        reserve_requirements, qualifications):
    """Prepare normalized, immutable input envelope; no numeric model build.

    forecasts = {load_mw|wind_mw|solar_mw|boundary_tie_mw:
                 {node_id: {market_point_id: MW}}}.
    Each family must equal its source payload. Caller supplies explicit values
    at representative times; this function does not extrapolate any source.
    """
    static,hashes=_load_original(root)
    envelope=_validated_envelope(static=static,hashes=hashes,contract=contract,forecasts=forecasts,boundary_policy=boundary_policy,sources=sources,boundary_source_id=boundary_source_id,forecast_source_ids=forecast_source_ids,reserve_requirements=reserve_requirements,qualifications=qualifications)
    blob=canonical(envelope)
    return DA98Inputs(blob,sha256(blob).hexdigest())


def _validated_envelope(*,static,hashes,contract,forecasts,boundary_policy,sources,
                        boundary_source_id,forecast_source_ids,reserve_requirements,qualifications):
    require(hashes==STATIC_SHA256 and sha256(canonical(static)).hexdigest()==NORMALIZED_STATIC_SHA256,'embedded original static identity changed')
    c=contract if isinstance(contract,DA98Contract) else validate_contract(contract)
    d=c.payload(); units=static['config/units.yaml']
    require(type(sources) is dict and bool(sources),'source inventory required')
    checked={k:validate_source(v,cutoff=d['information_cutoff']) for k,v in sources.items()}
    require(boundary_source_id in checked,'boundary source missing')
    boundary=_boundary(boundary_policy,units,checked[boundary_source_id])
    families={'load_mw','wind_mw','solar_mw','boundary_tie_mw'}
    require(set(forecasts)==families and set(forecast_source_ids)==families,'four separate forecast families required')
    nodes={str(r['bus']) for r in static['data/case39/buses.csv']}; points={p['id'] for p in d['grid']}
    for family,values in forecasts.items():
        require(type(values) is dict and set(values)==nodes,'forecast node coverage: '+family)
        sid=forecast_source_ids[family]
        require(sid in checked and checked[sid]['kind'] in ('synthetic_da_vintage','published_causal_forecast'),'forecast source type/identity')
        require(checked[sid].get('contract_sha256')==c.sha256,'forecast must bind exact point semantics/weights contract: '+family)
        require(checked[sid]['payload']==values,'forecast differs from source bytes: '+family)
        for node,series in values.items():
            require(type(series) is dict and set(series)==points,'forecast 98-point coverage: '+family+'/'+node)
            for value in series.values(): number(value,'forecast MW')
    required={'positive_mw':400.,'negative_mw':0.,'primary_mw':0.,'contingency_mw':400.}
    require(type(reserve_requirements) is dict and set(reserve_requirements)==set(required),'explicit four reserve families required')
    for family,expected in required.items():
        series=reserve_requirements[family]
        require(type(series) is dict and set(series)==points,'reserve 98-point coverage')
        require(all(type(v) in (int,float) and math_isfinite(v) and v==expected for v in series.values()),'original reserve requirement changed: '+family)
    resource_ids={u['id'] for group in ('thermal','renewable','storage','pumped','tie') for u in units.get(group,[])}
    require(type(qualifications) is dict and set(qualifications)==resource_ids,'qualification inventory must preserve all original resources')
    thermal={u['id'] for u in units['thermal']}
    for uid,row in qualifications.items():
        require(set(row)=={'positive','negative','primary','contingency','agc','source'},'qualification record keys')
        text(row['source'],'qualification source')
        require(all(type(row[k]) is bool for k in ('positive','negative','primary','contingency','agc')),'qualification booleans required')
        if uid not in thermal:
            require(not any(row[k] for k in ('positive','negative','primary','contingency','agc')),'unqualified resource cannot gain incremental reserves/AGC')
    envelope=dict(schema=SCHEMA,contract=d,contract_sha256=c.sha256,static=static,static_sha256=hashes,sources=checked,source_authenticity_verified=False,forecasts=deepcopy(forecasts),forecast_source_ids=deepcopy(forecast_source_ids),boundary=boundary,boundary_policy=deepcopy(boundary_policy),boundary_source_id=boundary_source_id,reserve_requirements=deepcopy(reserve_requirements),qualifications=deepcopy(qualifications),official_equivalence_verified=False,execution_authority=False,solved_sced_certificate=None,scope='fixed-known-state DA98 research inputs; free UC and original boundary reachability not proven')
    return envelope


def math_isfinite(value):
    import math
    return math.isfinite(value)
