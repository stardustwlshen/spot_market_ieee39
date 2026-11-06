"""Detach completed ID models without changing their numeric evidence or fallback rules.

Only formal economic SCED duals are read. The handoff's per-resource tolerance
and the historical audit's strict bundle remain separate, including errors.
"""
from __future__ import annotations
import copy
import numpy as np
from . import scuc
from .state_bridge import digest

VERSION = 2
SOURCE = 'formal_energy_SCED_existing_duals_no_resolve'
ERROR_TYPES = {'KeyError': KeyError, 'TypeError': TypeError, 'ValueError': ValueError}


def _payload(record):
    return {key: value for key, value in record.items() if key != 'evidence_hash'}


def _positive_factor(value):
    if type(value) not in (int, float) or not np.isfinite(value) or value <= 0:
        raise ValueError('Invalid original ID value conversion parameter')
    return float(value)


def conversion_parameters(cfg):
    """Independent archive anchor from effective day inputs, never from a cache."""
    season = cfg.base['season']
    return dict(source='effective_day_configuration',
        storage=[u['id'] for u in cfg.units['storage']],
        pumped={u['id']:dict(e_gen=float(u['e_gen'])) for u in cfg.units['pumped']},
        hydro={u['id']:dict(water_rate=float(dict(u, **u['seasons'][season])['water_rate'])) for u in cfg.units['hydro']})


def _parameter_factors(parameters):
    if (not isinstance(parameters, dict) or set(parameters) != {'source','storage','pumped','hydro'}
            or parameters['source'] != 'effective_day_configuration'
            or not isinstance(parameters['storage'], list)
            or any(not isinstance(u,str) for u in parameters['storage'])
            or len(set(parameters['storage'])) != len(parameters['storage'])):
        raise ValueError('Missing independent original ID value parameters')
    result = {'E': {u:1. for u in parameters['storage']}, 'Vu': {}, 'V': {}}
    for kind, group, field in (('Vu','pumped','e_gen'),('V','hydro','water_rate')):
        if not isinstance(parameters[group],dict):raise ValueError('Invalid original ID parameter resources')
        for unit, row in parameters[group].items():
            if not isinstance(unit,str) or not isinstance(row,dict) or set(row)!={field}:
                raise ValueError('Invalid original ID value parameter row')
            value=_positive_factor(row[field])
            result[kind][unit]=1./value if kind=='Vu' else .36*value
    return result


def _model_factors(model, values):
    result={kind:{} for kind in ('E','Vu','V')}
    for kind, units in values.items():
        for unit in units:
            try:
                value=(1. if kind=='E' else 1./_positive_factor(float(model._md.pumped[unit]['e_gen']))
                       if kind=='Vu' else .36*_positive_factor(float(model._md.hydro[unit]['water_rate'])))
            except (AttributeError,KeyError,TypeError) as exc:
                raise ValueError('Original SCED conversion parameter is missing') from exc
            result[kind][unit]=_positive_factor(value)
    return result


def _validate_values(values, periods, factors):
    if not isinstance(values, dict) or set(values) != {'E', 'Vu', 'V'}:
        raise ValueError('ID cached values must contain the exact state families')
    if not isinstance(factors,dict) or set(factors)!={'E','Vu','V'}:
        raise ValueError('Missing frozen ID conversion factors')
    for kind, units in values.items():
        if not isinstance(units, dict):
            raise ValueError('Invalid ID cached resource mapping')
        if not isinstance(factors[kind],dict) or set(factors[kind])!=set(units):
            raise ValueError('Frozen ID conversion factors do not cover valid resources')
        for unit, row in units.items():
            if not isinstance(unit, str) or not isinstance(row, dict):
                raise ValueError('Invalid ID cached state record')
            for key in ('value_native', 'value_yuan_per_mwh', 'dual_raw'):
                a = row.get(key)
                if not isinstance(a, np.ndarray) or a.dtype != np.dtype('float64') or a.shape != (periods,) or not np.isfinite(a).all():
                    raise ValueError('ID cached state values lost dtype, precision or coverage')
            factor=_positive_factor(factors[kind][unit])
            native=np.r_[-row['dual_raw'][1:],0.] if periods>1 else np.array([0.])
            if not np.array_equal(row['value_native'],native):
                raise ValueError('ID continuation values must equal shifted negative raw duals with a zero tail')
            if not np.array_equal(row['value_yuan_per_mwh'],native*factor):
                raise ValueError('ID converted values differ from the original parameter factor')
            if row.get('native_unit') != ('元/MWh' if kind=='E' else '元/万m³'):
                raise ValueError('Invalid original ID value native unit')
            terminal=row.get('terminal_target_dual')
            if not isinstance(terminal,dict) or type(terminal.get('available')) is not bool:
                raise ValueError('Missing original terminal-target dual availability')
            if terminal['available']:
                value=terminal.get('value_native');converted=terminal.get('value_yuan_per_mwh')
                if (type(value) not in (float,int) or not np.isfinite(value)
                        or type(converted) not in (float,int) or not np.isfinite(converted)
                        or converted!=value*factor):
                    raise ValueError('Terminal-target dual conversion differs from the original factor')
            elif terminal.get('value_native') is not None or terminal.get('value_yuan_per_mwh') is not None:
                raise ValueError('Unavailable terminal-target dual must remain unknown')


