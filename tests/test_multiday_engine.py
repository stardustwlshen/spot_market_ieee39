from copy import deepcopy
import json,shutil,time
import numpy as np
import pandas as pd
import pytest
from smm import state_bridge as bridge
from smm import series
from multiday_fixture import micro_day
from multiday_replay import replay

@pytest.fixture(scope='module')
def completed(tmp_path_factory):
    root=tmp_path_factory.mktemp('real_two_day_market');one,state=micro_day(intraday=True);two,_=micro_day('2026-07-16',second_day=True,intraday=True)
    start=time.perf_counter();result=series.run_series([one,two],root,initial_actual_state=state,log=lambda *x:None)
    report=dict(scope='two_bus_synthetic_24x1h_two_full_days_not_IEEE39',elapsed_s=time.perf_counter()-start,days=[])
    for r in result['new_results']:
        report['days'].append(dict(date=r['da'].case.cfg.base['date'],rt_count=len(r['rr'].rt),id_count=len(r['rr'].id_rounds),da_gap=r['da'].scuc_info['gap'],rt_gap_max=max(x['mip']['gap'] for x in r['rr'].rt),checks={k:v for k,v in r['checks'].items() if isinstance(v,(str,bool,int,float))},initial=bridge.pack_state(r['da'].case.cfg,r['rr'].states[0]),terminal=bridge.pack_state(r['da'].case.cfg,r['rr'].states[-1]),replay=replay(root/('day_'+str(r['da'].case.cfg.base['date'])))))
    (root/'test_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print('TWO_DAY_EVIDENCE',root)
    return root,one,two,state,result


def test_actual_two_day_full_state_and_pending(completed):
    root,one,two,state,result=completed;a,b=result['new_results'];end=a['rr'].states[-1];start=b['rr'].states[0]
    assert result['status']=='completed' and result['completed_days']==2
    for name in ('thermal','storage','hydro','hydro_p','hydro_online','pumped','hydro_commitment','storage_dispatch','pumped_dispatch'):assert getattr(end,name)==getattr(start,name),name
    assert start.hydro_used=={'H1':0.,'H2':0.} and start.pumped_used=={'PS1':{'gen':0.,'pump':0.}}
    assert end.hydro_used['H1']>0 and start.pumped['PS1']['V']!=one.cfg.units['pumped'][0]['vup0']
    assert start.hydro_pre_history['H1']['values']==end.hydro_hist['H1'][-3:].tolist()
    assert start.pumped_pre_history['PS1']['values']['xg']==end.pumped_hist['PS1']['xg'][-3:].tolist()
    assert result['checkpoints'][0]['pending']
    for r in (a,b):
        for key in ('physical_feasible_checked_constraints','hydro_window_constraints_satisfied','hydro_identified_commitment_execution_valid','pumped_cross_day_history_and_start_budget_valid'):assert r['checks'][key]
        assert len(r['rr'].id_rounds)==4 and r['settled'] is True
    assert not result['financial']['all_costs_available'] and result['financial']['cash_operating_cost_yuan'] is None


def test_day_boundary_resume_does_not_resolve_or_change_completed_outputs(completed,monkeypatch):
    root,one,two,state,result=completed;before={p:series._hash_file(p) for p in root.glob('day_*/*') if p.is_file()}
    import smm.full
    monkeypatch.setattr(smm.full,'run_full',lambda *a,**k:(_ for _ in ()).throw(AssertionError('must not solve completed day')))
    restored=series.run_series([one,two],root,initial_actual_state=state,log=lambda *x:None)
    assert restored['new_results']==[] and restored['completed_days']==2
    assert all(series._hash_file(p)==h for p,h in before.items())


def test_missing_bids_blocks_all_before_da_and_prefix_stops(completed,tmp_path,monkeypatch):
    _,one,two,state,_=completed;two=deepcopy(two);two.bid_receipt['confirmed']=False
    import smm.full
    monkeypatch.setattr(smm.full,'run_full',lambda *a,**k:(_ for _ in ()).throw(AssertionError('no DA admission')))
    with pytest.raises(series.AwaitingBids):series.run_series([one,two],tmp_path/'all',initial_actual_state=state)
    assert not (tmp_path/'all').exists()
    one=deepcopy(one);one.bid_data=None;out=series.run_series([one,two],tmp_path/'prefix',initial_actual_state=state,mode='ready_prefix')
    assert out['status']=='awaiting_bids' and out['completed_days']==0


def test_boundary_hash_date_missing_state_and_parameter_failures(completed):
    _,one,two,state,result=completed;cp=result['checkpoints'][0];bad=deepcopy(cp);bad['state']['state']['storage']['ES1']=0
    with pytest.raises(ValueError,match='hash'):bridge.next_day_state(bad,two.cfg,net=two.net)
    cfg=deepcopy(two.cfg);cfg.base['date']='2026-07-17'
    with pytest.raises(ValueError,match='adjacent'):bridge.next_day_state(cp,cfg,net=two.net)
    cfg=deepcopy(two.cfg);cfg.units['thermal'][0]['ramp']+=1
    with pytest.raises(ValueError,match='identity'):bridge.next_day_state(cp,cfg,net=two.net)
    bad=deepcopy(cp);bad['state']['state'].pop('hydro_hist');bad['checkpoint_hash']=bridge.digest({k:v for k,v in bad.items() if k!='checkpoint_hash'})
    with pytest.raises((ValueError,KeyError,TypeError)):bridge.next_day_state(bad,two.cfg,net=two.net)


def test_changed_day_two_bid_changes_day_two_not_day_one(completed,tmp_path):
    root,one,two,state,result=completed;dest=tmp_path/'reprice';dest.mkdir();shutil.copy2(root/'series_identity.json',dest/'series_identity.json');shutil.copytree(root/'day_2026-07-15',dest/'day_2026-07-15');before=series._files(dest/'day_2026-07-15')
    revised,_=micro_day('2026-07-16',second_day=True,reverse_price=True,intraday=True);revised.bid_data['version']=3;revised.bid_receipt.update(version=3,bid_hash=series.bid_hash(revised.bid_data))
    out=series.run_series([one,revised],dest,initial_actual_state=state,log=lambda *x:None)
    assert len(out['new_results'])==1 and series._files(dest/'day_2026-07-15')==before
    assert out['checkpoints'][0]['checkpoint_hash']==result['checkpoints'][0]['checkpoint_hash']
    r=out['new_results'][0];old=result['new_results'][1]
    assert r['da'].model._md.thermal['C1']['c']==[150.] and old['da'].model._md.thermal['C1']['c']==[10.]
    assert not np.allclose(r['rt']['units'].P_mw,old['rt']['units'].P_mw);replay(dest/'day_2026-07-16')


def test_day_boundary_cancel_then_ready_prefix_resume(completed,tmp_path,monkeypatch):
    root,one,two,state,_=completed;dest=tmp_path/'prefix';dest.mkdir();shutil.copy2(root/'series_identity.json',dest/'series_identity.json');shutil.copytree(root/'day_2026-07-15',dest/'day_2026-07-15')
    waiting=deepcopy(two);waiting.bid_receipt['confirmed']=False;out=series.run_series([one,waiting],dest,initial_actual_state=state,mode='ready_prefix')
    assert out['status']=='awaiting_bids' and out['completed_days']==1
    import smm.full
    monkeypatch.setattr(smm.full,'run_full',lambda *a,**k:(_ for _ in ()).throw(AssertionError('cancel before day')))
    out=series.run_series([one,two],dest,initial_actual_state=state,should_cancel=lambda:True);assert out['status']=='cancelled_at_day_boundary' and out['completed_days']==1
    (dest/'day_2026-07-16').mkdir()
    with pytest.raises(series.UncommittedDayError):series.run_series([one,two],dest,initial_actual_state=state)


def test_N1_equivalent_to_same_explicit_single_day(completed,tmp_path):
    _,one,two,state,result=completed;single=series.run_series([one],tmp_path/'one',initial_actual_state=state,log=lambda *x:None)['new_results'][0]
    from smm.case import build_da_case
    from smm.full import run_full
    case=build_da_case(one.cfg,one.net,one.profiles,one.bid_data,init=bridge.state_to_case_init(state),require_complete_bids=True);direct=run_full(one.cfg,case,log=lambda *x:None)
    assert single['da'].model._md.thermal==direct['da'].model._md.thermal
    for key in ('units','storage','pumped','hydro','prices'):pd.testing.assert_frame_equal(single['rt'][key],direct['rt'][key],check_exact=False,atol=1e-7,rtol=1e-7)
    pd.testing.assert_frame_equal(single['settle']['detail'],direct['settle']['detail'],check_exact=False,atol=1e-7,rtol=1e-7)
