"""Independent, versioned energy-market cash accounting. Never an offer input."""
from __future__ import annotations
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import yaml

BASIS = 'spot_energy_cash_v2'
LEGACY = 'legacy_cash_operating_v1'
VERSION = 'spot-market-costs-v1'
FILENAME = 'spot_market_costs.yaml'
THERMAL = {'coal', 'gas_ccgt', 'gas_gt'}
KINDS = THERMAL | {'wind', 'pv', 'hydro', 'storage', 'pumped'}
SOURCE_KIND = 'research_assumption_not_observed_plant_data'
EXCLUSIONS = 'fixed/variable O&M,depreciation,cycle degradation,CAPEX,financing,tax,capacity,AGC,opportunity/replacement energy,inventory valuation,operator penalties'
ONLINE_BASES = ('no_load_fuel', 'online_operation_excluding_om')
FIELDS = {
    **{kind: ('fuel_cost_yuan_per_mwh', 'online_cost_yuan_per_h', 'startup_cost_yuan_per_start', 'shutdown_cost_yuan_per_stop') for kind in THERMAL},
    'pumped': ('gen_start_yuan_per_unit_start', 'pump_start_yuan_per_unit_start', 'gen_stop_yuan_per_unit_stop', 'pump_stop_yuan_per_unit_stop'),
    **{kind: () for kind in ('wind', 'pv', 'hydro', 'storage')},
}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def specs(cfg):
    return {u['id']: u.get('type', group) for group in ('thermal','renewable','hydro','storage','pumped')
            for u in cfg.units.get(group, []) if u.get('enabled', True)}


def validate_record(uid, record, typ=None):
    if not isinstance(record, dict) or record.get('type') not in KINDS or (typ and record['type'] != typ):
        raise ValueError(f'{uid}: 现货成本资源类型不匹配')
    kind = record['type']
    required = {'type', 'source', 'source_kind', *FIELDS[kind]}
    required |= {'online_cost_basis'} if kind in THERMAL else {'cost_scope'} if not FIELDS[kind] else set()
    if set(record) - required - {'derivation'} or required - set(record):
        raise ValueError(f'{uid}: 现货成本字段缺失或含未知字段')
    for key in ('source', 'source_kind'):
        if not isinstance(record[key], str) or not record[key].strip():
            raise ValueError(f'{uid}: 必须说明{key}')
    for key in FIELDS[kind]:
        v=record[key]
        if isinstance(v, bool) or not isinstance(v, (float,int)) or not math.isfinite(v) or v<0:
            raise ValueError(f'{uid}.{key}: 费用必须为有限非负数')
    if kind in THERMAL and record['online_cost_basis'] not in ONLINE_BASES:
        raise ValueError(f'{uid}: 在线成本须明确定义为燃料或不含运维的在线运行费')
    if not FIELDS[kind] and record['cost_scope'] != 'no_fuel_or_defined_start_stop_costs_excluding_om':
        raise ValueError(f'{uid}: 须明确无燃料/独立启停费用，不计运维/退化')
    if 'derivation' in record and not isinstance(record['derivation'], dict):
        raise ValueError(f'{uid}: 来源依据须为对象')
    return record


def validate_profile(profile, cfg=None):
    if (not isinstance(profile,dict) or profile.get('version') != VERSION or profile.get('profit_basis') != BASIS
            or not isinstance(profile.get('units'),dict) or not isinstance(profile.get('source'),str) or not profile['source'].strip()):
        raise ValueError('现货成本档案版本/范围/来源无效')
    if set(profile)-{'version','profit_basis','source','units','available','sha256','input_file','exclusions'}:
        raise ValueError('现货成本档案含未知字段')
    known=specs(cfg) if cfg is not None else None
    for uid, record in profile['units'].items():
        if not isinstance(uid,str) or not uid.strip(): raise ValueError('成本主体编号无效')
        # Disabled records may be retained as historical data; never used for other IDs.
        validate_record(uid, record, known.get(uid) if known else None)
    return profile


def _read(path):
    path=Path(path)
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError('成本文件不允许符号链接')
    return path.read_bytes() if path.exists() else None


def empty_profile():
    return dict(version=VERSION,profit_basis=BASIS,source='Independent spot-energy cash cost assumptions; not offers',units={})


class _UniqueLoader(yaml.SafeLoader):
    pass

def _unique_mapping(loader,node,deep=False):
    result={}
    for key_node,value_node in node.value:
        key=loader.construct_object(key_node,deep=deep)
        if key in result:raise ValueError(f'现货成本重复字段：{key}')
        result[key]=loader.construct_object(value_node,deep=deep)
    return result

_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,_unique_mapping)

