"""Compose synthetic boundary scenarios by creating new scenes, never editing sources."""
from __future__ import annotations

import copy
from datetime import datetime
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from . import scenario
from .bids import default_bids, load_bids, validate_bids, raise_on_errors, save_bids
from .config import load_config
from .input_validation import read_input_csv
from .forecast import apply_profile_overrides, generate_profiles, validate_profiles

RENEWABLE_LEVELS = {'基准': 1.0, '大发': 1.2, '小发': .8}
LOAD_LEVELS = {'基准': 1.0, '高负荷': 1.1, '低负荷': .9}
WIND_EVENTS = {'无额外事件': 'none', '傍晚持续低风（17–21时25%，两侧1h过渡）': 'evening_lull'}
STAGES = ('actual', 'da', 'id', 'rt')
MANIFEST = 'config/typical_scenario.json'
BASELINE = 'data/typical_baseline_profiles.csv'


def fingerprint(root):
    """Preview guard covers all copied input/quote files, not generated outputs."""
    root = Path(root)
    paths = [p for folder in ('config', 'bids', 'data/case39') for p in (root/folder).rglob('*') if p.is_file()]
    paths += [root/name for name in ('current_quote.json', 'data/profile_overrides.csv', BASELINE) if (root/name).exists()]
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(paths))}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _generation_basis(cfg):
    base = {k: copy.deepcopy(cfg.base.get(k)) for k in ('season', 'load_scale', 'seed', 'periods', 'dt_h', 'forecast', 'tie_line_mode', 'tie_load_allocation')}
    if isinstance(base.get('forecast'), dict):
        base['forecast'].pop('error_scenarios', None)
        base['forecast'] = base['forecast'] or None
    return {'base': base,
            'units': cfg.units}


def _current_bids(cfg, bids):
    if bids is not None:
        out = copy.deepcopy(bids)
    elif (cfg.root/'current_quote.json').exists():
        record = json.loads((cfg.root/'current_quote.json').read_text(encoding='utf-8'))
        if record.get('schema') != 1 or not isinstance(record.get('draft'), dict):
            raise ValueError('当前报价存档无效，不能用默认报价替代')
        out = copy.deepcopy(record['draft'])
    elif list((cfg.root/'bids'/cfg.base['date']/cfg.rule_set).glob('bids_v*.json')):
        out = load_bids(cfg)
    else:
        out = default_bids(cfg)
    raise_on_errors(validate_bids(cfg, out, require_complete=True))
    return out


def _availability(cfg, bids):
    return {u['id']: copy.deepcopy(bids['units'][u['id']].get('avail_pmax')) for u in cfg.units['renewable']}


def _read_manifest(root):
    path = Path(root)/MANIFEST
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema') != 1 or not isinstance(data.get('baseline_availability'), dict):
        raise ValueError('典型场景基准记录不完整，不能静默改用当前曲线')
    return data


