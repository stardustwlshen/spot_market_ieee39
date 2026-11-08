"""Read-only two-settlement presentation; never borrow live offers or costs."""
from __future__ import annotations
import numpy as np
import pandas as pd
from smm import spot_costs
from smm.time_grid import result_time_grid

COSTS = ['variable_cost_yuan', 'fixed_online_cost_yuan', 'startup_cost_yuan', 'shutdown_cost_yuan']

def build_unit_economics(result, *, profile=None, initial_state=None):
    """Compare DA plan and final cash on the SAME saved settlement period scope.

    profile must be the caller-verified frozen spot cost profile. initial_state is
    {phase: {unit: {'on': ..., 'ng': ..., 'np': ...}}}; never assume cold starts.
    Missing state/cost evidence yields NaN, not fabricated zero profits.
    """
    profile, initial_state = _frozen_evidence(result, profile, initial_state)
    from settlement_validation import normalized_settlement_tables
    tables = normalized_settlement_tables(result)
    cash = tables.get('settlement_detail', pd.DataFrame())
    da_only = cash.empty
    if da_only and (result.get('full') or not result.get('rt_units',pd.DataFrame()).empty or (result.get('summary') or {}).get('executed_periods',0)):
        raise ValueError('Actual execution exists but formal settlement evidence is missing')
    if da_only:
        cash = _planned_cash(result)
        if cash.empty:
            return pd.DataFrame()
    required = {'entity','period','q_da','q_rt','da_amount','rt_amount','total'}
    if not required <= set(cash):
        raise ValueError('Settlement quantity/cash evidence incomplete')
    _, dt = result_time_grid(result)
    catalogs = [result.get(k) for k in ('da_unit_awards','rt_units')]
    catalogs = [x[['unit','type']] for x in catalogs if isinstance(x,pd.DataFrame) and {'unit','type'} <= set(x)]
    if not catalogs:
        return pd.DataFrame()
    catalog = pd.concat(catalogs).drop_duplicates()
    if catalog.unit.duplicated().any():
        raise ValueError('Conflicting saved unit types')
    if profile is not None:
        if profile.get('profit_basis') != spot_costs.BASIS:
            raise ValueError('Only explicit spot-energy cost assumptions are supported')
        spot_costs.validate_profile(profile)
    expected = (result.get('summary') or {}).get('expected_periods' if da_only else 'executed_periods')
    expected_periods=set(range(int(expected))) if expected is not None else set(cash.period)
    if set(cash.period)!=expected_periods: raise ValueError('Incomplete declared period scope')
    rows = []
    for uid, typ in catalog.itertuples(index=False, name=None):
        if typ not in spot_costs.KINDS:
            continue
        names = [uid+'_放电',uid+'_充电'] if typ=='storage' else [uid+'_发电',uid+'_抽水'] if typ=='pumped' else [uid]
        subset = cash[cash.entity.isin(names)]
        if subset.empty:
            raise ValueError('Missing settlement unit '+uid)
        periods = sorted(expected_periods)
        if set(zip(subset.entity,subset.period)) != {(n,p) for n in names for p in periods}:
            raise ValueError('Incomplete settlement grid for '+uid)
        rec, reason = spot_costs.parameters(profile or {'units':{}},uid,typ)
        for phase in (('da',) if da_only else ('da','rt')):
            previous = (initial_state or {}).get(phase,{}).get(uid,{})
            states = result.get('da_unit_awards' if phase=='da' else 'rt_units', pd.DataFrame())
            states = states[states.unit.eq(uid)].set_index('period') if isinstance(states,pd.DataFrame) and {'unit','period'} <= set(states) else pd.DataFrame()
            if states.index.duplicated().any():
                raise ValueError('Duplicate saved unit state')
            for p in periods:
                group = subset[subset.period.eq(p)].set_index('entity')
                amounts = group.da_amount if phase=='da' else group.total
                gross = float(amounts.loc[names[0]])
                buying = -float(amounts.loc[names[1]]) if len(names)>1 else 0.
                energy = float(group.loc[names[0], 'q_'+phase])*dt
                row = dict(unit=uid,type=typ,phase=phase,period=p,
                    generation_mwh=energy, purchase_mwh=-float(group.loc[names[1],'q_'+phase])*dt if len(names)>1 else 0.,
                    gross_generation_cash_yuan=gross,purchase_cost_yuan=buying,
                    da_cash_yuan=float(group.da_amount.sum(min_count=len(group))),rt_deviation_cash_yuan=0. if phase=='da' else float(group.rt_amount.sum(min_count=len(group))),
                    net_cash_yuan=gross-buying,cost_available=False,cost_missing_reason=reason)
                costs = [np.nan]*4
                try:
                    if rec is None:
                        raise ValueError(reason)
                    on=starts=stops=pstarts=pstops=0.
                    if typ in spot_costs.THERMAL:
                        on=float(states.loc[p,'on'])
                        prior=float(previous['on'])
                        if on not in (0.,1.) or prior not in (0.,1.):
                            raise ValueError('Invalid commitment evidence')
                        starts=max(on-prior,0.); stops=max(prior-on,0.)
                    elif typ=='pumped':
                        modes=result.get(phase+'_pumped',pd.DataFrame())
                        if not {'unit','period','n_gen','n_pump'} <= set(modes): raise ValueError('Missing pumped mode evidence')
                        mode=modes[modes.unit.eq(uid)&modes.period.eq(p)]
                        if len(mode)!=1: raise ValueError('Missing pumped mode evidence')
                        ng=float(mode.iloc[0].n_gen); npump=float(mode.iloc[0].n_pump)
                        if any(not np.isfinite(v) or v<0 or v!=int(v) for v in (ng,npump,float(previous['ng']),float(previous['np']))): raise ValueError('Invalid pumped counts')
                        starts=max(ng-float(previous['ng']),0.); stops=max(float(previous['ng'])-ng,0.)
                        pstarts=max(npump-float(previous['np']),0.); pstops=max(float(previous['np'])-npump,0.)
                    costs = list(spot_costs.period_cost(rec,energy,on,starts,pstarts,stops,pstops,dt))
                    row['cost_available']=True
                    row['cost_missing_reason']=''
                except (KeyError, ValueError, TypeError) as exc:
                    row['cost_missing_reason']=str(exc)
                row.update(zip(COSTS,costs))
                row['operating_cost_yuan']=sum(costs)
                row['total_cost_yuan']=buying+sum(costs)
                row['profit_yuan']=gross-row['total_cost_yuan']
                rows.append(row)
                previous = {'on':states.loc[p,'on']} if p in states.index and 'on' in states else {}
                if typ=='pumped':
                    modes=result.get(phase+'_pumped',pd.DataFrame())
                    if not modes.empty:
                        mode=modes[modes.unit.eq(uid)&modes.period.eq(p)]
                        if len(mode)==1: previous={'ng':mode.iloc[0].n_gen,'np':mode.iloc[0].n_pump}
    import copy
    output=pd.DataFrame(rows)
    output.attrs['cost_profile']=copy.deepcopy(profile)
    output.attrs['cost_source']={k:profile.get(k) for k in ('input_file','sha256','profit_basis','source')} if profile else None
    return output


