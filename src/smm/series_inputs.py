"""Frozen series-level causal source admission, separate from forecast issuance.

Only this outer simulator layer reads the complete bundle. Model builders get a
fresh source service and an issue-bound selection, never the ActualStore or the
private plan. Bundle lineage hashes are audit metadata, not forecaster inputs.
"""
from __future__ import annotations
import copy
import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path
import numpy as np
from . import state_bridge as bridge

CAUSAL_POLICY='causal_physical_v3'
CONSUMER_CONTRACT='causal_inputs_qin_v3_1'
BUNDLE_RELATIVE='inputs/causal_v3'
BUNDLE_FILES=('manifest.json','actual.npz')


def consumer_contract(*,required=True):
    try:
        from .causal_integration import consumer_capabilities
    except ImportError as exc:
        if not required: return None
        raise ValueError('Causal execution is not ready: complete input/water consumers are not installed') from exc
    value=consumer_capabilities()
    if (not isinstance(value,dict) or value.get('contract_version')!=CONSUMER_CONTRACT
            or value.get('ready') is not True or value.get('grid_minutes')!=15
            or value.get('natural_coverage')!='remaining_day_same_issue'):
        if not required:return None
        raise ValueError('Causal execution is not ready: consumer contract is incomplete or incompatible')
    return copy.deepcopy(value)


def public_schedule_profiles(cfg,profiles):
    """Explicit UI adapter: keep only the public period grid and tie schedules."""
    names=['period']+[f"{u['id']}_sched" for u in cfg.units.get('tie',[])]
    if any(n not in profiles for n in names): raise ValueError('Public schedule is incomplete')
    result=profiles[names].copy()
    result.attrs={}
    validate_public_schedules(cfg,result)
    return result


def validate_public_schedules(cfg,profiles):
    expected={'period'}|{f"{u['id']}_sched" for u in cfg.units.get('tie',[])}
    if set(profiles.columns)!=expected or len(profiles)!=cfg.T or profiles.columns.has_duplicates:
        raise ValueError('Causal profiles may contain only the complete public period/tie schedules; no future actual or legacy forecast columns')
    if profiles.attrs:
        raise ValueError('Causal public profiles must not carry private legacy generation metadata')
    if not np.array_equal(profiles['period'].to_numpy(),np.arange(1,cfg.T+1)):
        raise ValueError('Public schedule period grid mismatch')
    for name in expected-{'period'}:
        value=profiles[name].to_numpy(dtype=float)
        if not np.isfinite(value).all(): raise ValueError('Public schedule must be finite')
    from .fixed_load import tie_mode,tie_profiles
    from .input_validation import _series
    for unit in cfg.units.get('tie',[]):
        if tie_mode(cfg)=='fixed_load':tie_profiles(cfg,profiles,unit)
        else:_series(profiles[f"{unit['id']}_sched"].to_numpy(),cfg.T,'public tie schedule',maximum=unit['pmax'])


def _file_hashes(directory):
    directory=Path(directory)
    if set(p.name for p in directory.iterdir())!=set(BUNDLE_FILES):
        raise ValueError('Frozen causal bundle must contain exactly manifest.json and actual.npz')
    if any((directory/name).is_symlink() or not (directory/name).is_file() for name in BUNDLE_FILES):
        raise ValueError('Frozen causal bundle requires ordinary immutable source files')
    return {name:hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in BUNDLE_FILES}


