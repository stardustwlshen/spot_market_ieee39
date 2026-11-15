"""Read-only, hash-bound proof of continuation FROM a source checkpoint.
An uploaded or downloaded output checkpoint alone never qualifies.
"""
from pathlib import Path
import hashlib,json

def need(ok,message):
    if not ok: raise ValueError(message)
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p): return json.loads(Path(p).read_bytes())
def verify(sidecar):
    z=read(sidecar)
    need(z['schema']=='gd.restore.source_checkpoint_verification.v1','unsupported verification descriptor')
    objects={}
    for name in ('report','checkpoint_ready','checkpoint_ledger','continuation_plan','continuation_config'):
        d=z[name];need(sha(d['path'])==d['sha256'],'verification evidence changed: '+name)
        objects[name]=read(d['path'])
    report,ready,ledger,plan,config=(objects[n]for n in ('report','checkpoint_ready','checkpoint_ledger','continuation_plan','continuation_config'))
    need(ready.get('rt_clock_mode')=='provincial_15min_execution' and ready.get('grid_minutes')==15,'old grid recovery proof cannot validate new province checkpoint')
    need(report['schema']=='gd.independent_restore.provincial15.two_executed_steps.v1','unsupported actual verification report')
    for key in ('physical_and_no_double_count_checks_passed','two_first_cells_executed_once','thermal_power_storage_energy_age_cycles_match_previous_executed_end','source_model_unchanged_between_steps'):
        need(report.get(key) is True,'independent continuation checks not passed: '+key)
    need(report.get('prior_windows_reoptimized') is False,'prior results were reoptimized')
    need(ready['checkpoint_kind']=='quarter' and ready['window']==ledger['window'],'source checkpoint identity differs')
    need(ready['executed_end_exclusive']==ledger['next_execute_tick'],'source boundary differs')
    need(plan['source_output']==ready['output'],'continuation did not use this source output')
    need(plan['output']==report['source_output'],'report is not for this continuation')
    need(plan['durable_checkpoint']['config_sha256']==z['continuation_config']['sha256'],'continuation configuration not bound')
    previous=config['previous_execution_ledger']
    need(previous['sha256']==z['checkpoint_ledger']['sha256'],'continuation did not consume this checkpoint ledger')
    need(ledger['next_execute_tick'] < report['executed_end_tick'],'checkpoint is the output, not tested source')
    ext=[e for e in report['external_checkpoints']if e['window']==ready['window']]
    need(len(ext)==1 and ext[0]['actual_download_verified_payloads']>0,'source external copy not verified')
    need(z['source_archive_sha256']==ext[0]['archive_sha256'],'source external archive differs')
    need(z['source_library_file_id']==ext[0]['library_file_id'],'source external Library identity differs')
    return dict(window=ready['window'],executed_end_tick=ledger['next_execute_tick'],meaning='source checkpoint independently resumed; not a claim about later output checkpoints',checkpoint_manifest_sha256=z['checkpoint_ready']['sha256'],checkpoint_ledger_sha256=z['checkpoint_ledger']['sha256'],report_sha256=z['report']['sha256'],continuation_output=plan['output'],verified_through_tick=report['executed_end_tick'],source_archive_sha256=z['source_archive_sha256'])