def summarize_unit_economics(periods):
    """Unknown period costs propagate; never display a partial total as complete."""
    if periods.empty:
        return periods.copy()
    rows=[]
    for (uid,typ,phase),g in periods.groupby(['unit','type','phase'],sort=False):
        row=dict(unit=uid,type=typ,phase=phase,period_count=len(g),cost_available=bool(g.cost_available.all()),
                 cost_missing_reason='; '.join(sorted(set(g.cost_missing_reason)-{''})))
        for col in g.select_dtypes(include='number'):
            if col!='period':
                row[col]=g[col].sum(min_count=len(g))
        rows.append(row)
    return pd.DataFrame(rows)


def _evidence_bytes(path):
    """Do not follow archive evidence out through symlinks or Windows junctions."""
    if any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)()
           for p in (path, *path.parents)):
        raise ValueError('Linked frozen cost evidence is not supported')
    return path.read_bytes()


def _evidence_json(raw):
    import json
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate frozen evidence field: ' + key)
            result[key] = value
        return result
    def invalid(value):
        raise ValueError('Non-finite frozen evidence: ' + value)
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def _archived_cost_profile(result, run, root, units, frozen):
    """Accept only this run's independently indexed spot-cost export.

    The profile sha256 identifies the ORIGINAL YAML bytes. The manifest hash
    binds the exported JSON bytes separately; neither is a signature or proof
    against coordinated modification of an entire archive. No report money is
    copied here: quantities, commitment transitions and cash are recomputed.
    """
    import hashlib
    import re
    path = run / 'fixed_cost_profile.json'
    if not path.exists() and not path.is_symlink():
        return frozen
    allowed_roots = [run / 'scenario_snapshot']
    if run.name == 'da':
        allowed_roots.append(run.parent / 'scenario_snapshot')
    if root.absolute() not in [p.absolute() for p in allowed_roots]:
        raise ValueError('Cost export and frozen units belong to different archives')
    raw = _evidence_bytes(path)
    archived = _evidence_json(raw)
    # Old writers exported both explicit/merged legacy profiles and a minimal
    # missing-cost sentinel. None is an input to spot-energy accounting.
    if (isinstance(archived, dict) and archived.get('version') != spot_costs.VERSION
            and archived.get('profit_basis') in (None, spot_costs.LEGACY)):
        summaries = [(result.get('summary') or {})]
        if (run / 'summary.json').exists():
            summaries.append(_evidence_json(_evidence_bytes(run / 'summary.json')))
        ops = [s.get('operating_profit', {}) for s in summaries]
        if any(not isinstance(op, dict) for op in ops):
            raise ValueError('Invalid legacy cost summary')
        empty_legacy = (not set(archived) - {'available', 'units', 'sha256', 'missing_reason'}
                        and archived.get('available') is False and archived.get('units') == {}
                        and archived.get('sha256') is None)
        legacy_source = (archived.get('input_file') == 'config/fixed_costs.yaml'
                         or archived.get('input_files') == ['config/fixed_costs.yaml', 'config/generated_research_costs.json']
                         or any(op.get('profit_basis') == spot_costs.LEGACY for op in ops))
        if empty_legacy or legacy_source:
            if not empty_legacy:
                from smm.profit import validate_profile as validate_legacy_profile
                validate_legacy_profile(archived)
            if (any(op.get('profit_basis') == spot_costs.BASIS for op in ops)
                    or empty_legacy and any(op.get('cost_profile_available') is True
                                           or op.get('all_unit_costs_available') is True for op in ops)
                    or frozen is not None):
                raise ValueError('Legacy export contradicts declared spot or available costs')
            return None
    spot_costs.validate_profile(archived)
    available = archived.get('available')
    if type(available) is not bool:
        raise ValueError('Archived spot costs lack explicit availability')
    source_hash = archived.get('sha256')
    if available:
        if not isinstance(source_hash, str) or not re.fullmatch('[0-9a-f]{64}', source_hash):
            raise ValueError('Invalid original cost input hash')
    elif archived['units'] or source_hash is not None:
        raise ValueError('Unavailable archived spot costs contain cost evidence')
    if archived.get('input_file') != 'config/' + spot_costs.FILENAME:
        raise ValueError('Archived spot cost input source mismatch')
    if 'exclusions' in archived and archived['exclusions'] != spot_costs.EXCLUSIONS:
        raise ValueError('Archived spot cost exclusions mismatch')
    if frozen is not None and (spot_costs.clean_profile(archived) != spot_costs.clean_profile(frozen)
                               or source_hash != frozen['sha256']):
        raise ValueError('Archived and snapshot spot costs disagree')
    manifest_path = run / 'output_manifest.json'
    # An original YAML can stand on its own. JSON-only fallback cannot: a
    # matching declared source hash alone does not authenticate its parameters.
    if frozen is not None and not manifest_path.exists():
        return frozen
    if not manifest_path.exists():
        if not available:
            # A valid explicit absence supplies no parameters to authenticate.
            # Older missing-cost runs can still display independently saved cash.
            summaries = [(result.get('summary') or {})]
            if (run / 'summary.json').exists():
                summaries.append(_evidence_json(_evidence_bytes(run / 'summary.json')))
            expected = dict(profit_basis=spot_costs.BASIS, cost_basis_version=spot_costs.VERSION,
                            cost_profile_sha256=None, cost_profile_available=False,
                            all_unit_costs_available=False)
            for summary in summaries:
                op = summary.get('operating_profit')
                if op is not None and (not isinstance(op, dict) or any(op.get(k) != v for k, v in expected.items())):
                    raise ValueError('Unavailable cost export contradicts saved summary')
            return None
        raise ValueError('Cost export requires its saved output manifest')
    manifest = _evidence_json(_evidence_bytes(manifest_path))
    if (not isinstance(manifest, dict) or type(manifest.get('schema_version')) is not int
            or manifest['schema_version'] != 1 or not isinstance(manifest.get('artifacts'), list)):
        raise ValueError('Invalid frozen cost output manifest')
    def indexed(name, content):
        matches = [x for x in manifest['artifacts'] if isinstance(x, dict) and x.get('file') == name]
        if (len(matches) != 1 or matches[0].get('present') is not True
                or type(matches[0].get('bytes')) is not int or matches[0]['bytes'] != len(content)
                or matches[0].get('sha256') != hashlib.sha256(content).hexdigest()):
            raise ValueError('Frozen cost artifact does not match manifest: ' + name)
    indexed(path.name, raw)
    summary_raw = _evidence_bytes(run / 'summary.json')
    indexed('summary.json', summary_raw)
    summary = _evidence_json(summary_raw)
    metadata = dict(profit_basis=spot_costs.BASIS, cost_basis_version=spot_costs.VERSION,
                    cost_profile_sha256=source_hash, cost_profile_available=available)
    saved = summary.get('operating_profit') if isinstance(summary, dict) else None
    supplied = (result.get('summary') or {}).get('operating_profit')
    if (not isinstance(saved, dict) or any(saved.get(k) != v for k, v in metadata.items())
            or saved.get('cost_profile_available') is not available
            or (supplied is not None and supplied != saved)):
        raise ValueError('Archived spot cost summary source mismatch')
    known = {}
    for family in ('thermal', 'renewable', 'hydro', 'storage', 'pumped'):
        for unit in units.get(family, []):
            uid, typ = unit.get('id'), unit.get('type', family)
            if not isinstance(uid, str) or not uid or uid in known or typ not in spot_costs.KINDS:
                raise ValueError('Invalid or duplicate frozen cost subject')
            known[uid] = typ
    for uid, rec in archived['units'].items():
        if uid not in known:
            raise ValueError('Unknown frozen cost subject: ' + uid)
        spot_costs.validate_record(uid, rec, known[uid])
    from types import SimpleNamespace
    enabled = spot_costs.specs(SimpleNamespace(units=units))
    for key in ('da_unit_awards', 'rt_units'):
        table = result.get(key)
        if not isinstance(table, pd.DataFrame) or table.empty:
            continue
        if not {'unit', 'type'} <= set(table):
            raise ValueError('Missing saved cost subject identifiers')
        for uid, typ in table[['unit', 'type']].drop_duplicates().itertuples(index=False, name=None):
            if (uid in enabled and enabled[uid] != typ) or (typ != 'tie' and enabled.get(uid) != typ):
                raise ValueError('Saved and frozen cost subjects disagree')
    if saved.get('all_unit_costs_available') is True and not set(enabled) <= set(archived['units']):
        raise ValueError('Complete cost summary contradicts missing unit costs')
    if saved.get('unit_count') != len(enabled):
        raise ValueError('Archived cost summary unit count mismatch')
    # Profit reports are provenance checks only, never a source of money.
    for name in ('operating_profit_by_unit', 'operating_profit_by_period'):
        table = result.get(name)
        if not isinstance(table, pd.DataFrame) or table.empty:
            continue
        required = {'unit', 'type', 'profit_basis', 'cost_basis_version', 'cost_profile_sha256'}
        if not required <= set(table):
            raise ValueError('Missing archived cost report provenance')
        for key in ('profit_basis', 'cost_basis_version', 'cost_profile_sha256'):
            matches = table[key].isna() if metadata[key] is None else table[key].eq(metadata[key])
            if not matches.all():
                raise ValueError('Archived cost report source mismatch')
        for uid, typ in table[['unit', 'type']].drop_duplicates().itertuples(index=False, name=None):
            if enabled.get(uid) != typ:
                raise ValueError('Archived cost report subject mismatch')
    return frozen if frozen is not None else archived if available else None


