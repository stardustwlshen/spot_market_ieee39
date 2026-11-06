"""Reserve arithmetic can pass while strict zero-shortage acceptance fails."""
import copy
import hashlib
import json
import os
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from test_validate_runs_evidence import validator, archive


def setup():
    def w(t,n):
        return dict(down_enabled=True,system=[dict(period=t+k,relative_period=k,R_up_short=0.,R_dn_short=0.) for k in range(n)])
    ev=dict(da=w(0,3),rt_windows=[w(t,3-t) for t in range(3)])
    rs=pd.DataFrame([dict(period=t,R_up_short=0.,R_dn_short=0.,reserve_up_short_max_win=0.,reserve_dn_short_max_win=0.) for t in range(3)])
    slack=dict(reserve_up_short_max=0.,reserve_dn_short_max=0.)
    mode=dict(reserve=dict(down_enabled=True),realtime=dict(lookahead_h=.75))
    return ev,rs,slack,mode


@pytest.mark.parametrize('direction',['up','dn'])
@pytest.mark.parametrize('stage',['da','actual','future'])
def test_shortage_is_not_zero_even_with_correct_arithmetic(direction,stage):
    ev,rs,slack,mode=setup()
    field=f'R_{direction}_short'
    if stage=='da':
        ev['da']['system'][1][field]=4.;slack[f'reserve_{direction}_short_max']=4.
    else:
        ev['rt_windows'][0]['system'][0 if stage=='actual' else 1][field]=4.
        rs.loc[0,f'reserve_{direction}_short_max_win']=4.
        if stage=='actual':rs.loc[0,field]=4.
    r=validator._reserve_shortage_audit(ev,rs,slack,mode,3,.25)
    label='上' if direction=='up' else '下'
    assert not r['零备用缺额']
    assert r[f'实际{label}备用缺额MWh']==(1. if stage=='actual' else 0.)
    assert r[f'日前{label}备用缺额MWh']==(1. if stage=='da' else 0.)


@pytest.mark.parametrize('damage',['missing','nan','negative','duplicate','csv_missing','summary_missing','flag'])
@pytest.mark.parametrize('direction',['up','dn'])
def test_bad_shortage_evidence_fails_closed(damage,direction):
    ev,rs,slack,mode=setup();row=ev['rt_windows'][0]['system'][0];field=f'R_{direction}_short'
    if damage=='missing':del row[field]
    elif damage=='nan':row[field]=float('nan')
    elif damage=='negative':row[field]=-1.
    elif damage=='duplicate':ev['rt_windows'][0]['system'][1]=copy.deepcopy(row)
    elif damage=='csv_missing':rs=rs.drop(columns=field)
    elif damage=='summary_missing':del slack[f'reserve_{direction}_short_max']
    else:ev['rt_windows'][0]['down_enabled']=False
    with pytest.raises(ValueError):validator._reserve_shortage_audit(ev,rs,slack,mode,3,.25)


def test_default_down_disabled_requires_explicit_zero_evidence():
    ev,rs,slack,mode=setup();mode.pop('reserve')
    for w in [ev['da'],*ev['rt_windows']]:w['down_enabled']=False
    assert validator._reserve_shortage_audit(ev,rs,slack,mode,3,.25)['零备用缺额']
    del ev['da']['system'][0]['R_dn_short']
    with pytest.raises(ValueError):validator._reserve_shortage_audit(ev,rs,slack,mode,3,.25)


def test_integration_positive_actual_shortage_blocks_acceptance(archive):
    p=archive/'reserve_window_evidence.json';e=json.loads(p.read_text())
    e['rt_windows'][0]['system'][0]['R_up_short']=4.;p.write_text(json.dumps(e))
    p=archive/'rt_system.csv';r=pd.read_csv(p);r.loc[0,['R_up_short','reserve_up_short_max_win']]=4.;r.to_csv(p,index=False)
    result=validator.checks(archive)
    assert result['备用复核通过'] and result['证据完整'] and result['流程未中断']
    assert not result['零备用缺额'] and not result['非计划松弛为0'] and not result['验收通过']


def test_real_dry_archive_read_only():
    p=Path(os.environ.get('IEEE39_DRY_ARCHIVE','/workspace/scratch/42c5fe32dad0/strict_handoff_audit_20261105/scenario_evidence/dry_id_on'))
    if not p.is_dir():pytest.skip('Set IEEE39_DRY_ARCHIVE to the archived dry_id_on evidence')
    def hashes():return {str(x.relative_to(p)):hashlib.sha256(x.read_bytes()).hexdigest() for x in p.rglob('*') if x.is_file()}
    before=hashes();r=validator.checks(p)
    assert r['流程未中断'] and r['备用复核通过'] and r['证据完整'],r['说明']
    assert r['终态政策合规'] and r['日内失败回退合规'] and r['日内失败轮数']==2
    assert r['实际上备用缺额最大MW']==pytest.approx(11.75336552)
    assert r['实际上备用缺额MWh']==pytest.approx(2.93834138)
    assert not r['零备用缺额'] and not r['非计划松弛为0'] and not r['验收通过']
    assert hashes()==before