def prepare_typical(root, *, season=None, renewable_multiplier=1., load_multiplier=1.,
                    bids=None, use_current_as_baseline=False, restore=False,
                    wind_multiplier=None, pv_multiplier=None, wind_event="none", error_scenarios=None):
    """Pure preview, using all effective override columns and bid-DA precedence."""
    root = Path(root)
    before = fingerprint(root)
    cfg = load_config(root=root)
    from .resource_error_scenarios import normalized_scenarios, apply_error_scenarios
    from .profile_sources import override_sources, column_source_rows
    from .forecast_settings import generation_metadata
    selection = normalized_scenarios(cfg if error_scenarios is None else error_scenarios)
    source_info = override_sources(cfg)
    protected = source_info['protected_columns']
    current_bids = _current_bids(cfg, bids)
    wind_multiplier = renewable_multiplier if wind_multiplier is None else wind_multiplier
    pv_multiplier = renewable_multiplier if pv_multiplier is None else pv_multiplier
    if wind_event != 'none' and not np.isclose(cfg.T * cfg.dt, 24.):
        raise ValueError('傍晚低风事件只适用于完整24小时边界')
    if wind_event not in WIND_EVENTS.values():
        raise ValueError('风电时序事件须为none或evening_lull')
    for name, value in [('新能源倍率', renewable_multiplier), ('负荷倍率', load_multiplier),
                        ('风电倍率', wind_multiplier), ('光伏倍率', pv_multiplier)]:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or not .1 <= value <= 2:
            raise ValueError(f'{name}须为0.1至2的有限数值')
    prior = _read_manifest(root)
    inherited = prior is not None and not use_current_as_baseline
    if restore and not inherited:
        raise ValueError('只有已有典型场景可恢复其保存基准')
    if inherited:
        if not restore and (_generation_basis(cfg) != prior['applied_generation_basis'] or
                before.get('data/profile_overrides.csv') != prior['applied_override_sha256'] or
                _availability(cfg, current_bids) != prior['applied_availability']):
            raise ValueError('当前曲线、技术边界或申报可用量已自定义修改。请明确选择“当前输入作为新基准”；旧基准不会自动覆盖这些修改')
        path = root/BASELINE
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != prior['baseline_profiles_sha256']:
            raise ValueError('原基准曲线缺失或变化，不能恢复或重组')
        baseline = validate_profiles(cfg, read_input_csv(path))
        baseline_season = prior['baseline_season']
        baseline_load_scale = prior['baseline_load_scale']
        baseline_availability = copy.deepcopy(prior['baseline_availability'])
        baseline_name = prior['baseline_name']
    else:
        # Store configured forecasts, never the last selected error recipe.
        configured_cfg = copy.deepcopy(cfg)
        if isinstance(configured_cfg.base.get('forecast'), dict):
            configured_cfg.base['forecast'].pop('error_scenarios', None)
        generated = generate_profiles(configured_cfg)
        override_path = root/'data/profile_overrides.csv'
        if override_path.exists():
            overrides = read_input_csv(override_path)
            for column in ('period', 'time'):
                if column not in overrides:
                    continue
                actual = (pd.to_numeric(overrides[column], errors='coerce').tolist() if column == 'period'
                          else overrides[column].astype(str).str.strip().tolist())
                expected = generated[column].tolist()
                if len(overrides) != len(generated) or actual != expected:
                    raise ValueError(f'上传曲线的{column}顺序与当前时序不一致，不能按行静默错配')
        baseline = apply_profile_overrides(configured_cfg, generated)
        baseline_season, baseline_load_scale = cfg.base['season'], cfg.base['load_scale']
        baseline_availability = _availability(cfg, current_bids)
        baseline_name = root.name
        # The declared available power is the actual DA model input, not a
        # hidden override of a differently labeled scenario curve.
        for uid, values in baseline_availability.items():
            if values is not None:
                baseline[f'{uid}_da'] = np.asarray(values, float)
        validate_profiles(cfg, baseline)
    if restore:
        season, renewable_multiplier, load_multiplier = baseline_season, 1., 1.
        wind_multiplier, pv_multiplier, wind_event = 1., 1., "none"
    season = season or baseline_season
    if season not in ('wet', 'dry'):
        raise ValueError('水文类型须为丰水期wet或枯水期dry')
    target = copy.deepcopy(cfg)
    target.base.update(season=season, load_scale=float(baseline_load_scale)*load_multiplier)
    if error_scenarios is not None or 'error_scenarios' in (cfg.base.get('forecast') or {}):
        target.base['forecast'] = {**(target.base.get('forecast') or {}), 'error_scenarios': selection}
    profiles = baseline.copy()
    summary, clipped = [], []
    fixed_loads = cfg.units.get('tie', []) if cfg.base.get('tie_line_mode', 'supply') == 'fixed_load' else []
    from .fixed_load import tie_profiles
    for tie in fixed_loads:
        for column, values in tie_profiles(cfg, baseline, tie).items():
            baseline[column] = values
    profiles = baseline.copy()
    load_names = ['load'] + [tie['id'] for tie in fixed_loads]
    hours = (np.arange(cfg.T) + .5) * cfg.dt
    wind_mask = np.interp(hours, [0., 16., 17., 21., 22., 24.], [1., 1., .25, .25, 1., 1.]) if wind_event == 'evening_lull' else np.ones(cfg.T)
    wind_ids = {u['id'] for u in cfg.units['renewable'] if u.get('type') == 'wind'}
    for name, factor, cap in [(name, load_multiplier, None) for name in load_names] + [(u['id'], wind_multiplier if u.get('type') == 'wind' else pv_multiplier, float(u['cap'])) for u in cfg.units['renewable']]:
        for stage in STAGES:
            col = f'{name}_{stage}'
            raw = baseline[col].to_numpy(float)*factor * (wind_mask if name in wind_ids else 1.)
            values = np.minimum(raw, cap) if cap is not None else raw
            profiles[col] = values
            clipped.append({'对象': name, '阶段': stage, '触及装机上限点数': int(np.count_nonzero(raw > cap+1e-9)) if cap is not None else 0})
        summary.append({'对象': '基础外生负荷' if name == 'load' else name, '倍率': factor,
                        '基准实际峰值（MW）': float(baseline[f'{name}_actual'].max()),
                        '组合实际峰值（MW）': float(profiles[f'{name}_actual'].max()),
                        '组合日前峰值（MW）': float(profiles[f'{name}_da'].max())})
    # Quote availability changes only with the explicit weather transformation,
    # never as a side effect of an error selection.
    configured_profiles = profiles.copy(deep=True)
    quote_columns = [f'{uid}_da' for uid, values in baseline_availability.items() if values is not None]
    profiles = apply_error_scenarios(target, profiles, protected_columns=set(protected) | set(quote_columns))
    validate_profiles(target, profiles)
    for row in summary:
        name = 'load' if row['对象'] == '基础外生负荷' else row['对象']
        row['组合日前峰值（MW）'] = float(profiles[f'{name}_da'].max())
    summary.append({'对象': '全网区内外生负荷合计', '倍率': load_multiplier,
                    '基准实际峰值（MW）': float(baseline[[f'{n}_actual' for n in load_names]].sum(axis=1).max()),
                    '组合实际峰值（MW）': float(profiles[[f'{n}_actual' for n in load_names]].sum(axis=1).max()),
                    '组合日前峰值（MW）': float(profiles[[f'{n}_da' for n in load_names]].sum(axis=1).max())})
    output_bids = copy.deepcopy(current_bids)
    changed = []
    for uid, values in baseline_availability.items():
        new = configured_profiles[f'{uid}_da'].tolist() if values is not None else None
        if output_bids['units'][uid].get('avail_pmax') != new:
            changed.append(uid)
            output_bids['units'][uid]['avail_pmax'] = new
    raise_on_errors(validate_bids(target, output_bids, require_complete=True))
    # Read the very same seasonal model data used by formal clearing, without solving.
    from .case import build_da_case
    from .network import build_network
    from .scuc import prepare
    from .reserve_policy import for_new_run
    model_case = build_da_case(for_new_run(target), build_network(target), profiles, output_bids)
    model_data = prepare(model_case)
    from .scenario_assessment import assess_boundaries
    assessment = assess_boundaries(model_case, model_data=model_data)
    assessment['designed_stress_event'] = wind_event
    assessment['forecast_model'] = target.base.get('forecast', {}).get('model', 'legacy_v1')
    assessment['error_scenarios'] = selection
    if wind_event != 'none' and assessment['tier'] != 'pressure_required':
        assessment.update(tier='designed_stress', label='压力敏感性：傍晚持续低风，尚需出清验证')
    assessment['caveats'].append('倍率与低风事件先作用于基准曲线，再从最终实际值生成所选误差场景；上传预测与报价可用量优先。这是未校准离线情景，不是在线因果预测。')
    hydro = [{'电站': uid, '季节': '丰水期' if season == 'wet' else '枯水期',
              **{k: values.get(k) for k in ('qin', 'v0', 'vmin', 'vmax', 'v_end', 'qeco', 'e_day')}}
             for uid, values in model_data.hydro.items()]
    if fingerprint(root) != before:
        raise RuntimeError('输入在预览期间发生变化，请重新预览')
    warnings = list(source_info['warnings'])
    if quote_columns:
        warnings.append('报价 avail_pmax 优先于日前预测：' + '、'.join(quote_columns) + '；仅更换误差场景不会改变这些申报可用量。')
    previous_profiles = apply_profile_overrides(cfg, generate_profiles(cfg))
    for uid, values in _availability(cfg, current_bids).items():
        if values is not None:
            previous_profiles[f'{uid}_da'] = values
    effects = []
    for name in ['load'] + [u['id'] for u in cfg.units['renewable']]:
        for stage in ('da', 'id', 'rt'):
            column = f'{name}_{stage}'
            changed_count = int(np.count_nonzero(~np.isclose(profiles[column].to_numpy(), previous_profiles[column].to_numpy(), rtol=1e-12, atol=1e-9)))
            effects.append({'对象': name, '阶段': {'da': '日前', 'id': '日内', 'rt': '实时'}[stage],
                            '列': column, '变化时段数': changed_count,
                            '预测减实际均值（MW）': float((profiles[column] - profiles[f'{name}_actual']).mean()),
                            '受保护': column in set(protected) | set(quote_columns)})
    metadata = generation_metadata(target, override_columns=profiles.columns,
                                   protected_columns=protected, quote_columns=quote_columns)
    metadata.update(error_effect_summary=effects, profile_source_warnings=warnings)
    column_sources = column_source_rows(target, profiles.columns, protected, quote_columns,
                                        limited=source_info['status'] == 'unverified_preserve_all')
    return dict(root=str(root.resolve()), fingerprint=before, bid_fingerprint=_digest(current_bids),
                request=dict(season=None if restore else season, renewable_multiplier=renewable_multiplier,
                             load_multiplier=load_multiplier, use_current_as_baseline=use_current_as_baseline, restore=restore,
                             wind_multiplier=wind_multiplier, pv_multiplier=pv_multiplier, wind_event=wind_event,
                             error_scenarios=error_scenarios),
                season=season, renewable_multiplier=renewable_multiplier, load_multiplier=load_multiplier,
                wind_multiplier=wind_multiplier, pv_multiplier=pv_multiplier, wind_event=wind_event,
                load_scale=target.base['load_scale'], baseline_name=baseline_name,
                baseline_season=baseline_season, baseline_load_scale=baseline_load_scale,
                baseline_availability=baseline_availability, baseline_profiles=baseline,
                profiles=profiles, bids=output_bids, original_bids=current_bids,
                availability_changes=changed, summary=summary, clipping=clipped, hydro=hydro, assessment=assessment,
                inherited=inherited, error_scenarios=selection, error_effect_summary=effects,
                column_sources=column_sources, source_warnings=warnings, generation_metadata=metadata,
                error_scenario_warnings=warnings, error_scenario_summary=effects,
                protected_columns=protected, quote_columns=quote_columns,
                configured_profiles=configured_profiles,
                persist_error_scenarios='error_scenarios' in (target.base.get('forecast') or {}))


