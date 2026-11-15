"""Normal-entry persistence barrier. No cloud credentials or autonomous transport.

The optional deployment config binds an explicitly operated Dot/Library
coordinator. Ordinary local mode remains local, never advertised as external.
"""
from pathlib import Path
import json
from gd_resume_runtime.hourly_durable import read,sha,need,atomic,preflight_config


def review(output,config_path=None):
    if config_path is None:
        return dict(mode='local_only',external_persistence_available=False,
                    meets_independent_backup_requirement=False,
                    reason='No authorized independent transport configured; local files are not an external backup',
                    automatic_library_transport=False)
    p=Path(config_path).resolve();digest=sha(p)
    cfg=preflight_config(output,p,digest,normal_runtime=True)
    need(cfg.get('policy')=='cloud_required' and cfg.get('cadence')=='quarter',
         'ordinary external mode requires each executed 15-minute checkpoint')
    need(cfg.get('require_download_ack') is True,'upload-only acknowledgement is insufficient')
    need(cfg.get('transport')=='dot_library_coordinator',
         'no deployed authenticated autonomous transport; only explicit Dot coordinator is implemented')
    need(cfg.get('coordinator_operated') is True,'explicit live coordinator operation required')
    timeout=cfg.get('timeout_s',600)
    need(type(timeout) in (int,float) and 0<timeout<=3600,'checkpoint timeout must be positive and <=3600 seconds')
    stop=cfg.get('stop_after_executed_window')
    need(stop is None or(type(stop)is int and 0<=stop<96),'invalid bounded stop window')
    return dict(mode='external_required',config_path=str(p),config_sha256=digest,
                policy='cloud_required',cadence='quarter',require_download_ack=True,
                transport='dot_library_coordinator',automatic_library_transport=False,
                external_persistence_available='coordinator-dependent, not autonomous',
                meets_independent_backup_requirement='only after actual downloaded-content ACK at each boundary',
                timeout_s=timeout)


def preflight(output,contract):
    if not contract or contract.get('mode')=='local_only':return None
    need(review(output,contract.get('config_path'))==contract,'durable configuration changed')
    return read(contract['config_path'])


def commit(output,window,contract):
    cfg=preflight(output,contract)
    if cfg is None:return False
    from gd_resume_runtime.executed_quarter import write as write_state
    from gd_resume_runtime.hourly_durable import seal_quarter,pack,wait_for_upload
    out=Path(output).resolve()
    state=out/f'EXECUTED_QUARTER_W{window}.json'
    write_state(out,window,state)
    directory=seal_quarter(out,window,contract['config_path'],contract['config_sha256'],executed_state_path=state)
    pack(directory)
    wait_for_upload(directory,timeout_s=contract['timeout_s'])
    return cfg.get('stop_after_executed_window')==window


def progress(output,contract=None):
    from gd_resume_runtime.hourly_durable import progress as checkpoint_progress
    p=checkpoint_progress(output)
    return dict(mode=(contract or {}).get('mode','historical_unconfigured'),
                **p,automatic_library_transport=False,
                independent_backup_requirement_met_through=p['external_saved'],
                local_only_is_not_external_backup=True,
                scope='persistence evidence only, not model or operating safety')