def cached_state_evidence(round_result):
    """Validate an explicit cache; never silently reconstruct a damaged cache."""
    record = round_result.get('state_value_evidence')
    if not isinstance(record, dict) or type(record.get('schema_version')) is not int or record.get('schema_version') != VERSION or record.get('source') != SOURCE:
        raise ValueError('Missing or invalid completed-ID state value evidence')
    expected = {'schema_version', 'source', 'window_start', 'periods', 'values',
                'value_error', 'resource_errors', 'strict_audit', 'conversion_factors', 'evidence_hash'}
    if set(record) != expected or record['evidence_hash'] != digest(_payload(record)):
        raise ValueError('Completed-ID state value evidence hash/fields mismatch')
    tau, periods = round_result.get('tau'), len(round_result.get('snap', {}).get('surplus', []))
    if type(tau) is not int or type(record['window_start']) is not int or record['window_start'] != tau or type(record['periods']) is not int or record['periods'] != periods or periods <= 0:
        raise ValueError('Completed-ID state value evidence clock/coverage mismatch')
    _validate_values(record['values'], periods, record['conversion_factors'])
    if record['value_error'] is not None and (not isinstance(record['value_error'], str) or not record['value_error'].strip()):
        raise ValueError('Invalid cached ID value error')
    errors = record['resource_errors']
    if not isinstance(errors, list) or any(not isinstance(row, dict) or set(row) != {'kind', 'unit', 'reason'}
            or row['kind'] not in ('E','Vu','V') or not isinstance(row['unit'],str) or (not isinstance(row['reason'],str) or not row['reason'].strip()) for row in errors):
        raise ValueError('Invalid cached ID resource errors')
    if len({(r['kind'],r['unit']) for r in errors}) != len(errors):
        raise ValueError('Duplicate cached ID resource errors')
    snapshot=round_result.get('snap')
    if not isinstance(snapshot,dict) or any(key not in snapshot or not isinstance(snapshot[key],dict) for key in ('E','Vu','Vh')):
        raise ValueError('Missing ID snapshot state-resource coverage')
    expected_resources = {kind:set(round_result['snap'].get(key,{})) for kind,key in (('E','E'),('Vu','Vu'),('V','Vh'))}
    errors_by_kind = {kind:{r['unit'] for r in errors if r['kind']==kind} for kind in expected_resources}
    for kind, expected_units in expected_resources.items():
        present = set(record['values'][kind]); invalid = errors_by_kind[kind]
        if not invalid <= expected_units or present & invalid:
            raise ValueError('Cached ID errors refer to absent or successful resources')
        if record['value_error'] is None:
            if present | invalid != expected_units:
                raise ValueError('ID cached resource coverage is incomplete without an explicit error')
        elif present or invalid:
            raise ValueError('Global missing-ID-value evidence must not claim partial values')
    strict = record['strict_audit']
    if not isinstance(strict, dict) or set(strict) != {'status', 'values', 'error_type', 'error_message'}:
        raise ValueError('Missing strict ID audit evidence')
    if strict['status'] == 'values':
        _validate_values(strict['values'], periods, record['conversion_factors'])
        if any(set(strict['values'][k]) != units for k,units in expected_resources.items()):
            raise ValueError('Strict ID audit values must cover all snapshot state resources')
        if strict['error_type'] is not None or strict['error_message'] is not None:
            raise ValueError('Conflicting strict ID audit evidence')
        # Both successful reads come from the same unchanged formal SCED duals.
        # A rehashed cache must not give handoff and offline audit different values.
        if record['value_error'] is not None or errors or digest(record['values']) != digest(strict['values']):
            raise ValueError('Conflicting same-source handoff and strict ID values')
    elif strict['status'] == 'unavailable':
        if strict['values'] is not None or strict['error_type'] not in (None, 'KeyError'):
            raise ValueError('Invalid unavailable ID audit evidence')
    elif strict['status'] == 'error':
        if strict['values'] is not None or strict['error_type'] not in ('TypeError', 'ValueError') or not isinstance(strict['error_message'], str):
            raise ValueError('Invalid strict ID audit error')
    else:
        raise ValueError('Unknown strict ID audit status')
    return record


