"""Real two-day archive-only acceptance and separate synthetic 31-day retention stress."""
import copy,gc,json,weakref,hashlib,ast
from datetime import date,timedelta
from pathlib import Path
from types import SimpleNamespace as NS
import numpy as np
import pandas as pd
import psutil
import pytest
from smm import series,full,scuc,state_bridge as bridge,result_retention as retention
from multiday_fixture import micro_day
from multiday_replay import replay


def rss():
    return psutil.Process().memory_info().rss


def test_rss_uses_portable_process_memory_in_bytes(monkeypatch):
    expected = 123456789
    monkeypatch.setattr(psutil, 'Process', lambda: NS(memory_info=lambda: NS(rss=expected)))
    def no_procfs(*args, **kwargs):
        pytest.fail('RSS measurement must use psutil, not read /proc directly')
    monkeypatch.setattr(Path, 'read_text', no_procfs)
    assert rss() == expected


def test_real_archive_two_days_and_resume(tmp_path,monkeypatch):
    one,initial=micro_day(intraday=True);two,_=micro_day('2026-07-16',second_day=True,intraday=True)
    days=[one,two]
    for day in days:
        day.cfg.base['solver']['mip_rel_gap']=0.
        day.cfg.mode['intraday']['mip_rel_gap']=0.
        day.cfg.mode['realtime']['mip_rel_gap']=0.
        day.bid_receipt['confirmation_source']='explicit_automated_acceptance_not_user_manual_confirmation'
    refs=[];samples=[];calls=[];metrics=[]
    original=scuc.build_model
    def build(case):
        if case.stage=='DA':
            gc.collect();samples.append(dict(date=case.cfg.base['date'],phase='before_DA',alive_prior_models=sum(x() is not None for x in refs),rss=rss()))
        m=original(case);refs.append(weakref.ref(m));return m
    monkeypatch.setattr(scuc,'build_model',build)
    run=full.run_full
    def audited_run(cfg,case,**kwargs):
        assert kwargs['retain_id_models'] is False
        calls.append(cfg.base['date']);result=run(cfg,case,**kwargs)
        assert result['rr'].id_models_retained is False
        assert result['rr'].id_rounds
        assert all('model' not in r and 'pricing_model' not in r for r in result['rr'].id_rounds)
        metrics.append(dict(date=cfg.base['date'],DA_gap=result['da'].scuc_info['gap'],ID_gaps=[r['mip']['gap'] for r in result['rr'].id_rounds],RT_gaps=[r['mip']['gap'] for r in result['rr'].rt],checks=result['checks']))
        return result
    monkeypatch.setattr(full,'run_full',audited_run)
    root=tmp_path/'series'
    result=series.run_series(days,root,initial_actual_state=initial,result_retention='archive_only',log=lambda *a:None)
    assert result['new_results']==[] and len(result['new_result_refs'])==2 and result['completed_days']==2
    assert calls==['2026-07-15','2026-07-16'] and [x['alive_prior_models'] for x in samples]==[0,0]
    gc.collect();assert all(x() is None for x in refs)
    replays=[]
    for day,ref in zip(days,result['new_result_refs']):
        directory=root/ref['directory'];commit=json.loads((directory/'day_commit.json').read_text())
        assert ref['commit_hash']==commit['commit_hash']
        assert commit['files']==series._files(directory)
        replays.append(replay(directory))
        assert retention.audit_archive(directory,json.loads((directory/'summary.json').read_text()))['valid']
        cache=json.loads((directory/'id_state_value_evidence.json').read_text())
        assert len(retention.validate_archived_state_evidence(cache))==4
        assert len(pd.read_csv(directory/'rt_system.csv'))==24
    a=json.loads((root/'day_2026-07-15/actual_checkpoint.json').read_text())
    b=json.loads((root/'day_2026-07-16/actual_initial_state.json').read_text())
    assert bridge.pack_state(two.cfg,bridge.next_day_state(a,two.cfg,net=two.net))==b
    before={p:series._hash_file(p) for p in root.glob('day_*/*') if p.is_file()}
    monkeypatch.setattr(full,'run_full',lambda *a,**k:pytest.fail('committed resume cannot solve'))
    restored=series.run_series(days,root,initial_actual_state=initial,result_retention='archive_only')
    assert restored['new_results']==[] and restored['new_result_refs']==[] and restored['completed_days']==2
    assert before=={p:series._hash_file(p) for p in before}
    (tmp_path/'archive_two_day_report.json').write_text(json.dumps(bridge.plain(dict(scope='real synthetic 2bus 24x1h two days, not default IEEE39 or causal-v3',samples=samples,metrics=metrics,replay=replays,all_models_collected=True,result={k:v for k,v in result.items() if k not in ('checkpoints','financial')},financial_summary={k:v for k,v in result['financial'].items() if k!='rows'})),ensure_ascii=False,indent=2))