def create_typical(name, prepared, *, bids=None, confirm_availability=False):
    """Recheck preview, then publish one exclusively claimed new scenario."""
    root = Path(prepared['root'])
    name = scenario._check_name(name)
    dst = scenario.scenario_path(name)
    if dst.exists():
        raise FileExistsError(f'场景 {name} 已存在，请换新名称；不会覆盖')
    if fingerprint(root) != prepared['fingerprint']:
        raise RuntimeError('源场景或上传曲线已变化，请重新预览')
    cfg = load_config(root=root)
    current = _current_bids(cfg, bids)
    if _digest(current) != prepared['bid_fingerprint']:
        raise RuntimeError('当前报价已变化，请重新预览')
    checked = prepare_typical(root, bids=current, **prepared['request'])
    if checked['availability_changes'] and not confirm_availability:
        raise ValueError('需明确同意仅在新场景同步新能源申报可用出力；报价价格与分段量不变')
    scenario.SCEN_DIR.mkdir(parents=True, exist_ok=True)
    # Prepare completely off-catalog; final exclusive directory claim prevents
    # collisions, and failed preparation cannot leave a selectable half scene.
    with tempfile.TemporaryDirectory(prefix='.typical-', dir=scenario.SCEN_DIR) as temporary:
        work = Path(temporary)/'scene'
        scenario._copy_tree(root, work)
        y = scenario._yaml()
        base_path = work/'config/base.yaml'
        base = y.load(base_path.read_text(encoding='utf-8'))
        base.update(season=checked['season'], load_scale=checked['load_scale'])
        if checked['persist_error_scenarios']:
            base['forecast'] = {**(base.get('forecast') or {}), 'error_scenarios': checked['error_scenarios']}
        with base_path.open('w', encoding='utf-8') as stream:
            y.dump(base, stream)
        objects = ['load'] + [u['id'] for u in cfg.units['renewable']]
        if cfg.base.get('tie_line_mode', 'supply') == 'fixed_load':
            objects += [u['id'] for u in cfg.units.get('tie', [])]
        override_columns = ['period', 'time'] + [f'{obj}_{stage}' for obj in objects for stage in STAGES]
        checked['profiles'][override_columns].to_csv(work/'data/profile_overrides.csv', index=False)
        checked['baseline_profiles'].to_csv(work/BASELINE, index=False)
        # Preserve the original uploaded file and complete quote workspace as evidence.
        for source, destination in [('data/profile_overrides.csv', 'data/typical_source_overrides.csv'),
                                    ('current_quote.json', 'data/typical_source_current_quote.json')]:
            if (root/source).exists():
                shutil.copy2(root/source, work/destination)
        target_cfg = load_config(root=work)
        path = save_bids(target_cfg, checked['bids'], new_version=True)
        saved = json.loads(path.read_text(encoding='utf-8'))
        record = dict(schema=1, draft=saved, baseline=copy.deepcopy(saved), initial_latest=copy.deepcopy(saved),
                      basis_label='典型场景建立时复制的当前报价', origin='价格与分段量来自源场景当前报价；可用预测按已确认的场景组合处理。',
                      history={'源场景当前报价': checked['original_bids']}, manual=[], saved_at=None)
        (work/'current_quote.json').write_text(json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        manifest = {key: checked[key] for key in ('season', 'renewable_multiplier', 'load_multiplier', 'baseline_name',
                    'baseline_season', 'baseline_load_scale', 'baseline_availability', 'summary', 'clipping', 'hydro', 'assessment',
                    'wind_multiplier', 'pv_multiplier', 'wind_event')}
        manifest.update(schema=1, method='synthetic_research_boundary_scenario',
                        source_scene=root.name, source_fingerprint=prepared['fingerprint'],
                        baseline_profiles_sha256=hashlib.sha256((work/BASELINE).read_bytes()).hexdigest(),
                        applied_override_sha256=hashlib.sha256((work/'data/profile_overrides.csv').read_bytes()).hexdigest(),
                        applied_generation_basis=_generation_basis(target_cfg),
                        applied_availability=_availability(target_cfg, saved),
                        converted_load_policy='scale_once_with_total_load_factor_then_allocate_by_frozen_base_load_shares',
                        warning='合成研究情景；负荷是外生需求，不参与市场报价或需求响应。装机、网络、市场规则与原报价价格不变。')
        protected = sorted((set(checked['protected_columns']) | set(checked['quote_columns'])) & set(override_columns))
        generated = sorted(set(override_columns) - set(protected) - {'period', 'time'})
        manifest.update(error_scenarios=checked['error_scenarios'], generation_metadata=checked['generation_metadata'],
                        profile_sources=dict(version=1, protected_columns=protected, generated_columns=generated,
                                             quote_columns=checked['quote_columns'],
                                             override_sha256=manifest['applied_override_sha256'],
                                             configured_forecasts={col: checked['configured_profiles'][col].tolist()
                                                                   for col in generated if col.endswith(('_da', '_id', '_rt'))}))
        (work/MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        (work/'scenario.json').write_text(json.dumps({'name': name, 'source': root.name, 'note': manifest['warning'], 'created': datetime.now().isoformat(timespec='seconds'),
                                                      'typical_scenario': {key: checked[key] for key in ('season', 'renewable_multiplier', 'load_multiplier', 'wind_multiplier', 'pv_multiplier', 'wind_event')}}, ensure_ascii=False, indent=2), encoding='utf-8')
        # Real pipeline preparation verifies persisted profile + saved bid precedence.
        from .pipeline import prepare
        case = prepare(target_cfg, verbose=False)
        for unit in target_cfg.units['renewable']:
            np.testing.assert_allclose(case.ren_fc[unit['id']], checked['profiles'][f"{unit['id']}_da"])
        if fingerprint(root) != prepared['fingerprint']:
            raise RuntimeError('源场景在创建期间变化，未发布，请重新预览')
        dst.mkdir(exist_ok=False)
        try:
            # Base YAML is the scene catalog marker: publish it last.
            for item in work.iterdir():
                if item.name != 'config':
                    shutil.copytree(item, dst/item.name) if item.is_dir() else shutil.copy2(item, dst/item.name)
            (dst/'config').mkdir()
            for item in (work/'config').iterdir():
                if item.name != 'base.yaml':
                    shutil.copy2(item, dst/'config'/item.name)
            shutil.copy2(work/'config/base.yaml', dst/'config/base.yaml')
        except Exception:
            shutil.rmtree(dst)
            raise
    return dst
