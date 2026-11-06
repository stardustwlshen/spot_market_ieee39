"""Adapter from a saved UI manifest to the real sequential-state engine."""
from __future__ import annotations
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import hashlib
import numpy as np
import pandas as pd
from .series_workspace import (load_task, atomic_json, locked, input_signature, day_by_date,
                               bid_status, digest)


def capabilities():
    engine=all(importlib.util.find_spec('smm.'+name) is not None for name in ('series','state_bridge','hydro_commitment'))
    causal=None
    if importlib.util.find_spec('smm.causal_presets') is not None:
        from .causal_presets import capabilities as catalog
        causal=catalog()
    contract=None
    if importlib.util.find_spec('smm.series_inputs') is not None:
        from .series_inputs import consumer_contract
        contract=consumer_contract(required=False)
    return dict(engine=engine,causal=causal,causal_execution_contract=contract)


def verify_source(root,m):
    root=Path(root)
    for relative,expected in m['source']['files'].items():
        path=root/'source'/relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            raise ValueError('任务基础快照已变化：'+relative)
    return True


def day_config(root,m,date):
    from .config import load_config
    d=day_by_date(m,date)
    cfg=load_config(root=Path(root)/'source',overrides=dict(date=date,simulation_timezone=m['timezone'],season=d['season']))
    if m['generation']['model']=='causal_physical_v3':
        cfg.base['forecast']={'model':'causal_physical_v3'}
    return cfg


def deterministic_profiles(root,m,date):
    """Explicit perfect-information replay, never a causal forecast fallback."""
    cfg=day_config(root,m,date);d=day_by_date(m,date)
    data=json.loads((Path(root)/'source/baseline_profiles.json').read_text(encoding='utf-8'))
    p=pd.DataFrame(data['columns'])
    items=[('load',d['load_multiplier'],None)]
    for u in cfg.units['renewable']:
        items.append((u['id'],d['wind_multiplier' if u['type']=='wind' else 'pv_multiplier'],float(u['cap'])))
    for name,mult,cap in items:
        values=p[name+'_actual'].to_numpy(float)*mult
        if cap is not None:values=np.minimum(values,cap)
        for stage in ('actual','da','id','rt'):p[name+'_'+stage]=values
    from .forecast import validate_profiles
    validate_profiles(cfg,p)
    p.attrs['information_policy']='deterministic_known_inputs_v1'
    p.attrs['source']='frozen_existing_actual_profile_explicitly_known_for_all_stages'
    return p


def compile_days(root,m):
    from .series import DayInputs
    from .network import build_network
    verify_source(root,m)
    model=m['generation']['model']
    causal=model=='causal_physical_v3'
    if causal:
        from .series_inputs import consumer_contract,public_schedule_profiles,SeriesSource
        consumer_contract()  # Installed runtime contract, never a UI claim.
        _,_,bundle=prepared_causal_bundle(root,m)
        source=SeriesSource(bundle)
        public_frame=pd.DataFrame(json.loads((Path(root)/'source/baseline_profiles.json').read_text())['columns'])
    elif model!='deterministic_known_inputs_v1':
        raise ValueError('不支持的输入方式；不会退回旧预测')
    days=[]
    for d in m['days']:
        cfg=day_config(root,m,d['date'])
        profiles=public_schedule_profiles(cfg,public_frame) if causal else deterministic_profiles(root,m,d['date'])
        days.append(DayInputs(cfg,build_network(cfg),profiles,copy.deepcopy(d['bid_package']),
            copy.deepcopy(d['bid_receipt']),information_policy=model))
    return source.bind(days) if causal else days


def causal_daily_selection(root,m,date):
    from .state_bridge import at
    _,provider,_=prepared_causal_bundle(root,m)
    return provider.select_day(at(day_config(root,m,date)),0,0,stage='DA',
        current_interval=False,executed_through_period=0)


def apply_issued_bid_availability(root,m,date):
    """Explicit UI action: use that day's issued DA forecast, keep prices intact."""
    day=day_by_date(m,date)
    if not isinstance(day['bid_package'],dict):raise ValueError('请先生成或复制该日报价')
    selected=causal_daily_selection(root,m,date)
    bids=copy.deepcopy(day['bid_package'])
    for series in selected.series:
        if series.kind not in ('wind','pv'):continue
        row=bids['units'][series.resource_id]
        row['avail_pmax']=list(series.values)
        row['availability_provenance']=dict(kind='issued_forecast',forecast_id=selected.forecast_id,
            issue_time_utc=selected.issue_time_utc,information_set_id=selected.information_set_id)
    return bids


