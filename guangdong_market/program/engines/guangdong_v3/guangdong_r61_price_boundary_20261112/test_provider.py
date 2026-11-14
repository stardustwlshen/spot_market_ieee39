from datetime import datetime,timedelta,timezone
from copy import deepcopy
import pytest
from provider import boundaries,PricingStateBasis
from transform import transform

def fixture(k):
 start=datetime(2026,11,12,tzinfo=timezone.utc)-timedelta(minutes=5*k);cells=[]
 for j in range(24):
  a=start+timedelta(minutes=5*j);end=a+timedelta(minutes=5);date=a.date().isoformat();used=(40+j+1)if j<k else j-k+1
  cells.append(dict(start=a.isoformat(),end=end.isoformat(),batteries={'ES1':dict(charge_mw=0.,discharge_mw=12.,energy_begin_mwh=120.-j,energy_end_mwh=119.-j,daily_used_internal_mwh={date:used})}))
 dates=sorted({c['start'][:10]for c in cells});inp=dict(da_cells=cells,window=dict(start=start.isoformat(),midnight_local_energy_index=k),projection=dict(state=dict(batteries={'ES1':dict(energy_mwh=100.,daily_used_internal_mwh={dates[0]:60.})})),resources={'ES1':dict(cycle_budget={d:dict(used_internal_mwh=0.,limit_internal_mwh=400.)for d in dates})},midnight_targets={'ES1':120.-k})
 names=[f'energy[ES1,{j}]'for j in range(25)]+[f'lift[ES1,{j}]'for j in range(24)]+[f'power[ES1,{j}]'for j in range(8)];ix={n:j for j,n in enumerate(names)};vs=[dict(lb=0.,ub=200.,kind='C',start=None)for n in names];vs[0].update(lb=100.,ub=100.)
 rows=[];cn=[]
 for j in range(24):
  terms=[[ix[f'energy[ES1,{j+1}]'],1.],[ix[f'lift[ES1,{j}]'],1/12]]
  if j:terms.append([ix[f'energy[ES1,{j}]'],-1.])
  rows.append(dict(lb=0. if j else 100.,ub=0. if j else 100.,terms=terms));cn.append(f'dyn[{j}]')
 for j in range(8):rows.append(dict(lb=0.,ub=0.,terms=[[ix[f'power[ES1,{j}]'],1.]]+[[ix[f'lift[ES1,{l}]'],-1/3]for l in range(3*j,3*j+3)]));cn.append(f'mean[{j}]')
 for d in dates:
  rows.append(dict(lb=None,ub=340. if d==dates[0]else 400.,terms=[[ix[f'lift[ES1,{j}]'],1/12]for j,c in enumerate(cells)if c['start'][:10]==d]));cn.append(f'daily_throughput[ES1,{d}]')
 rows.append(dict(lb=120.-k,ub=120.-k,terms=[[ix[f'energy[ES1,{k}]'],1.]]));cn.append('midnight[ES1]')
 return inp,dict(variables=vs,rows=rows,objective=dict(terms=[],constant=0.,minimize=True)),dict(variables=names,constraints=cn)
@pytest.mark.parametrize('k',[24,21,3])
def test_D96_and_date_reset(k):
 i,q,n=fixture(k);before=deepcopy(i);s=boundaries(i);out,nn,tr=transform(q,n,i)
 assert i==before and s['ES1']['energy_mwh']==120 and s['ES1']['used_internal_mwh'][i['window']['start'][:10]]==40
 if k<24:assert s['ES1']['used_internal_mwh']['2026-11-12']==0
 assert tr['D96_target_unchanged'] and len(out['variables'])==len(q['variables'])-16
 for change in tr['changed_dated_usage_rows']:
  assert change['new_rhs']==(360 if change['date']=='2026-11-11' else 400)
 assert out['rows'][nn['constraints'].index('midnight[ES1]')]['ub']==120-k

def test_purpose_and_missing():
 i,q,n=fixture(3)
 for b in [PricingStateBasis(purpose='RT'),PricingStateBasis(formal=True)]:
  with pytest.raises(ValueError):boundaries(i,b)
 del i['da_cells'][3]['batteries']['ES1']['daily_used_internal_mwh']
 with pytest.raises(KeyError):boundaries(i)

def test_next_day_unknown_usage_rejected():
 i,q,n=fixture(3);i['resources']['ES1']['cycle_budget']['2026-11-12']['used_internal_mwh']=99
 with pytest.raises(AssertionError):boundaries(i)