def capture_state_evidence(round_result):
    """Capture once, preserving the two pre-existing consumer contracts.

    Two read-only traversals of the same unchanged SCED dual suffix preserve the
    strict audit behavior; neither traversal runs a solver or changes a dual.
    """
    if 'state_value_evidence' in round_result:
        return cached_state_evidence(round_result)
    if round_result.get('models_retained') is False:
        raise ValueError('Detached ID is missing its state value evidence')
    m = round_result.get('model'); errors = {}; error = None
    values = {'E': {}, 'Vu': {}, 'V': {}}
    strict = dict(status='unavailable', values=None, error_type=None, error_message=None)
    if m is None or not hasattr(m, 'dual'):
        error = 'intraday_SCED_model_or_duals_missing'
    else:
        try:
            values = scuc.state_values(m, errors=errors)
        except KeyError as exc:
            error = f'intraday_SCED_dual_missing: {exc}'
        try:
            strict.update(status='values', values=scuc.state_values(m))
        except KeyError as exc:
            strict.update(error_type='KeyError', error_message=str(exc))
        except (TypeError, ValueError) as exc:
            strict.update(status='error', error_type=type(exc).__name__, error_message=str(exc))
    record = copy.deepcopy(dict(schema_version=VERSION, source=SOURCE,
        window_start=round_result['tau'], periods=len(round_result['snap']['surplus']),
        values=values, value_error=error, conversion_factors=_model_factors(m,values),
        resource_errors=[dict(kind=k, unit=u, reason=reason) for (k,u),reason in sorted(errors.items())],
        strict_audit=strict))
    record['evidence_hash'] = digest(record)
    round_result['state_value_evidence'] = record
    return cached_state_evidence(round_result)


def handoff_values(round_result):
    record = cached_state_evidence(round_result)
    return record['values'], record['value_error'], {(r['kind'],r['unit']):r['reason'] for r in record['resource_errors']}


def strict_audit_values(round_result):
    record = cached_state_evidence(round_result)['strict_audit']
    if record['status'] == 'error':
        raise ERROR_TYPES[record['error_type']](record['error_message'])
    return record['values']


def detach_id_models(round_result):
    """Return the same numeric objects after required completed-stage evidence checks."""
    cached_state_evidence(round_result)
    required = {'tau','issue_t','snap','prices','dispatch_prices','pricing_info','slack',
                'stage_diagnostics','mip','sced','solution_quality','repriced','pricing_source'}
    if not required <= set(round_result):
        raise ValueError('Cannot detach ID models before complete numeric evidence capture')
    stages = round_result['stage_diagnostics']
    wanted = {'scuc_preplan','energy_sced'} | ({'auxiliary_pricing'} if round_result['repriced'] else set())
    if not isinstance(stages,dict) or not wanted <= set(stages):
        raise ValueError('Cannot detach ID models with missing stage diagnostics')
    if round_result['repriced'] and not isinstance(round_result['pricing_info'],dict):
        raise ValueError('Cannot detach ID auxiliary model without pricing evidence')
    for name in wanted:
        if not isinstance(stages[name],dict) or not {'units','summary','integer_values','prices','hydro_states'} <= set(stages[name]):
            raise ValueError('Cannot detach ID models with incomplete stage evidence')
    if not isinstance(round_result['sced'],dict) or 'hydro_water_selection' not in round_result['sced']:
        raise ValueError('Cannot detach ID model without water-selection certificate')
    result = {key:value for key,value in round_result.items() if key not in ('model','pricing_model')}
    result['models_retained'] = False
    return result


def validate_compact_rounds(rr):
    if getattr(rr, 'id_models_retained', True) is not False:
        return
    for row in rr.id_rounds:
        if row.get('models_retained') is not False or 'model' in row or 'pricing_model' in row:
            raise ValueError('Archive-only ID history must have completed numeric evidence and no models')
        detach_id_models(row)  # Pure validation; does not recapture missing evidence.


def archive_state_evidence(rr):
    """Additional audit record; existing output numerics remain unchanged."""
    from .state_bridge import plain
    validate_compact_rounds(rr)
    return dict(schema_version=VERSION, array_encoding='float64_json_numbers',
        source=SOURCE, rounds=[dict(tau=r['tau'], evidence=plain(cached_state_evidence(r)))
                              for r in rr.id_rounds if 'state_value_evidence' in r])


