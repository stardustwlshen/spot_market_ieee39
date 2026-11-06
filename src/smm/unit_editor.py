"""Explicit unit edits: stage/validate everything, then commit only selected inputs."""
from __future__ import annotations
import copy
import hashlib
import json
import re
import shutil
from pathlib import Path
import tempfile
from datetime import datetime, timezone

import yaml

from .config import Config, filter_enabled, load_config
from .process_control import file_lock
from .unit_edit_transaction import scene_input_lock, commit_inputs

TYPES = {'coal': ('thermal', '燃煤机组'), 'gas_ccgt': ('thermal', '燃气联合循环'),
         'gas_gt': ('thermal', '燃气轮机'), 'hydro': ('hydro', '水库式水电'),
         'run_of_river': ('hydro', '径流式水电'), 'storage': ('storage', '电池储能'),
         'pumped': ('pumped', '抽水蓄能'), 'wind': ('renewable', '风电'), 'pv': ('renewable', '光伏')}


def unit_template(kind, uid, bus, capacity=100., *, n_units=1, energy=200.):
    """Clearly synthetic defaults. Capacity is MW; energy is MWh, never interchanged."""
    if kind not in TYPES:
        raise ValueError('请选择受支持的机组类型')
    common = dict(id=uid, bus=bus, enabled=True)
    if kind in ('coal', 'gas_ccgt', 'gas_gt'):
        ratio = {'coal': .4, 'gas_ccgt': .4, 'gas_gt': .25}[kind]
        minimum = capacity * ratio
        hours = {'coal': 8, 'gas_ccgt': 4, 'gas_gt': 1}[kind]
        from .research_defaults import thermal_research_parameters
        costs = thermal_research_parameters(kind, capacity)
        hot = costs['fixed_startup_cost_yuan_per_start']
        # Type-specific historical research temperature ratios, not market rules.
        cold, warm, very_hot = {'coal': (2.4, 1.6, .6),
                               'gas_ccgt': (2., 22/15, 2/3),
                               'gas_gt': (1.5, 1.25, .75)}[kind]
        return dict(common, type=kind, pmax=capacity, pmin=minimum,
                    ramp=capacity * (.02 if kind == 'coal' else .10), ut=hours, dt=hours,
                    noload=costs['fixed_online_cost_yuan_per_h'],
                    startup=dict(cold=hot*cold, warm=hot*warm, hot=hot, very_hot=hot*very_hot),
                    init=dict(online=False, p0=0., hours=24.))
    if kind in ('wind', 'pv'):
        return dict(common, type=kind, cap=capacity, profile='wind_a' if kind == 'wind' else 'pv')
    if kind == 'storage':
        return dict(common, pmax=capacity, ecap=energy, eta_c=.95, eta_d=.95,
                    soc_min=.1, soc_max=.9, soc0=.5, soc_end_bid=.5)
    if kind in ('hydro', 'run_of_river'):
        unit = capacity / n_units
        reservoir = kind == 'hydro'
        def season(factor):
            return dict(qin=capacity*1.2*factor, v0=1000., vmin=0. if reservoir else 1000.,
                        vmax=2000. if reservoir else 1000., v_end=1000., qeco=0., e_day=capacity*24.)
        result = dict(common, regulation='reservoir' if reservoir else 'run_of_river',
                    n_units=n_units, unit_pmax=unit, zones=[[unit*.2, unit]], ramp=unit,
                    ut=1., dt=1., water_rate=1.2, init_units_on=0, init_p_unit=0.,
                    seasons=dict(wet=season(.6), dry=season(.35)), e_day_min=None, qout_max=None,
                    upstream=None, lag_periods=0,
                    **({} if reservoir else dict(pondage=0.)))
        from .hydro_commitment import research_initial
        result['commitment'] = research_initial(result)
        return result
    # Six hours usable generation; conversion factors imply 76.2% round-trip efficiency.
    usable = capacity * 6 / 9.6
    low, high = usable*.1, usable*1.1
    middle = (low+high)/2
    return dict(common, n_units=n_units, unit_gen_max=capacity/n_units,
                unit_gen_min=capacity/n_units*.5, unit_pump=capacity/n_units,
                variable_speed=False, unit_pump_min=capacity/n_units*.7,
                e_gen=9.6, e_pump=12.6, v_total=low+high, vup_min=low, vup_max=high,
                vdn_min=low, vdn_max=high, vup0=middle, vup_end_bid=middle,
                switch_gap=1, max_gen_starts=4, max_pump_starts=2,
                start_cost_gen=capacity/n_units/300*2000,
                start_cost_pump=capacity/n_units/300*4000, init_mode='idle')