class SeriesSource:
    """Verified bundle reference. Instantiation never generates or issues inputs."""
    def __init__(self,directory):
        from .causal_provider import load_bundle
        self.directory=Path(directory)
        before=_file_hashes(self.directory)
        actual,self.provider=load_bundle(self.directory)
        raw=json.loads((self.directory/'manifest.json').read_text())
        after=_file_hashes(self.directory)
        if before!=after: raise ValueError('Causal bundle changed while loading')
        self.file_hashes=after
        self.bundle_content_hash=raw['bundle_content_hash']
        self.source_manifest=self.provider.source_manifest()
        self.resources=actual.plan.resources
        # ActualStore remains inside provider capabilities, never copied to a day.

    def identity(self):
        return dict(bundle_relative_path=BUNDLE_RELATIVE,bundle_content_hash=self.bundle_content_hash,
            bundle_file_hashes=self.file_hashes,source_manifest=self.source_manifest,
            consumer_contract_version=CONSUMER_CONTRACT)

    def bind(self,days):
        from .causal_types import utc_seconds,utc_iso
        if not days:raise ValueError('Causal series needs at least one day')
        expected_start=utc_iso(bridge.at(days[0].cfg))
        expected_end=utc_iso(utc_seconds(bridge.at(days[-1].cfg))+86400)
        if self.source_manifest['start_utc']!=expected_start or self.source_manifest['end_utc']!=expected_end:
            raise ValueError('Frozen process bundle must cover exactly this complete series date range')
        result=[]
        for day in days:
            cfg=day.cfg
            if day.information_policy!=CAUSAL_POLICY or (cfg.base.get('forecast') or {}).get('model')!=CAUSAL_POLICY:
                raise ValueError('All days must explicitly select the same causal model and information policy')
            if cfg.T!=96 or cfg.dt!=.25: raise ValueError('Causal series requires 96 x 15min days')
            if cfg.base.get('simulation_timezone','UTC')!=self.source_manifest['timezone']:
                raise ValueError('Causal bundle and operating timezone differ')
            expected={'load':'load',**{r['id']:r['type'] for r in cfg.units['renewable']},
                      **{h['id']:'natural_inflow' for h in cfg.units['hydro']}}
            if {r.resource_id:r.kind for r in self.resources}!=expected:
                raise ValueError('Causal bundle resource identity/kind differs from technical assets')
            caps={r['id']:float(r['cap']) for r in cfg.units['renewable']}
            if any(r.kind in ('wind','pv') and float(r.scale)!=caps[r.resource_id] for r in self.resources):
                raise ValueError('Causal renewable technical capacity changed')
            summary=self.provider.day_summary(bridge.at(cfg))
            if summary['operating_date']!=str(cfg.base['date']): raise ValueError('Causal business date mismatch')
            ref=dict(**self.identity(),day_summary=summary)
            if day.input_source_ref is not None and day.input_source_ref!=ref:
                raise ValueError('Day source reference differs from the verified frozen bundle')
            result.append(replace(day,input_source_ref=copy.deepcopy(ref)))
        return result

    def freeze(self,root):
        """Called under the series lock. Existing sources are verified, not replaced."""
        target=Path(root)/BUNDLE_RELATIVE
        if _file_hashes(self.directory)!=self.file_hashes:
            raise ValueError('Causal source changed after admission')
        if target.exists():
            if _file_hashes(target)!=self.file_hashes:
                raise ValueError('Existing series causal bundle differs; overwrite is prohibited')
        else:
            target.parent.mkdir(parents=True,exist_ok=True)
            # A partial interrupted copy remains an explicit blocker, never hidden.
            target.mkdir()
            for name in BUNDLE_FILES: shutil.copyfile(self.directory/name,target/name)
        fresh=SeriesSource(target)
        if fresh.identity()!=self.identity(): raise ValueError('Frozen causal source identity changed')
        return fresh


def validate_causal_day(day):
    validate_public_schedules(day.cfg,day.profiles)
    ref=day.input_source_ref
    if not isinstance(ref,dict) or ref.get('consumer_contract_version')!=CONSUMER_CONTRACT:
        raise ValueError('Causal day must be bound to a verified series bundle before admission')
    summary=ref.get('day_summary',{})
    if summary.get('operating_date')!=str(day.cfg.base['date']) or summary.get('periods')!=96 or summary.get('dt_h')!=.25:
        raise ValueError('Causal day source date/grid mismatch')
    if not ref.get('bundle_content_hash') or not ref.get('source_manifest',{}).get('provider_fingerprint'):
        raise ValueError('Causal source lineage is missing')
    for r in day.cfg.units['renewable']:
        row=day.bid_data['units'][r['id']]
        if row.get('avail_pmax') and row.get('availability_provenance',{}).get('kind') not in ('issued_forecast','public_bound','user_declared'):
            raise ValueError('Renewable bid availability requires explicit permitted provenance')
