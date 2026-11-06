"""Persistent, date-specific task drafts. No solver or implicit daily bids."""
from __future__ import annotations
import copy
from contextlib import contextmanager
from datetime import date, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
from uuid import uuid4, uuid5, NAMESPACE_URL
from zoneinfo import ZoneInfo

SCHEMA = 1
KINDS = ('wind', 'pv', 'load', 'natural_inflow')
SCENARIO_FIELDS = ('season', 'wind_multiplier', 'pv_multiplier', 'load_multiplier')
LOCK_FIELDS = SCENARIO_FIELDS + tuple(f'{group}.{kind}' for group in ('process_specs','forecast_policy') for kind in KINDS)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    tmp = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    with tmp.open('x', encoding='utf-8') as stream:
        stream.write(text); stream.flush(); os.fsync(stream.fileno())
    os.replace(tmp, path)


@contextmanager
def locked(root):
    from .process_control import file_lock
    with file_lock(Path(root) / '.draft.lock',blocking=True):
        yield


def validate_manifest(m):
    if m.get('schema_version') != SCHEMA: raise ValueError('不支持的任务版本')
    if type(m.get('revision')) is not int or m['revision'] < 1: raise ValueError('任务修订号非法')
    ZoneInfo(m['timezone'])
    start = date.fromisoformat(m['start_date'])
    days = m['days']
    if not 1 <= len(days) <= 31: raise ValueError('连续天数须为1至31')
    if len({d['day_uid'] for d in days}) != len(days): raise ValueError('每日稳定编号重复')
    if m['generation']['model'] not in ('deterministic_known_inputs_v1','causal_physical_v3'):raise ValueError('不支持的输入方式')
    seed = m['generation']['root_seed']
    if not isinstance(seed, str) or not seed.isdecimal() or len(seed)>80: raise ValueError('随机种子须为不超过80位的非负十进制整数')
    for i, d in enumerate(days):
        if d['date'] != (start + timedelta(days=i)).isoformat(): raise ValueError('日期必须连续且有序')
        if d['season'] not in ('wet','dry'): raise ValueError('水文模板非法')
        for k in ('wind_multiplier','pv_multiplier','load_multiplier'):
            v=d[k]
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not .1<=v<=2:
                raise ValueError(f'{k}须为0.1至2的有限数值')
        if set(d['locked']) - set(LOCK_FIELDS): raise ValueError('锁定字段未知')
    if m['execution']['timing'] != 'sequential_actual_carryover': raise ValueError('不支持独立单日循环冒充连续运行')
    return m


def create_task(tasks_root, source_root, *, title, start_date, day_count, timezone, root_seed, input_model='deterministic_known_inputs_v1'):
    from .config import load_config
    from .forecast import generate_profiles, apply_profile_overrides
    from .unit_edit_transaction import scene_input_lock
    src=Path(source_root).resolve(); parent=Path(tasks_root);parent.mkdir(parents=True,exist_ok=True)
    if type(day_count) is not int or not 1<=day_count<=31: raise ValueError('连续天数须为1至31')
    title=str(title).strip()
    if not title or len(title)>80: raise ValueError('任务名称须为1至80字')
    start=date.fromisoformat(str(start_date));ZoneInfo(timezone)
    task_id=uuid4().hex; root=parent/task_id;root.mkdir()
    try:
        with scene_input_lock(src):
            cfg=load_config(root=src)
            snapshot=root/'source';snapshot.mkdir()
            for folder in ('config','data'):
                shutil.copytree(src/folder,snapshot/folder)
            source_files={str(p.relative_to(snapshot)):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(snapshot.rglob('*')) if p.is_file()}
            profiles=apply_profile_overrides(cfg,generate_profiles(cfg))
            atomic_json(snapshot/'baseline_profiles.json',dict(columns=profiles.to_dict('list')))
            quote=src/'current_quote.json'
            if quote.is_file():
                candidate=json.loads(quote.read_text(encoding='utf-8'))['draft']
            else:
                from .bids import load_bids
                try: candidate=load_bids(cfg)
                except FileNotFoundError: candidate=None
            if candidate is not None: atomic_json(snapshot/'quote_candidate.json',candidate)
            source_files={str(p.relative_to(snapshot)):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(snapshot.rglob('*')) if p.is_file()}
        days=[]
        for i in range(day_count):
            ds=(start+timedelta(days=i)).isoformat()
            days.append(dict(day_uid=uuid5(NAMESPACE_URL,'smm-operating-day:'+ds).hex,date=ds,
                season=cfg.base['season'],wind_multiplier=1.,pv_multiplier=1.,load_multiplier=1.,
                locked=[],source='base_snapshot',bid_package=None,bid_receipt=None,
                process_specs={kind:dict(preset=None,parameters={}) for kind in KINDS},forecast_policy={}))
        m=dict(schema_version=SCHEMA,task_id=task_id,title=title,revision=1,start_date=start.isoformat(),
            timezone=timezone,source=dict(path=str(src),name=src.name,files=source_files,hash=digest(source_files)),
            generation=dict(model=input_model,root_seed=str(root_seed),realization_id='1'),
            execution=dict(timing='sequential_actual_carryover',mode='all_ready'),days=days,
            initial_state=None,initial_state_source=None,validation=None)
        validate_manifest(m);atomic_json(root/'manifest.json',m)
        return root
    except BaseException:
        shutil.rmtree(root)
        raise