def research_initial(root,m):
    from .state_bridge import research_initial_state,pack_state
    cfg=day_config(root,m,m['start_date'])
    if any(h.get('commitment',{}).get('mode')!='unit_duration_v1' for h in cfg.units.get('hydro',[])):
        raise ValueError('请先在基础场景的机组参数中声明水电逐台初态，再新建任务；不能推测持续时间')
    state=research_initial_state(cfg,acknowledge_constant_prehistory=True)
    return pack_state(cfg,state)


def validate_initial(root,m,packed):
    from .state_bridge import unpack_state,validate_system_state,pack_state
    cfg=day_config(root,m,m['start_date'])
    state=unpack_state(cfg,packed)
    validate_system_state(cfg,state,expected_t=0,continuation_required=True)
    return pack_state(cfg,state)


def update_research_state(root,m,updates):
    """Explicit first-boundary declaration only; executed imports are read-only."""
    from .state_bridge import unpack_state,pack_state,validate_system_state
    from . import hydro_commitment as hc
    if m['initial_state_source']!='research_assumption':raise ValueError('实际接续初态只读；需新研究分支才能修改')
    cfg=day_config(root,m,m['start_date']);state=unpack_state(cfg,m['initial_state'])
    expected={'thermal','storage','hydro','pumped','hydro_units'}
    if set(updates)-expected:raise ValueError('不支持的初态编辑类别')
    for uid,row in updates.get('thermal',{}).items():
        if uid not in state.thermal:raise ValueError('未知火电编号')
        state.thermal[uid].update(row)
    for uid,value in updates.get('storage',{}).items():
        if uid not in state.storage:raise ValueError('未知电池编号')
        state.storage[uid]=value
    for uid,row in updates.get('hydro',{}).items():
        if uid not in state.hydro:raise ValueError('未知水电编号')
        if 'V' in row:state.hydro[uid]=row['V'];state.continuation['day_initial_hydro'][uid]=row['V']
        if 'V' in row and hasattr(state,'day_initial_hydro'):state.day_initial_hydro[uid]=row['V']
        if 'P' in row:state.hydro_p[uid]=row['P']
    for uid,row in updates.get('pumped',{}).items():
        if uid not in state.pumped:raise ValueError('未知抽蓄编号')
        state.pumped[uid].update(row)
    if updates.get('hydro_units'):
        for spec in cfg.units.get('hydro',[]):
            rows=updates['hydro_units'].get(spec['id'])
            if rows is None:
                rows={uid:{k:r[k] for k in ('online','elapsed_h','elapsed_kind')}
                    for uid,r in state.hydro_commitment['stations'][spec['id']]['units'].items()}
            expected_ids={r['unit_id'] for r in spec['commitment']['initial_units']}
            if set(rows)!=expected_ids:raise ValueError('逐台稳定编号不匹配')
            spec['commitment']['initial_units']=[dict(unit_id=uid,**row) for uid,row in rows.items()]
            spec['init_units_on']=sum(row['online'] for row in rows.values())
            state.hydro_online[spec['id']]=spec['init_units_on']
        state.hydro_commitment=hc.initial_state(cfg)
    validate_system_state(cfg,state,expected_t=0,continuation_required=True)
    return pack_state(cfg,state)


def readiness(root,m,mode):
    errors=[];warnings=[];days=[]
    try:
        if not capabilities()['engine']:raise ValueError('真实跨日状态引擎尚未安装')
        days=compile_days(root,m)
        if m['initial_state'] is None:raise ValueError('请查看并确认首日完整初态')
        from .state_bridge import unpack_state,validate_system_state
        s=unpack_state(days[0].cfg,m['initial_state'])
        validate_system_state(days[0].cfg,s,expected_t=0,continuation_required=True)
        from .series import validate_day,AwaitingBids
        for d in days:
            try:
                validate_day(d)
                if d.information_policy=='causal_physical_v3':
                    from .natural_inflow import validate_bid_availability
                    validate_bid_availability(d.cfg,d.bid_data,causal_daily_selection(root,m,str(d.cfg.base['date'])))
            except AwaitingBids:
                if mode=='ready_prefix':
                    warnings.append(f"{d.cfg.base['date']} 未确认报价，将停在该日前");break
                raise
        if mode=='ready_prefix' and bid_status(m['days'][0])!='已确认':raise ValueError('首日报价尚未确认，没有可运行前缀')
    except (ImportError,ValueError,KeyError,TypeError,OSError,AttributeError) as exc:errors.append(str(exc))
    return dict(ready=not errors,errors=errors,warnings=warnings,input_signature=input_signature(m),mode=mode)


