"""Exercise saved Sichuan RT rows; never run a scheduler or create billing entries."""
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal as D
import json
from pathlib import Path
import sys

import pytest

# Exercise the same import layout as the Streamlit entry point.
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))

from sichuan_v5_demo import research_values
from sichuan_v5_energy import Case, Resource
from sichuan_v5_reconciliation import reconcile_energy


def inputs():
    case=Case((1,2),(),(
        Resource('C1',1,'public_coal','thermal',100.,100.),
        Resource('ES1',2,'independent_storage','storage',10.,0.),
        Resource('G0',2,'gas','fixed',10.,0.),
    ),(),())
    rt=dict(quarter=0,power={'C1':20.,'ES1':0.,'G0':4.},charge={'ES1':4.},
            demand={1:10.,2:10.},shed={1:0.,2:0.},spill={1:0.,2:0.},
            reliability_reserve_short_mw=0.,raw_nodal_prices={1:890.,2:-10.})
    return case,rt,research_values(case,rt)


def test_report_reconciles_rt_prices_quantities_and_da_non_money():
    case,rt,values=inputs()
    ref=[{**deepcopy(rt),'quarter':i,'raw_nodal_prices':{1:3.,2:3.},'power':{'C1':8.,'ES1':0.,'G0':6.}}
         for i in range(96)]
    saved=json.loads(json.dumps(asdict(case)))
    report=reconcile_energy(saved,[json.loads(json.dumps(rt))],[json.loads(json.dumps(values))],da_reference=ref)
    assert report['formal_settlement'] is False and report['da_reference_only']
    assert report['summary']['executed_quarters']==1 and report['summary']['full_day_coverage'] is False
    assert len(report['quarters'])==1 and report['quarters'][0]['rt_price_source']=='rt_initial'
    r={x['resource_id']:x for x in report['transactions']}
    assert set(r)=={'C1','ES1','负荷-1','负荷-2'} # Fixed non-market injection excluded.
    assert D(r['C1']['grid_delivered_mwh'])==5 and D(r['C1']['da_delivered_mwh'])==2
    assert D(r['C1']['raw_rt_nodal_price'])==890 and D(r['C1']['effective_rt_nodal_price'])==800
    assert D(r['ES1']['effective_rt_nodal_price'])==0 # price floor isn't missing
    assert D(r['ES1']['effective_rt_uniform_price'])==800
    assert D(r['C1']['net_amount'])==4000 and D(r['ES1']['net_amount'])==-800
    assert not any(k.startswith('da_') and 'amount' in k for k in r['C1'])
    assert D(report['summary']['net_amount'])==-800
    assert report['summary']['incomplete_quarters']==0


def test_missing_price_never_silently_zero_and_no_da_fallback():
    case,rt,_=inputs()
    rt['shed'][2]=.1 # forces reliability suppression even while RT raw values exist
    values=research_values(case,rt)
    report=reconcile_energy(asdict(case),[rt],[values])
    assert report['quarters'][0]['valuation_status']=='incomplete'
    assert report['quarters'][0]['rt_price_source']=='withheld'
    assert report['summary']['net_amount'] is None and report['summary']['unpriced_rows']==4
    assert all(t['net_amount'] is None for t in report['transactions'])
    assert all(t['raw_rt_nodal_price'] is not None for t in report['transactions'])
    assert all(t['effective_rt_nodal_price'] is None for t in report['transactions'])
    assert D(report['summary']['grid_consumed_mwh'])==D('5.975')


def test_explicit_review_does_not_change_physical_rt_and_nonsettlement_da():
    case,rt,_=inputs()
    replacement={1:300.,2:100.}
    reviewed_values=research_values(case,{**rt,'raw_nodal_prices':replacement})
    review=dict(quarter=0,source='da_proxy_rt',evidence_id='audited-da-q0',incident_id='incident',
                evidence_status='declared_synthetic_research',original_raw_nodal_prices=rt['raw_nodal_prices'],
                selected_nodal_prices=replacement)
    result=reconcile_energy(asdict(case),[rt],[reviewed_values],price_reviews=[review])
    assert result['quarters'][0]['rt_price_source']=='da_proxy_rt'
    a=result['transactions'][0]
    assert D(a['raw_rt_nodal_price'])==890 and D(a['effective_raw_nodal_price'])==300
    assert D(a['grid_delivered_mwh'])==5 and D(a['net_amount'])==1500
    assert a['da_delivered_mwh'] is None and a['price_evidence_id']=='audited-da-q0'
    assert 'formal_settlement' in result and not result['formal_settlement']
    with pytest.raises(ValueError,match='cleared nodal price'):
        reconcile_energy(asdict(case),[rt],[reviewed_values])
    with pytest.raises(ValueError,match='does not match executed raw'):
        reconcile_energy(asdict(case),[rt],[reviewed_values],
                         price_reviews=[{**review,'original_raw_nodal_prices':replacement}])
    with pytest.raises(ValueError,match='outside executed'):
        reconcile_energy(asdict(case),[rt],[reviewed_values],price_reviews=[{**review,'quarter':1}])


@pytest.mark.parametrize('bad_count',['repeat','future','money','power','money_quantity'])
def test_fails_closed_on_archive_inconsistencies(bad_count):
    case,rt,values=inputs()
    executed=[rt]
    money=[values]
    if bad_count=='repeat':
        executed.append(deepcopy(rt));money.append(deepcopy(values))
    elif bad_count=='future':
        money[0]['quarter']=1
    elif bad_count=='money':
        money[0]['rows'][0]['net_amount']='1'
    elif bad_count=='power':
        rt['power']['C1']=float('nan')
    else:
        money[0]['rows'][0]['grid_delivered_mwh']='4.99'
    with pytest.raises(ValueError):
        reconcile_energy(asdict(case),executed,money)


def test_withheld_verified_review_uses_no_original_prices_for_money():
    from sichuan_v5_demo import _result
    from sichuan_v5_settlement import SettlementInput

    case,rt,original=inputs()
    withheld={**original,'valuation_status':'withheld_price_review',
              'cleared_nodal_price':dict.fromkeys(case.buses),'uniform_price':None,
              'rows':[_result(SettlementInput(0,row['resource_id'],row['role'],D(row['grid_delivered_mwh']),
                                              D(row['grid_consumed_mwh']))) for row in original['rows']]}
    review=dict(quarter=0,source='withheld',evidence_id=None,incident_id='review-42',
                evidence_status='declared_synthetic_research',original_raw_nodal_prices=rt['raw_nodal_prices'],
                selected_nodal_prices=None)
    report=reconcile_energy(asdict(case),[rt],[withheld],price_reviews=[review])
    assert report['summary']['net_amount'] is None
    assert report['quarters'][0]['archived_valuation_status']=='withheld_price_review'
    assert report['quarters'][0]['price_suppressed_due_reliability'] is False
    assert report['transactions'][0]['incident_id']=='review-42'
    assert report['transactions'][0]['effective_raw_nodal_price'] is None