def validate_archived_state_evidence(archive):
    """Verify cache coverage/hash after JSON read-back, never repair absent fields."""
    if not isinstance(archive,dict) or set(archive) != {'schema_version','array_encoding','source','rounds'} or type(archive['schema_version']) is not int or archive['schema_version'] != VERSION or archive['array_encoding'] != 'float64_json_numbers' or archive['source'] != SOURCE or not isinstance(archive['rounds'],list):
        raise ValueError('Invalid archived ID value evidence')
    previous=-1;result=[]
    for row in archive['rounds']:
        if not isinstance(row,dict) or set(row) != {'tau','evidence'} or type(row['tau']) is not int or row['tau'] <= previous:
            raise ValueError('Invalid archived ID evidence ordering')
        previous=row['tau'];record=copy.deepcopy(row['evidence'])
        if not isinstance(record,dict) or type(record.get('periods')) is not int or not 0 < record['periods'] <= 100000:
            raise ValueError('Invalid archived ID evidence horizon')
        for values in (record.get('values'), (record.get('strict_audit') or {}).get('values')):
            if values is None:continue
            if not isinstance(values,dict):raise ValueError('Invalid archived ID values')
            for units in values.values():
                if not isinstance(units,dict):raise ValueError('Invalid archived ID resource mapping')
                for item in units.values():
                    for key in ('value_native','value_yuan_per_mwh','dual_raw'):
                        raw=item.get(key)
                        if not isinstance(raw,list) or any(type(v) not in (float,int) for v in raw):
                            raise ValueError('Invalid archived ID numeric array')
                        item[key]=np.array(raw,dtype=np.float64)
        keys={kind:set((record.get('values') or {}).get(kind,{})) | {r['unit'] for r in record.get('resource_errors',[]) if r.get('kind')==kind} for kind in ('E','Vu','V')}
        strict=(record.get('strict_audit') or {}).get('values') or {}
        for kind in keys:keys[kind]|=set(strict.get(kind,{}))
        snapshot={'surplus':np.empty(record['periods']),**{key:{u:None for u in keys[kind]} for kind,key in (('E','E'),('Vu','Vu'),('V','Vh'))}}
        result.append(cached_state_evidence(dict(tau=row['tau'],snap=snapshot,state_value_evidence=record)))
    return result


ARCHIVE_CONTRACT = dict(schema_version=VERSION, mode='numeric_ID_evidence_v2',
                        evidence_file='id_state_value_evidence.json', source=SOURCE)


def audit_archive(directory, summary):
    """Official offline check, binding caches to archived ID clocks/resources/values.

    Historical outputs without a marker and without a sidecar retain their old
    scope. A declared new contract cannot silently downgrade to that scope.
    """
    from pathlib import Path
    import json
    import pandas as pd
    root=Path(directory);marker=summary.get('id_result_retention')
    sidecar=root/ARCHIVE_CONTRACT['evidence_file']
    if marker is None:
        if sidecar.exists():raise ValueError('ID value sidecar exists without its declared archive contract')
        return dict(active=False,valid=None,scope='legacy_archive_without_numeric_ID_contract')
    if marker != ARCHIVE_CONTRACT or type(marker.get('schema_version')) is not int:
        raise ValueError('Unknown ID numeric archive contract')
    archive=json.loads(sidecar.read_text(encoding='utf-8'))
    records=validate_archived_state_evidence(archive)
    anchored=_parameter_factors(summary.get('id_state_value_parameters'))
    for record in records:
        for kind,units in record['conversion_factors'].items():
            for unit,factor in units.items():
                if unit not in anchored[kind] or factor!=anchored[kind][unit]:
                    raise ValueError('ID cache conversion factor differs from independent original input parameters')
    hydro=json.loads((root/'hydro_window_evidence.json').read_text(encoding='utf-8'))
    saved=hydro.get('id_rounds')
    if not isinstance(saved,list) or any(not isinstance(r,dict) or type(r.get('tau')) is not int for r in saved):
        raise ValueError('Missing ID snapshot coverage for numeric value audit')
    periods=[r['tau'] for r in saved]
    summary_count=summary.get('intraday',{}).get('rounds')
    horizon=summary.get('expected_periods',summary.get('periods'))
    if type(summary_count) is not int or type(horizon) is not int or horizon<=0 or summary_count!=len(saved):
        raise ValueError('ID numeric archive count/horizon mismatch')
    table=pd.read_csv(root/'id_rounds_summary.csv')
    if 'tau' not in table or table.tau.tolist()!=periods or [r['window_start'] for r in records]!=periods:
        raise ValueError('ID numeric archive is missing or duplicating successful rounds')
    for r,snapshot in zip(records,saved):
        sn=snapshot.get('snapshot')
        if not isinstance(sn,dict) or not {'E','Vu','Vh'} <= set(sn) or r['periods']!=horizon-snapshot['tau']:
            raise ValueError('ID numeric archive resources/remaining-day horizon missing')
        actual=dict(sn,surplus=np.empty(r['periods']))
        row=dict(tau=snapshot['tau'],snap=actual,state_value_evidence=r)
        cached_state_evidence(row)
        if digest(strict_audit_values(row))!=digest(snapshot.get('state_values')):
            raise ValueError('Cached strict ID value evidence differs from the frozen hydro audit bundle')
    return dict(active=True,valid=True,rounds=len(records),scope='exact_ID_clock_resource_hash_and_strict_value_linkage')