def launch_state(root):
    p=Path(root)/'launch.json'
    if not p.exists():return 'never_started'
    from .process_control import process_state
    d=json.loads(p.read_text(encoding='utf-8'))
    if d.get('status')=='not_started':return 'dead'
    if d.get('status') in ('starting','uncertain'):return 'unknown'
    return process_state(d.get('identity'))


def launch(root,*,expected_signature,mode):
    from .process_control import process_identity,LaunchError
    root=Path(root).resolve()
    from .runtime_identity import source_digest
    expected=os.environ.get('IEEE39_LAUNCH_SOURCE_SHA256')
    if expected and source_digest(Path(__file__).resolve().parents[2])!=expected:
        raise RuntimeError('源码更新后须先重启界面，不能用旧模块启动新任务')
    with locked(root):
        m=load_task(root)
        if input_signature(m)!=expected_signature:raise ValueError('输入在验证后已变化，请重新验证')
        if launch_state(root) in ('alive','unknown'):raise RuntimeError('已有运行或其进程身份未核实，拒绝重复启动')
        result=readiness(root,m,mode)
        if not result['ready']:raise ValueError('；'.join(result['errors']))
        # One immutable manifest per explicit launch; the engine enforces the
        # already-committed per-day hash chain and blocks unfinished days.
        frozen=root/f'launch_manifest_r{m["revision"]}.json'
        if frozen.exists():
            if json.loads(frozen.read_text())!=m:raise ValueError('同修订启动输入已改变')
        else:atomic_json(frozen,m)
        cancel=root/'cancel_after_day.request'
        if cancel.exists():cancel.unlink()
        atomic_json(root/'launch.json',dict(status='starting',revision=m['revision'],manifest=frozen.name))
        cmd=[sys.executable,'-m','smm.series_ui_runner',str(root),str(frozen),mode]
        env=dict(os.environ);src=str(Path(__file__).resolve().parents[1]);env['PYTHONPATH']=src+os.pathsep+env.get('PYTHONPATH','')
        try:
            with (root/'run.log').open('ab') as log:
                p=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,cwd=Path(__file__).resolve().parents[2],env=env,start_new_session=True)
        except Exception as exc:
            atomic_json(root/'launch.json',dict(status='not_started',identity=None,error=str(exc)))
            raise LaunchError('多日启动',exc,directory=root,process_started=False)
        try:identity=process_identity(p.pid)
        except Exception as exc:
            atomic_json(root/'launch.json',dict(status='uncertain',pid=p.pid,manifest=frozen.name,error=str(exc)))
            raise LaunchError('核实多日进程身份',exc,directory=root,process_started=True)
        atomic_json(root/'launch.json',dict(status='launched',identity=identity,manifest=frozen.name,revision=m['revision']))
        return p.pid


def completed_day_metadata(tasks_root):
    result=[]
    for path in sorted(Path(tasks_root).glob('*/run/day_*/day_commit.json')):
        directory=path.parent
        try:
            commit=json.loads(path.read_text());summary=json.loads((directory/'summary.json').read_text())
            title=load_task(directory.parents[1])['title']
            if summary.get('completed_scope')!='full_day':continue
            result.append(dict(dir=str(directory),scenario=title,display_label=f"{title} · {commit['date']}",mode='full',status='done',
                started=commit['date'],bid_version=json.loads((directory/'scenario_snapshot/bid_data.json').read_text())['version'],
                series_root=str(directory.parent),operating_date=commit['date'],snapshot_root=str(directory/'scenario_snapshot')))
        except (ValueError,KeyError,OSError,TypeError):continue
    return result


def import_actual_checkpoint(root,m,checkpoint):
    from .state_bridge import next_day_state,pack_state
    from .network import build_network
    cfg=day_config(root,m,m['start_date'])
    state=next_day_state(checkpoint,cfg,net=build_network(cfg))
    return pack_state(cfg,state)