def load_task(root):
    return validate_manifest(json.loads((Path(root)/'manifest.json').read_text(encoding='utf-8')))


def list_tasks(tasks_root):
    result=[]
    for root in sorted(Path(tasks_root).glob('*')):
        if not root.is_dir() or not (root/'manifest.json').is_file(): continue
        try: result.append((root,load_task(root),None))
        except (ValueError,KeyError,OSError,TypeError) as exc: result.append((root,None,str(exc)))
    return result


def _begun(root): return (Path(root)/'run'/'series_identity.json').exists() or (Path(root)/'launch.json').exists()


def update_task(root, m, *, expected_revision, changed_day=None, bids_only=False):
    root=Path(root)
    with locked(root):
        current=load_task(root)
        if current['revision'] != expected_revision: raise RuntimeError('另一会话已保存任务，请重新载入；没有覆盖')
        if m['task_id'] != current['task_id']: raise ValueError('任务身份不可更改')
        if _begun(root):
            if not bids_only or changed_day is None: raise ValueError('已启动任务输入已冻结；请新建任务分支')
            if (root/'run'/f'day_{changed_day}').exists(): raise ValueError('该日报价已进入出清，不可修改')
            old=copy.deepcopy(current);new=copy.deepcopy(m)
            for content in (old,new):
                content.pop('validation',None)
                for d in content['days']:
                    if d['date']==changed_day:
                        d['bid_package']=None;d['bid_receipt']=None;d.pop('bid_source',None)
            if old!=new: raise ValueError('运行后只可修改尚未开始日的独立报价')
        out=copy.deepcopy(m);out['revision']=expected_revision+1;out['validation']=None
        validate_manifest(out);atomic_json(root/'manifest.json',out)
        return out


def day_by_date(m, day_date):
    return next(d for d in m['days'] if d['date']==str(day_date))


def randomize_unlocked(m, dates):
    out=copy.deepcopy(m)
    choices=dict(season=('wet','dry'),wind_multiplier=(.75,1.,1.25),pv_multiplier=(.75,1.,1.25),load_multiplier=(.9,1.,1.1))
    if out['generation']['model']=='causal_physical_v3':
        from .causal_presets import capabilities
        catalog=capabilities()
        choices={'season':choices['season']}
        choices.update({f'process_specs.{kind}':tuple(catalog['actual_presets'][kind]) for kind in KINDS})
        choices.update({f'forecast_policy.{kind}':tuple(catalog['forecast_policies']) for kind in KINDS})
    for d in out['days']:
        if d['date'] not in dates: continue
        for field,options in choices.items():
            if field in d['locked']: continue
            value=int(digest([out['generation']['root_seed'],d['day_uid'],field]),16)
            selected=options[value%len(options)]
            if field.startswith('process_specs.'):
                d['process_specs'][field.split('.')[1]]=dict(preset=selected,parameters={})
            elif field.startswith('forecast_policy.'):
                d['forecast_policy'][field.split('.')[1]]=selected
            else:d[field]=selected
        d['source']='seeded_scenario_choices_not_independent_profile_noise'
    return out


