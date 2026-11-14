"""Read-only historical IEEE39 input readiness. No interpolation or solving.

The historical profiles are not causal forecasts. This report preserves that
fact and computes exact next-day spans absent from the single-day CSV. A clean
hash is consistency evidence only, not an issuance or source-authenticity proof.
"""
from __future__ import annotations

import argparse
import csv
from datetime import timedelta
from hashlib import sha256
import io
import json
import math
from pathlib import Path

from .gd_october_forecast_inputs import (ForecastInputError, parse_bytes, rolling_day_requirements,
    reject_future_lineage, _time, _iso)


def assess_historical_ieee39(*, profiles_document, source_inputs_document, day_start):
    """Inspect caller-supplied original bytes without guessing new forecasts."""
    if type(profiles_document) is not bytes or not profiles_document:
        raise ForecastInputError('invalid_bytes', 'profiles', 'original CSV bytes required')
    original = parse_bytes(source_inputs_document, 'source_inputs')
    audit = original.get('report', {}).get('audit', {})
    if type(audit) is not dict:
        raise ForecastInputError('invalid_schema', 'source_inputs.report.audit', 'explicit audit object required')
    try:
        reader = csv.DictReader(io.StringIO(profiles_document.decode('utf-8-sig'), newline=''))
        fields = reader.fieldnames
        if not fields or len(fields) != len(set(fields)) or not {'period', 'time'} <= set(fields):
            raise ValueError('unique columns including period/time required')
        rows = list(reader)
        if len(rows) != 96:
            raise ValueError('exactly 96 quarter-hour rows required')
        numeric = set(fields) - {'period', 'time'}
        for index, row in enumerate(rows):
            if None in row or any(v is None for v in row.values()):
                raise ValueError('ragged CSV row')
            expected_time = f'{index//4:02d}:{(index%4)*15:02d}'
            if int(row['period']) != index+1 or row['time'] != expected_time:
                raise ValueError('ordered 1..96 periods with complete 00:00..23:45 grid required')
            if any(not math.isfinite(float(row[column])) for column in numeric):
                raise ValueError('nonfinite profile value')
    except (UnicodeError, ValueError, TypeError, OverflowError) as exc:
        raise ForecastInputError('invalid_profiles', 'effective_profiles.csv', str(exc)) from exc
    start = _time(day_start, 'day_start')
    requirements = rolling_day_requirements(start)
    errors = []
    try:
        reject_future_lineage(original, 'source_inputs')
    except ForecastInputError as exc:
        errors.append(dict(code=exc.code, path=exc.path, message=exc.message))
    profiles_hash = sha256(profiles_document).hexdigest()
    declared = audit.get('source_file_hashes', {}).get('profiles')
    hash_matches = declared == profiles_hash
    if not hash_matches:
        errors.append(dict(code='missing_or_mismatched_profiles_hash', path='source_inputs.report.audit.source_file_hashes.profiles',
                           message='Historical metadata must pin these exact profile bytes.'))
    declared_day = audit.get('delivery_start')
    same_day = declared_day is not None and _time(declared_day, 'historical delivery_start') == start
    if not same_day:
        errors.append(dict(code='historical_day_mismatch', path='source_inputs.report.audit.delivery_start',
                           message='Requested day differs from historical delivery day; no implicit retiming.'))
    end = start + timedelta(days=1)
    missing = []
    for window in requirements['windows']:
        target_end = _time(window['forecast_end'], 'forecast_end')
        if target_end > end:
            missing.append(dict(window_index=window['window_index'], window_delivery_start=window['delivery_start'],
                delivery_start=_iso(end), delivery_end=_iso(target_end),
                missing_minutes=int((target_end-end).total_seconds()/60),
                applies_to='continuous_forecast_and_effective_plan_coverage_not_supplied_by_single_day_CSV'))
    # Even a hypothetical metadata file without the explicit future-truth marker
    # is not an issued-forecast snapshot: timestamps and conditioning evidence
    # cannot be reconstructed by changing a label.
    errors.append(dict(code='missing_issued_forecast_snapshots', path='effective_profiles.csv',
        message='DA/RT column names do not establish per-revision issue/availability, eligible conditioning history, or provenance.'))
    return dict(schema_version='gd.october.ieee39_readiness.v1', status='blocked',
        profiles=dict(sha256=profiles_hash, row_count=len(rows), columns=fields,
                      declared_historical_day=declared_day, historical_day_matches_request=same_day,
                      metadata_profiles_hash_matches=hash_matches,
                      delivery_start=_iso(start) if same_day else None,
                      delivery_end=_iso(end) if same_day else None),
        source_inputs_sha256=sha256(source_inputs_document).hexdigest(),
        historical_forecast_source_kind=audit.get('forecast_source_kind'),
        historical_forecast_kind=audit.get('forecast_kind'),
        future_actual_in_core_inputs=audit.get('future_actual_in_core_inputs'),
        false_core_actual_flag_does_not_clear_future_conditioned_lineage=True,
        issues=errors, requirements=requirements, missing_next_day_spans=missing,
        boundary_diagnostics_are_conditional_on_declared_day=same_day,
        missing_union=dict(delivery_start=_iso(end), delivery_end=requirements['continuous_forecasts_required']['delivery_end'], missing_minutes=105),
        reusable_without_causal_forecast_claim=['original network/nameplate inventory with original scaling declaration',
            'historical bids and continuous profiles as historical research evidence',
            'explicitly labelled historical research policy values'],
        additional_required_inputs=['eligible issued forecasts or explicitly new causal-conditioned research forecasts',
            'explicit five-minute effective schedules with approval/availability and thermal transition evidence',
            'dated mapping/reserve/AGC/storage/boundary policies',
            'pre-cutoff observation, issued seed commands and independent first delivery boundary',
            'explicit next-day forecast/plan data and per-window terminal boundaries'],
        causal_forecast_ready=False, native_day_ready=False, actual_ieee39_converted=False,
        binary_interpolation_performed=False, historical_day_repeated=False, solver_called=False,
        source_authenticity_verified=False, official_price=None, settlement=None, cash=None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', required=True, type=Path)
    parser.add_argument('--source-inputs', required=True, type=Path)
    parser.add_argument('--day-start', required=True)
    args = parser.parse_args(argv)
    report = assess_historical_ieee39(profiles_document=args.profiles.read_bytes(),
        source_inputs_document=args.source_inputs.read_bytes(), day_start=args.day_start)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0  # report generation succeeded; report.status remains blocked


if __name__ == '__main__':
    raise SystemExit(main())