def load_profile(cfg):
    path=Path(cfg.root)/'config'/FILENAME
    raw=_read(path)
    if raw is None:
        return dict(empty_profile(),available=False,sha256=None,input_file='config/'+FILENAME)
    profile=yaml.load(raw,Loader=_UniqueLoader)
    validate_profile(profile)
    return dict(profile,available=True,sha256=hashlib.sha256(raw).hexdigest(),input_file='config/'+FILENAME)


def clean_profile(profile):
    return {k:copy.deepcopy(v) for k,v in profile.items() if k not in ('available','sha256','input_file','exclusions')}


def parameters(profile, uid, typ):
    rec=profile.get('units',{}).get(uid)
    if rec is None:return None,'缺少独立现货成本；混合旧成本不能自动视为燃料或非运维在线费'
    if rec.get('type')!=typ:return None,'现货成本类型与当前机组不一致，需明确修订成本'
    validate_record(uid,rec,typ)
    return rec,''


def period_cost(record, energy, on, starts, pumpstarts, stops=0., pumpstops=0., dt=.25):
    """All quantities are executed; positive/negative cash lives outside this function."""
    v=record.get('fuel_cost_yuan_per_mwh',0.)*energy
    online=record.get('online_cost_yuan_per_h',0.)*on*dt
    startup=record.get('startup_cost_yuan_per_start',record.get('gen_start_yuan_per_unit_start',0.))*starts+record.get('pump_start_yuan_per_unit_start',0.)*pumpstarts
    shutdown=record.get('shutdown_cost_yuan_per_stop',record.get('gen_stop_yuan_per_unit_stop',0.))*stops+record.get('pump_stop_yuan_per_unit_stop',0.)*pumpstops
    if not all(math.isfinite(x) for x in (v,online,startup,shutdown,v+online+startup+shutdown)):raise ValueError('费用乘实际执行量溢出')
    return v,online,startup,shutdown


def _known_record(uid, raw, profile):
    from .research_defaults import is_known_legacy_research_record
    from .research_cost_setup import known_legacy_origin
    origin=profile.get('unit_sources',{}).get(uid,profile)
    if not known_legacy_origin(origin):return False
    extras={'PV2':dict(type='pv',scalar_marginal_cost_yuan_per_mwh=5),
            'PV3':dict(type='pv',scalar_marginal_cost_yuan_per_mwh=5),
            'ES4':dict(type='storage',discharge_degradation_yuan_per_mwh=25),
            'ES5':dict(type='storage',discharge_degradation_yuan_per_mwh=25)}
    return is_known_legacy_research_record(uid,raw) or (uid in extras and raw==extras[uid])


def research_defaults(cfg, *, original=None):
    """Detached proposal. Preserves recognized legacy fuel/start amounts, never custom guesses."""
    from .profit import load_fixed_cost_profile
    from .research_defaults import generate_research_defaults,research_record_hash
    original=load_fixed_cost_profile(cfg) if original is None else original
    result=empty_profile();missing={};generated=None
    for uid,typ in specs(cfg).items():
        raw=original.get('units',{}).get(uid)
        if raw is not None and raw.get('type')!=typ:
            missing[uid]='原成本类型已不匹配，须明确填写新类型成本';continue
        origin=original.get('unit_sources',{}).get(uid,{})
        evidence=origin.get('evidence',{})
        known=raw is not None and _known_record(uid,raw,original)
        if raw is None:
            if generated is None:generated=generate_research_defaults(cfg)
            raw=generated['profile']['units'][uid];evidence=generated['evidence'][uid]
        verified=bool(evidence and evidence.get('record_hash')==research_record_hash(raw))
        rec=dict(type=typ,source_kind=SOURCE_KIND,source='Explicit new spot-energy research assumption; excludes O&M/degradation. Shutdown rates assumed zero; not observed plant costs.')
        if typ in THERMAL:
            if known:
                fuel={'C1':270.,'C2':285.,'C3':300.,'CCGT':390.,'GT':600.}[uid]
            elif verified:
                p=evidence.get('parameters',{});fuel=p.get('fuel_price',float('nan'))*p.get('heat_rate',float('nan'))
                if not math.isfinite(fuel) or not math.isclose(fuel+p.get('variable_om',float('nan')),raw.get('scalar_marginal_cost_yuan_per_mwh',float('nan')),rel_tol=1e-12):
                    missing[uid]='生成证据不能分解燃料与运维';continue
            else:
                missing[uid]='自定义混合边际/在线成本须手工明确燃料和在线定义';continue
            rec.update(fuel_cost_yuan_per_mwh=fuel,online_cost_yuan_per_h=raw['fixed_online_cost_yuan_per_h'],
                       online_cost_basis='online_operation_excluding_om',startup_cost_yuan_per_start=raw['fixed_startup_cost_yuan_per_start'],shutdown_cost_yuan_per_stop=0.)
            rec['source']+=' Online amount is a NEW explicitly declared non-O&M running-cost assumption, not a verified decomposition of an ambiguous old online amount.'
        elif typ=='pumped':
            # Named startup parameters are unambiguous, unlike mixed scalar costs.
            rec.update(gen_start_yuan_per_unit_start=raw['gen_start_yuan_per_unit_start'],pump_start_yuan_per_unit_start=raw['pump_start_yuan_per_unit_start'],gen_stop_yuan_per_unit_stop=0.,pump_stop_yuan_per_unit_stop=0.)
        elif typ=='storage' or known or verified:
            rec['cost_scope']='no_fuel_or_defined_start_stop_costs_excluding_om'
        else:
            missing[uid]='自定义标量未明确是否为运维，须手工声明核算范围';continue
        rec['derivation']=dict(original_record=copy.deepcopy(raw),original_record_hash=research_record_hash(raw),
            classification='exact_known_legacy_research' if known else 'verified_generated_research' if verified else 'explicit_named_cost_fields',
            original_profile_sha256=original.get('sha256'),excluded='O&M and cycle degradation only; electricity purchase remains signed settlement')
        validate_record(uid,rec,typ);result['units'][uid]=rec
    return result,missing