def copy_day_settings(m, source_date, target_dates, *, include_locked=False):
    out=copy.deepcopy(m);source=day_by_date(out,source_date)
    for d in out['days']:
        if d['date'] not in target_dates or d is source: continue
        for k in SCENARIO_FIELDS:
            if include_locked or k not in d['locked']:d[k]=copy.deepcopy(source[k])
        for group in ('process_specs','forecast_policy'):
            for kind,value in source[group].items():
                if include_locked or f'{group}.{kind}' not in d['locked']:d[group][kind]=copy.deepcopy(value)
        d['source']=f'explicit_copy:{source_date}'
    return out


def replace_daily_bids(m, day_date, bids, *, source):
    out=copy.deepcopy(m);day=day_by_date(out,day_date)
    previous=day.get('bid_package')
    b=copy.deepcopy(bids)
    if not isinstance(b,dict) or not isinstance(b.get('units'),dict):raise ValueError('报价须含units对象')
    b['date']=day_date;b['version']=int(previous['version'])+1 if previous else 1
    day['bid_package']=b;day['bid_receipt']=None;day['bid_source']=source
    return out


def confirm_daily_bids(m, day_date, cfg):
    from .bids import validate_bids,raise_on_errors
    out=copy.deepcopy(m);day=day_by_date(out,day_date);b=day.get('bid_package')
    if b is None:raise ValueError('此日还没有独立报价')
    if b.get('date')!=day_date or b.get('rule_set')!=cfg.rule_set:raise ValueError('报价日期或规则不匹配')
    if type(b.get('version')) is not int or b['version']<1:raise ValueError('报价版本非法')
    raise_on_errors(validate_bids(cfg,b,require_complete=True))
    day['bid_receipt']=dict(date=day_date,version=b['version'],confirmed=True,bid_hash=digest(b))
    return out


def bid_status(day):
    b=day.get('bid_package');r=day.get('bid_receipt')
    if b is None:return '未报价'
    if not isinstance(r,dict) or r.get('confirmed') is not True:return '待确认'
    if r.get('date')!=day['date'] or b.get('date')!=day['date'] or r.get('version')!=b.get('version') or r.get('bid_hash')!=digest(b):return '确认已失效'
    return '已确认'


def input_signature(m):
    return digest({k:v for k,v in m.items() if k not in ('revision','validation','title')})


def fork_research_task(root,m,*,reason):
    """Explicit discontinuous research branch; never mutates an execution chain."""
    if not str(reason).strip():raise ValueError('请填写研究初态变更原因')
    if m.get('initial_state') is None:raise ValueError('尚无可复制的初态')
    root=Path(root);newroot=root.parent/uuid4().hex
    newroot.mkdir()
    try:
        shutil.copytree(root/'source',newroot/'source')
        new=copy.deepcopy(m);new['task_id']=newroot.name;new['revision']=1
        new['title']=m['title']+'（研究分支）';new['validation']=None
        new['parent']=dict(task_id=m['task_id'],initial_state_hash=digest(m['initial_state']),reason=str(reason).strip(),continuity_broken=True,origin='research_reset')
        new['initial_state_source']='research_assumption'
        state=new['initial_state']['state'];state['continuation']['source']='research_assumption';state['continuation']['previous_checkpoint_hash']=None
        if state.get('hydro_commitment'):state['hydro_commitment']['source']='research_assumption'
        # Copying a task is not daily re-approval of quotations.
        for day in new['days']:day['bid_receipt']=None
        validate_manifest(new);atomic_json(newroot/'manifest.json',new)
        return newroot
    except BaseException:
        shutil.rmtree(newroot);raise


def revise_from_current_base(root,m):
    """Explicit new task from today's base; preserve old task and manual bids."""
    root=Path(root)
    child=create_task(root.parent,m['source']['path'],title=m['title']+'（基础修订）',
        start_date=m['start_date'],day_count=len(m['days']),timezone=m['timezone'],root_seed=m['generation']['root_seed'])
    new=load_task(child)
    new['days']=copy.deepcopy(m['days']);new['generation']=copy.deepcopy(m['generation'])
    new['parent']=dict(task_id=m['task_id'],origin='explicit_base_revision',continuity_broken=True)
    for day in new['days']:day['bid_receipt']=None
    # New physics/identities require first-boundary review, never retain a
    # potentially incompatible initial state or silently fill unit quotes.
    new['initial_state']=None;new['initial_state_source']=None
    validate_manifest(new);atomic_json(child/'manifest.json',new)
    return child