def _frozen_evidence(result, profile, initial):
    from pathlib import Path
    from types import SimpleNamespace
    import yaml
    root = result.get('snapshot_root')
    run = Path(result['dir']) if result.get('dir') else None
    if root is None and run is not None:
        root=run/'scenario_snapshot'
        if not root.exists() and run.name=='da': root=run.parent/'scenario_snapshot'
    if root is None: return profile, initial
    root=Path(root)
    path=root/'config/units.yaml'
    if not path.exists() and not path.is_symlink(): return profile, initial
    units=yaml.load(_evidence_bytes(path), Loader=spot_costs._UniqueLoader)
    if not isinstance(units, dict):
        raise ValueError('Invalid frozen unit configuration')
    if profile is None:
        frozen=spot_costs.load_profile(SimpleNamespace(root=root,units=units))
        frozen = frozen if frozen.get('available') else None
        profile = _archived_cost_profile(result, run, root, units, frozen) if run is not None else frozen
    if initial is None:
        da={}
        for u in units.get('thermal',[]):
            if 'online' in u.get('init',{}): da[u['id']]={'on':u['init']['online']}
        for u in units.get('pumped',[]):
            if u.get('init_mode')=='idle': da[u['id']]={'ng':0,'np':0}
            if u.get('init_mode') in ('gen','pump') and 'n_units' in u:
                da[u['id']]={'ng':u['n_units'] if u['init_mode']=='gen' else 0,'np':u['n_units'] if u['init_mode']=='pump' else 0}
        initial={'da':da,'rt':{}}
        if run is not None:
            from smm.profit_recalculation import archived_actual_initial
            specs={u['id']:u.get('type',f) for f in ('thermal','renewable','hydro','storage','pumped') for u in units.get(f,[]) if u.get('enabled',True)}
            actual=archived_actual_initial(run,specs)
            if actual:
                initial['rt']={**{u:{'on':v['online']} for u,v in actual.get('thermal',{}).items()},**{u:{'ng':v['ng'],'np':v['np']} for u,v in actual.get('pumped',{}).items()}}
    return profile,initial