def _json(data):
    return json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2).encode()


def _paths(root):
    paths = []
    for folder in ('config', 'data', 'bids'):
        directory = root / folder
        if directory.is_symlink():
            raise ValueError('机组编辑不写入链接目录，请选择普通场景副本。')
        if directory.exists():
            for path in sorted(directory.rglob('*')):
                if path.is_symlink():
                    raise ValueError('机组编辑不处理符号链接输入。')
                if path.is_file():
                    paths.append(path)
    if (root / 'current_quote.json').is_symlink():
        raise ValueError('当前报价不能是符号链接。')
    if (root / 'current_quote.json').exists():
        paths.append(root / 'current_quote.json')
    return paths


def scene_fingerprint(root):
    root = Path(root)
    with scene_input_lock(root):
        return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in _paths(root)}


def catalog(root):
    data = yaml.safe_load((Path(root) / 'config/units.yaml').read_text(encoding='utf-8'))
    return [(group, copy.deepcopy(u)) for group, entries in data.items() if isinstance(entries, list)
            for u in entries if isinstance(u, dict) and group != 'tie']


def _validate_record(raw, family, unit, original_id):
    if family not in {g for g, _ in TYPES.values()}:
        raise ValueError('只能编辑已支持的发电、储能机组类型')
    if not isinstance(unit, dict):
        raise ValueError('机组参数须为对象')
    uid = unit.get('id')
    if not isinstance(uid, str) or not re.fullmatch(r'[\w\u4e00-\u9fff-]{1,60}', uid):
        raise ValueError('机组编号须为1–60位中文、字母、数字、下划线或短横线，不含空格')
    ids = [u['id'] for entries in raw.values() if isinstance(entries, list) for u in entries]
    if len(ids) != len(set(ids)):
        raise ValueError('原场景机组编号重复（含停用机组），请先修复，未保存')
    if original_id is None and uid in ids:
        raise ValueError(f'机组编号 {uid} 已存在（含停用机组），请改用唯一编号')
    if original_id is not None:
        match = [u for u in raw.get(family, []) if u['id'] == original_id]
        if len(match) != 1 or uid != original_id:
            raise ValueError('编辑已有机组不能改编号或资源组；请使用复制新增')
        if match[0].get('type', family) != unit.get('type', family):
            raise ValueError('编辑已有机组不能改类型；请使用新增机组')
    if not isinstance(unit.get('enabled', True), bool):
        raise ValueError('是否启用须为是或否')
    typ = unit.get('type', family)
    allowed = {'thermal': {'coal', 'gas_ccgt', 'gas_gt'}, 'renewable': {'wind', 'pv'},
               'hydro': {'hydro'}, 'storage': {'storage'}, 'pumped': {'pumped'}}
    if typ not in allowed[family]:
        raise ValueError('类型与资源组不匹配')
    if family == 'renewable':
        profiles = {'wind': {'wind_a', 'wind_b'}, 'pv': {'pv'}}
        if unit.get('profile') not in profiles[typ]:
            raise ValueError('风电预测曲线只能选wind_a/wind_b，光伏只能选pv')
    if family == 'thermal' and not {'cold', 'warm', 'hot', 'very_hot'} <= set(unit.get('startup', {})):
        raise ValueError('启动参数须完整含冷、温、热、极热态')
    if family == 'hydro':
        from .input_validation import number
        for key in ('ut', 'dt'):
            if number(unit.get(key), f'{uid}.{key}') < 0:
                raise ValueError(f'{uid}.{key}须为非负小时数')
        if not unit.get('init_units_on') and unit.get('init_p_unit') != 0:
            raise ValueError('水电初始在线台数为0时，初始单机功率应为0 MW')
    if family == 'pumped':
        # Current model infers all online units from init_mode, not arbitrary counts.
        if any(key in unit for key in ('init_gen', 'init_pump')):
            raise ValueError('抽蓄初态请用初始工况init_mode；模型不支持独立init_gen/init_pump台数')