def causal_plan(root,m):
    """Compile visible daily cards into actual consumed physical parameters."""
    from datetime import datetime,timezone
    from zoneinfo import ZoneInfo
    from .config import load_config
    from .causal_provider import resources_from_config,seasonal_inflow_spec
    from .causal_processes import ProcessPlan
    from .causal_presets import DEFAULTS,resolve_actual
    verify_source(root,m)
    cfg=load_config(root=Path(root)/'source')
    resources=resources_from_config(cfg)
    specs={};policies={}
    hydros={h['id']:h for h in cfg.units['hydro']}
    for day in m['days']:
        byresource={}
        for r in resources:
            spec=copy.deepcopy(day['process_specs'][r.kind])
            if not spec.get('preset'):spec['preset']=DEFAULTS[r.kind]
            resolve_actual(r.kind,spec)
            if r.kind=='natural_inflow':
                qin=float(hydros[r.resource_id]['seasons'][day['season']]['qin'])
                spec=seasonal_inflow_spec(r,qin,spec)
            byresource[r.resource_id]=spec
        specs[day['date']]=byresource
        policies[day['date']]={kind:day['forecast_policy'].get(kind,'baseline') for kind in ('wind','pv','load','natural_inflow')}
    from .causal_types import utc_iso
    start=utc_iso(datetime.fromisoformat(m['start_date']).replace(tzinfo=ZoneInfo(m['timezone'])))
    plan=ProcessPlan(start_utc=start,day_count=len(m['days']),timezone=m['timezone'],resources=resources,
        day_specs=specs,root_seed=m['generation']['root_seed'],realization_id=m['generation']['realization_id'])
    return plan,policies


def prepared_causal_bundle(root,m):
    from .causal_provider import compile_provider,save_bundle,load_bundle
    from .causal_types import plain
    plan,policies=causal_plan(root,m)
    signature=digest(dict(plan=plain(plan),policies=policies))
    bundle=Path(root)/'causal_previews'/signature
    from .process_control import file_lock
    with file_lock(Path(root)/'.causal_preview.lock',blocking=True):
        if bundle.exists():actual,provider=load_bundle(bundle)
        else:
            actual,provider=compile_provider(plan,forecast_policy_by_date=policies)
            save_bundle(bundle,actual,forecast_policy_by_date=policies)
    return actual,provider,bundle


def causal_preview(root,m,day_date,*,stage='DA',issue_period=0):
    from .causal_types import utc_seconds,utc_iso
    from datetime import datetime,timezone
    from zoneinfo import ZoneInfo
    actual,provider,bundle=prepared_causal_bundle(root,m)
    start=datetime.fromisoformat(day_date).replace(tzinfo=ZoneInfo(m['timezone'])).astimezone(timezone.utc).isoformat()
    issue=0 if stage=='DA' else int(issue_period)
    selected=provider.select_day(start,issue,issue,stage=stage,current_interval=False,executed_through_period=issue)
    truth={r.resource_id:actual.actual(r.resource_id,selected.start_utc,selected.end_utc).tolist() for r in actual.plan.resources}
    return dict(bundle=str(bundle),actual_hash=actual.content_hash,evidence=selected.evidence(),
        issue_time_utc=selected.issue_time_utc,resources=[dict(id=r.resource_id,kind=r.kind,unit=r.unit) for r in actual.plan.resources],
        period=list(range(issue,96)),actual=truth,forecast={r.resource_id:selected.values(r.resource_id).tolist() for r in actual.plan.resources})


def series_source_status(meta):
    """Verify the actual day commit and compare only that date's draft inputs."""
    directory=Path(meta['dir']);root=Path(meta['series_root']).parent
    try:
        from .series import _verify_commit,day_fingerprint
        admission=json.loads((directory/'admission.json').read_text())
        checkpoint,_=_verify_commit(directory,admission['input_hash'])
        m=load_task(root);days=compile_days(root,m)
        day=next(d for d in days if str(d.cfg.base['date'])==meta['operating_date'])
        equal=day_fingerprint(day)==admission['input_hash']
        return dict(status='equal' if equal else 'changed',changes=[] if equal else ['该日有效输入或日报价'],
            message='该日输入与实际执行提交已核对一致；图表绑定本日冻结快照。' if equal else '该日草稿已变化；以下仍为原冻结结果，不叠加新输入。',
            detail=f"运行日：{meta['operating_date']}\n日报价：{admission['bid_hash']}\n实际接续：{checkpoint['checkpoint_hash']}")
    except (ValueError,KeyError,OSError,ImportError,TypeError,StopIteration) as exc:
        return dict(status='unknown',changes=[],message='该日来源暂不能完整核对，仍只读冻结结果，不用当前输入补齐。',detail=str(exc))