def _planned_cash(result):
    awards=result.get('da_unit_awards',pd.DataFrame())
    prices=result.get('da_prices',result.get('da_nodal_prices',pd.DataFrame()))
    rows=[]
    _,dt=result_time_grid(result)
    if awards.empty:return pd.DataFrame()
    if not {'unit','type','period'} <= set(awards):raise ValueError('Missing DA identifiers')
    if awards.duplicated(['unit','period']).any():raise ValueError('Duplicate DA unit period')
    selected='lmp_clamped' if 'lmp_clamped' in prices else 'lmp'
    known='lmp_clamped' in prices or 'lmp_raw' in prices or ('settlement_price_source' in prices and not prices.empty and prices.settlement_price_source.notna().all() and prices.settlement_price_source.astype(str).str.contains('clipped').all())
    def number(v):
        try:
            x=float(v)
            return x if np.isfinite(x) else np.nan
        except (TypeError,ValueError):return np.nan
    for r in awards.itertuples():
        if r.type not in spot_costs.KINDS:continue
        price=np.nan
        if known and {'period','bus',selected} <= set(prices) and hasattr(r,'bus'):
            match=prices[prices.period.eq(r.period)&prices.bus.eq(r.bus)]
            if len(match)>1:raise ValueError('Duplicate saved node price')
            if len(match)==1:price=number(match.iloc[0][selected])
        powers={r.unit:number(getattr(r,'P_mw',None))}
        if r.type in ('storage','pumped'):
            table=result.get('da_'+r.type,pd.DataFrame())
            mode=table[table.unit.eq(r.unit)&table.period.eq(r.period)] if {'unit','period'} <= set(table) else pd.DataFrame()
            if len(mode)>1:raise ValueError('Duplicate DA component powers')
            x=mode.iloc[0] if len(mode)==1 else {}
            powers={r.unit+'_放电':number(x.get('P_dis')),r.unit+'_充电':-number(x.get('P_ch'))} if r.type=='storage' else {r.unit+'_发电':number(x.get('P_gen')),r.unit+'_抽水':-number(x.get('P_pump'))}
        for entity,power in powers.items():
            amount=power*price*dt
            rows.append(dict(entity=entity,period=r.period,q_da=power,q_rt=power,da_amount=amount,rt_amount=0.,total=amount))
    return pd.DataFrame(rows)