def _cost_update(stage, cfg, uid, *, refresh=True):
    """Refresh only selected demonstrably synthetic records, never any other unit."""
    from .research_cost_setup import prepare_generated_cost_setup, read_generated_costs, FILENAME, GROUPS, _bytes
    old = read_generated_costs(stage)
    plan = prepare_generated_cost_setup(cfg, replace_known_research=refresh)
    data = copy.deepcopy(plan['data'])
    for mapping in ('expected_fixed', 'evidence'):
        data[mapping] = {k: v for k, v in (old or {}).get(mapping, {}).items() if k != uid}
        if uid in plan['data'][mapping]:
            data[mapping][uid] = copy.deepcopy(plan['data'][mapping][uid])
    data['profile']['units'] = {k:v for k,v in (old or {}).get('profile', {}).get('units', {}).items() if k != uid}
    if uid in plan['data']['profile']['units']:
        data['profile']['units'][uid] = copy.deepcopy(plan['data']['profile']['units'][uid])
    for group in GROUPS:
        for container in ('worksheet', 'expected_worksheet', 'worksheet_evidence'):
            data[container][group] = {k:v for k,v in (old or {}).get(container, {}).get(group, {}).items() if k != uid}
            if uid in plan['data'][container][group]:
                data[container][group][uid] = copy.deepcopy(plan['data'][container][group][uid])
    # Rebase provenance without reviving inactive overlays after a user edits
    # a real/custom archive. Keep only previously applicable other-unit records.
    if old:
        from .research_cost_setup import _allowed, _origin, GROUPS
        fixed_path, sheet_path = stage / 'config/fixed_costs.yaml', stage / 'config/bid_cost_basis.yaml'
        fixed = yaml.safe_load(fixed_path.read_bytes()) if fixed_path.exists() else None
        sheet = yaml.safe_load(sheet_path.read_bytes()) if sheet_path.exists() else None
        for other in list(data['profile']['units']):
            if other == uid:
                continue
            current = (fixed or {}).get('units', {}).get(other)
            if not (_allowed(current, old['expected_fixed'][other]) and
                    (current is None or _origin(fixed, ('units',)) == old['expected_fixed_origin'])):
                for mapping in (data['profile']['units'], data['expected_fixed'], data['evidence']):
                    mapping.pop(other, None)
        for group in GROUPS:
            for other in list(data['worksheet'][group]):
                if other == uid:
                    continue
                current = (sheet or {}).get(group, {}).get(other)
                if not (_allowed(current, old['expected_worksheet'][group][other]) and
                        (current is None or _origin(sheet, GROUPS) == old['expected_worksheet_origin'])):
                    for container in ('worksheet', 'expected_worksheet', 'worksheet_evidence'):
                        data[container][group].pop(other, None)
    data.pop('fingerprint', None)
    data['fingerprint'] = hashlib.sha256(_bytes(data)).hexdigest()
    (stage / 'config' / FILENAME).write_bytes(_bytes(data))
    return uid in data['profile']['units']


def _current_record(root, cfg):
    path = root / 'current_quote.json'
    if path.exists():
        record = json.loads(path.read_bytes())
        if (not isinstance(record, dict) or record.get('schema') != 1
                or not isinstance(record.get('draft', {}).get('units'), dict)
                or not isinstance(record.get('baseline', {}).get('units'), dict)):
            raise ValueError('当前报价草稿格式无效，请先修复；机组未保存')
        return record
    from .bids import load_bids, default_bids, bid_dir
    archives = list(bid_dir(cfg).glob('bids_v*.json'))
    draft = load_bids(cfg) if archives else default_bids(cfg)
    return dict(schema=1, draft=copy.deepcopy(draft), baseline=copy.deepcopy(draft),
                initial_latest=copy.deepcopy(draft), basis_label='机组编辑前当前报价',
                history={}, manual=[], origin='')