def test_formal_entrypoints_select_low_memory():
    root=Path(__file__).resolve().parents[1]
    for name in ('main_full.py','run_scenario.py','audit_typical_scenarios.py','compare_pumped.py','run_causal_v3_micro.py'):
        calls=[n for n in ast.walk(ast.parse((root/'scripts'/name).read_text())) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='run_full']
        assert calls and all(any(k.arg=='retain_id_models' and isinstance(k.value,ast.Constant) and k.value.value is False for k in c.keywords) for c in calls)
    calls=[n for n in ast.walk(ast.parse((root/'src/smm/series_ui_runner.py').read_text())) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='run_series']
    assert len(calls)==1 and any(k.arg=='result_retention' and k.value.value=='archive_only' for k in calls[0].keywords)


@pytest.mark.parametrize('bad',[None,False,'compact',''])
def test_series_bad_retention_rejected_before_output(tmp_path,bad):
    with pytest.raises(ValueError,match='result_retention'):
        series.run_series([],tmp_path/'not_created',initial_actual_state=None,result_retention=bad)
    assert not (tmp_path/'not_created').exists()


class Payload:
    def __init__(self):self.data=np.ones(1024*1024,dtype=np.float64)


def test_31_day_orchestration_retention_no_solver(tmp_path,monkeypatch):
    """Explicit stubs isolate retention: NOT physical/forecast/31-day solve evidence."""
    days=[]
    for i in range(31):
        day,initial=micro_day(str(date(2026,7,1)+timedelta(days=i)),with_hydro=False)
        days.append(day)
    initial=bridge.research_initial_state(days[0].cfg,acknowledge_constant_prehistory=True)
    refs=[];samples=[];mode=['full_debug']
    def stub_run(cfg,case,**kwargs):
        if mode[0]=='archive_only':assert kwargs['retain_id_models'] is False
        else:assert 'retain_id_models' not in kwargs
        gc.collect();samples.append(dict(mode=mode[0],day=cfg.base['date'],alive_before=sum(r() is not None for r in refs),rss=rss()))
        payload=Payload();refs.append(weakref.ref(payload))
        from dataclasses import replace
        from smm.bids import effective_bids
        awarded=replace(case,bids=effective_bids(cfg,case.bids))
        return dict(da=NS(case=awarded),rr=NS(states=[bridge.state_from_case_init(cfg,case.init)]),_retention_test_payload=payload)
    monkeypatch.setattr(full,'run_full',stub_run)
    monkeypatch.setattr(full,'write_full_outputs',lambda result,directory,**kwargs:(directory/'stub.txt').write_text('retention-only synthetic result, no solver'))
    monkeypatch.setattr(bridge,'export_checkpoint',lambda r,**k:dict(checkpoint_hash=bridge.digest([r['da'].case.cfg.base['date'],k]),pending=[],initial_state_hash=bridge.digest(bridge.pack_state(r['da'].case.cfg,r['rr'].states[0]))))
    monkeypatch.setattr(bridge,'next_day_state',lambda cp,cfg,net:bridge.research_initial_state(cfg,acknowledge_constant_prehistory=True))
    monkeypatch.setattr(series,'execution_evidence',lambda r:dict(scope='synthetic retention stub'))
    monkeypatch.setattr(series,'_financial',lambda r:dict(date=r['da'].case.cfg.base['date'],rows=[]))
    monkeypatch.setattr(series,'_series_financial',lambda rows:dict(days=len(rows),scope='retention stub, no cash claim'))
    debug=series.run_series(days,tmp_path/'debug',initial_actual_state=initial,result_retention='full_debug',log=lambda *a:None)
    assert len(debug['new_results'])==31 and sum(r() is not None for r in refs)==31
    del debug;gc.collect();assert all(r() is None for r in refs)
    mode[0]='archive_only';refs.clear()
    compact=series.run_series(days,tmp_path/'compact',initial_actual_state=initial,result_retention='archive_only',log=lambda *a:None)
    gc.collect();assert compact['new_results']==[] and len(compact['new_result_refs'])==31 and all(r() is None for r in refs)
    assert all(r['alive_before']==0 for r in samples if r['mode']=='archive_only')
    assert [r['alive_before'] for r in samples if r['mode']=='full_debug']==list(range(31))
    (tmp_path/'retention_31day_stub_report.json').write_text(json.dumps(dict(scope='31-day orchestration stress with explicit solver/checkpoint/cash stubs and fixed 8MiB payload/day; NOT real 31-day optimization',samples=samples),indent=2))