def snapshot(root):
    return {name:hashlib.sha256(raw).hexdigest() if raw is not None else None
            for name in ('base.yaml','units.yaml','fixed_costs.yaml','generated_research_costs.json',FILENAME)
            for raw in [_read(Path(root)/'config'/name)]}


def prepare_edit(cfg, updates):
    before=snapshot(cfg.root);current=load_profile(cfg);profile=clean_profile(current);known=specs(cfg)
    for uid,record in updates.items():
        if uid not in known:raise ValueError(f'{uid}: 只能编辑启用机组')
        validate_record(uid,record,known[uid]);profile['units'][uid]=copy.deepcopy(record)
    validate_profile(profile)
    if before!=snapshot(cfg.root):raise ValueError('成本或机组已变化，请重新检查')
    return dict(root=str(cfg.root),snapshot=before,profile=profile,updates=copy.deepcopy(updates),changed=profile!=clean_profile(current))


def prepare_defaults(cfg):
    current=load_profile(cfg)
    if set(specs(cfg))<=set(current['units']):return prepare_edit(cfg,{}),{}
    profile,missing=research_defaults(cfg)
    updates={uid:r for uid,r in profile['units'].items() if uid not in current['units']}
    return prepare_edit(cfg,updates),missing


def apply_edit(plan, *, running=False):
    if running:raise ValueError('运行中不能保存成本')
    from .process_control import file_lock
    from .unit_edit_transaction import scene_input_lock
    from .config import load_config
    root=Path(plan['root'])
    with file_lock(root/'.cost_archive.lock',blocking=True),scene_input_lock(root):
        if snapshot(root)!=plan['snapshot']:raise ValueError('成本或机组已变化，请重新检查')
        cfg=load_config(root=root);validate_profile(plan['profile'])
        current=load_profile(cfg)
        rebuilt=clean_profile(current)
        for uid,rec in plan['updates'].items():
            if uid not in specs(cfg):raise ValueError('只能编辑当前启用机组')
            validate_record(uid,rec,specs(cfg)[uid]);rebuilt['units'][uid]=copy.deepcopy(rec)
        if rebuilt!=plan['profile']:raise ValueError('保存计划与已校验编辑不一致')
        if clean_profile(current)==plan['profile']:return root/'config'/FILENAME
        if any(uid not in specs(cfg) and current['units'].get(uid)!=rec for uid,rec in plan['profile']['units'].items()):
            raise ValueError('不能改写未知或停用机组成本')
        data=yaml.safe_dump(plan['profile'],allow_unicode=True,sort_keys=False).encode()
        target=root/'config'/FILENAME;versions=root/'spot_cost_versions'
        if versions.is_symlink():raise ValueError('成本版本目录不允许链接')
        versions.mkdir(exist_ok=True)
        for raw in (_read(target),data):
            if raw is None:continue
            path=versions/(hashlib.sha256(raw).hexdigest()+'.yaml')
            if path.exists():
                if _read(path)!=raw:raise ValueError('历史成本版本已损坏')
            else:
                with path.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        temp=None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent,delete=False) as stream:
                temp=Path(stream.name);stream.write(data);stream.flush();os.fsync(stream.fileno())
            if snapshot(root)!=plan['snapshot']:raise ValueError('保存期间成本输入变化')
            os.replace(temp,target)
        finally:
            if temp is not None:temp.unlink(missing_ok=True)
        return target


def initialize_new_scene(root):
    """Explicit scene creation only; existing scenes are never migrated on read."""
    from .config import load_config
    raw=yaml.safe_load(_read(Path(root)/'config/units.yaml')) or {}
    if not any(raw.get(k) for k in ('thermal','renewable','hydro','storage','pumped')):return
    cfg=load_config(root=root)
    plan,_=prepare_defaults(cfg)
    if plan['changed']:apply_edit(plan)