def prepare_unit_edit(root, family, unit, *, original_id=None, reset_bid=False, expected=None):
    """Read-only preview. No user files are changed; return a detached immutable plan."""
    root = Path(root)
    with file_lock(root / '.cost_archive.lock', blocking=True), file_lock(root / 'current_quote.lock', blocking=True), scene_input_lock(root):
        before = scene_fingerprint(root)
        if expected is not None and before != expected:
            raise RuntimeError('场景已被另一页面修改，请取消后重新打开编辑；未覆盖任何输入。')
        with tempfile.TemporaryDirectory(prefix='unit-edit-') as tmp:
            stage = Path(tmp)
            for p in _paths(root):
                target = stage / p.relative_to(root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, target)
            old_cfg = load_config(root=stage)
            record = _current_record(stage, old_cfg)
            raw = yaml.safe_load((stage / 'config/units.yaml').read_bytes())
            _validate_record(raw, family, unit, original_id)
            old_unit = next((u for u in raw.get(family, []) if u['id'] == original_id), None)
            cost_fields = ('type', 'pmax', 'cap', 'n_units', 'unit_pmax', 'ecap', 'eta_c', 'eta_d',
                           'unit_gen_max', 'unit_pump', 'e_gen', 'e_pump')
            refresh_cost = old_unit is None or any(old_unit.get(k) != unit.get(k) for k in cost_fields)
            if original_id is None:
                raw.setdefault(family, []).append(copy.deepcopy(unit))
            else:
                raw[family] = [copy.deepcopy(unit) if u['id'] == original_id else u for u in raw[family]]
            # Validate even a disabled edited resource, but keep all unrelated disabled
            # legacy entries out of the executable model as usual.
            check_raw = copy.deepcopy(raw)
            for u in check_raw[family]:
                if u['id'] == unit['id']:
                    u['enabled'] = True
            from .input_validation import validate_physical_inputs, validate_network_inputs, validate_case_inputs
            check_cfg = Config(old_cfg.base, filter_enabled(check_raw), old_cfg.mode, stage)
            validate_physical_inputs(check_cfg)
            from .network import build_network
            validate_network_inputs(check_cfg, build_network(check_cfg))
            (stage / 'config/units.yaml').write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding='utf-8')
            cfg = load_config(root=stage)
            uid = unit['id']
            generated = _cost_update(stage, cfg, uid, refresh=refresh_cost) if unit.get('enabled', True) else False
            # New resource creation explicitly initializes its independent spot cost;
            # editing existing capacity never rewrites a saved cost assumption.
            if original_id is None and unit.get('enabled',True):
                from . import spot_costs
                spot=spot_costs.clean_profile(spot_costs.load_profile(cfg))
                if uid not in spot['units']:
                    defaults,_=spot_costs.research_defaults(cfg)
                    if uid in defaults['units']:
                        spot['units'][uid]=defaults['units'][uid]
                        spot_costs.validate_profile(spot)
                        (stage/'config'/spot_costs.FILENAME).write_text(yaml.safe_dump(spot,allow_unicode=True,sort_keys=False))
            from .bids import legacy_default_bids, validate_bids, raise_on_errors
            previous = copy.deepcopy(record['draft'])
            replacing = reset_bid or uid not in record['draft']['units']
            if not unit.get('enabled', True):
                for key in ('draft', 'baseline', 'initial_latest'):
                    record.get(key, {}).get('units', {}).pop(uid, None)
            elif replacing:
                new_bid = legacy_default_bids(cfg)['units'][uid]
                record['draft']['units'][uid] = copy.deepcopy(new_bid)
                # Cost-driven synthetic offers only for new/reset unit, never other bids.
                from .bid_simulation import research_cost_bid_preview
                if new_bid.get('mode') == 'price_quantity':
                    record['draft'] = research_cost_bid_preview(cfg, selected_unit_ids=[uid], base_bids=record['draft'])
                record['baseline']['units'][uid] = copy.deepcopy(record['draft']['units'][uid])
                record.setdefault('manual', [])[:] = [x for x in record.get('manual', []) if x != uid]
            issues = validate_bids(cfg, record['draft'], require_complete=True)
            failures = [f'{i.unit_id}：{i.msg}' for i in issues if i.level == 'error']
            if failures:
                raise ValueError('当前报价与候选参数不兼容：' + '；'.join(failures) +
                    '。可取消修改，或明确勾选“仅为此机组重新生成模拟报价”；其他机组报价需在报价页先修复。')
            from .forecast import generate_profiles, apply_profile_overrides
            from .case import build_da_case
            profiles = apply_profile_overrides(cfg, generate_profiles(cfg, cfg.base['seed']))
            case = build_da_case(cfg, build_network(cfg), profiles, record['draft'])
            validate_case_inputs(case)
            if record['draft'] != previous:
                stamp = hashlib.sha256(_json(previous)).hexdigest()[:16]
                record.setdefault('history', {})['机组修改前报价:' + stamp] = previous
            record['saved_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
            record['origin'] = f'已保存机组 {uid} 技术参数；仅本机组新增/明确重建报价，其他报价保留。'
            replacements = {'config/units.yaml': (stage / 'config/units.yaml').read_bytes(),
                            'current_quote.json': _json(record)}
            cost_path = stage / 'config/generated_research_costs.json'
            if cost_path.exists():
                replacements['config/generated_research_costs.json'] = cost_path.read_bytes()
            spot_path=stage/'config/spot_market_costs.yaml'
            if spot_path.exists():replacements['config/spot_market_costs.yaml']=spot_path.read_bytes()
            if scene_fingerprint(root) != before:
                raise RuntimeError('预览期间场景已变化，请重新核对，未保存。')
            return dict(root=str(root), before=before, family=family, unit=copy.deepcopy(unit),
                        original_id=original_id, reset_bid=reset_bid, replacements=replacements,
                        generated_cost=generated, replaced_bid=bool(replacing and unit.get('enabled', True)),
                        warnings=[f'{i.unit_id}：{i.msg}' for i in issues if i.level != 'error'])


def apply_unit_edit(plan):
    """Optimistic concurrency + quote/cost writer locks + recoverable atomic bundle."""
    root = Path(plan['root'])
    # Revalidate the actual request rather than trusting caller-supplied replacement
    # bytes. Recheck its snapshot under writer locks immediately before commit.
    fresh = prepare_unit_edit(root, plan['family'], plan['unit'], original_id=plan['original_id'],
                              reset_bid=plan['reset_bid'], expected=plan['before'])
    with file_lock(root / '.cost_archive.lock', blocking=True), file_lock(root / 'current_quote.lock', blocking=True), scene_input_lock(root):
        if scene_fingerprint(root) != fresh['before']:
            raise RuntimeError('场景、成本或报价已变化，请重新预览；未覆盖另一页面的修改。')
        versions = root / 'unit_edit_versions'
        if versions.is_symlink():
            raise ValueError('机组编辑备份目录不能是链接')
        versions.mkdir(exist_ok=True)
        identity = hashlib.sha256(_json(fresh['before'])).hexdigest()
        backup = versions / (identity + '.json')
        import base64
        previous = {relative: (base64.b64encode((root / relative).read_bytes()).decode()
                    if (root / relative).exists() else None) for relative in fresh['replacements']}
        content = _json(dict(schema=1, before=fresh['before'], files_base64=previous))
        if backup.exists():
            if backup.read_bytes() != content:
                raise ValueError('原机组输入备份不一致，禁止覆盖')
        else:
            from .unit_edit_transaction import atomic_bytes
            atomic_bytes(backup, content)
        commit_inputs(root, fresh['replacements'])
    return root
