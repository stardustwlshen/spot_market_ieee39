from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import sys

import pytest

# Exercise the same import layout as the Streamlit entry point.
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))

from market_rule_profile import SICHUAN_V5
from sichuan_v5_price_review import BoundRerun, Incident, ReviewRequest, Vector, parse_review_request
from sichuan_v5_workflow import digest, json_bytes, load, review


def frame(quarter=0, *, generation=(80,20), prices=(200,400), slack=0):
    return dict(quarter=quarter, power={'G1':generation[0],'G2':generation[1],'ES':0},
                charge={'ES':10}, demand={1:90,2:0}, shed={1:slack,2:0}, spill={1:0,2:0},
                reliability_reserve_short_mw=0, raw_nodal_prices=dict(zip((1,2),prices)))


def archive(tmp_path, *, slack=0, da_slack=0):
    root=tmp_path/SICHUAN_V5.results_folder
    name='scv5_20261122T000000Z_012345abcdef'
    path=root/name
    path.mkdir(parents=True)
    rules=SICHUAN_V5.identity()
    resources=[dict(id=r,bus=b,role=role,kind=kind) for r,b,role,kind in (
        ('G1',1,'public_coal','thermal'),('G2',2,'provincial_hydro','hydro'),
        ('ES',2,'independent_storage','storage'))]
    contents={'INPUT.json':dict(rules=rules,requested_periods=2,case=dict(buses=[1,2],resources=resources)),
              'RT_EXECUTED.json':[frame(slack=slack),frame(1)],
              'DA_PRECLEAR.json':[frame(i,generation=(20,80),prices=(100,300),slack=da_slack) for i in range(96)],
              'RT_RESEARCH_VALUES.json':[dict(quarter=i,original=True) for i in range(2)]}
    hashes={}
    for key,record in contents.items():
        data=json_bytes(record)
        (path/key).write_bytes(data)
        hashes[key]=digest(data)
    (path/'RECEIPT.json').write_bytes(json_bytes(dict(
        schema='ieee39.sichuan_v5.receipt.v1',quarters=2,status='partial_day_research',rules=rules,
        formal_settlement=False,price_fallback_applied=False,data_files=hashes)))
    incident=Incident('incorrect_boundary','synthetic-incident','synthetic operator rerun blocked')
    return root,name,ReviewRequest(name,hashes['RT_EXECUTED.json'],0,incident)


def test_da_reference_preserves_executed_quantities_and_immutable_archive(tmp_path):
    root,name,request=archive(tmp_path)
    initial=load(root,name)
    assert initial['price_reviews']==[] and initial['values']==initial['reviewed_values']
    originals={f.name:f.read_bytes() for f in (root/name).iterdir()}
    updated=review(root,name,request)
    audit,=updated['price_reviews']
    row=updated['reviewed_values'][0]
    assert audit['source']=='da_proxy_rt' and audit['evidence_id']==f'{name}:DA_PRECLEAR.json:0'
    assert audit['original_raw_nodal_prices']=={'1':200,'2':400}
    assert audit['selected_nodal_prices']=={'1':'100.00','2':'300.00'}
    assert Decimal(row['uniform_price'])==140
    assert Decimal(next(x for x in row['rows'] if x['resource_id']=='ES')['net_amount'])==-350
    assert updated['reviewed_values'][1]==initial['values'][1]
    assert updated['rt']==initial['rt'] and updated['values']==initial['values']
    assert load(root,name)==updated
    assert all((root/name/key).read_bytes()==v for key,v in originals.items())
    with pytest.raises(ValueError,match='不可覆盖'):
        review(root,name,request)


def test_authorized_recomputation_precedes_proxy_and_is_run_bound(tmp_path):
    root,name,request=archive(tmp_path)
    rerun=BoundRerun(name,request.rt_sha256,Vector('rt_recomputed',0,{1:Decimal(200),2:Decimal(300)},'r2'))
    with pytest.raises(ValueError,match='摘要不匹配'):
        review(root,name,replace(request,rerun=replace(rerun,run_id=name+'different')))
    with pytest.raises(ValueError,match='摘要不匹配'):
        review(root,name,replace(request,rt_sha256='wrong'))
    with pytest.raises(ValueError,match='阶段或时段'):
        review(root,name,replace(request,rerun=replace(rerun,vector=replace(rerun.vector,quarter=1))))
    with pytest.raises(ValueError,match='显式价格异常'):
        review(root,name,replace(request,incident=None))
    assert load(root,name)['price_reviews']==[]
    data=review(root,name,replace(request,rerun=rerun))
    assert data['price_reviews'][0]['source']=='rt_recomputed'
    assert Decimal(data['reviewed_values'][0]['uniform_price'])==220


def test_no_unverified_dispatch_or_unavailable_price_cash(tmp_path):
    root,name,request=archive(tmp_path/'bad',slack=1)
    with pytest.raises(ValueError,match='物理结果不可核验'):
        review(root,name,request)
    assert load(root,name)['price_reviews']==[]
    root,name,request=archive(tmp_path/'dangling',da_slack=1)
    data=review(root,name,request)
    assert data['price_reviews'][0]['source']=='withheld'
    row=data['reviewed_values'][0]
    assert row['uniform_price'] is None and row['valuation_status']=='withheld_price_review'
    assert all(x['net_amount'] is None for x in row['rows'])
    root,name,request=archive(tmp_path/'no-blocker')
    data=review(root,name,replace(request,incident=replace(request.incident,documented_rerun_blocker=None)))
    assert data['price_reviews'][0]['source']=='withheld'


def test_bad_sidecar_rejected_on_reopen(tmp_path):
    root,name,request=archive(tmp_path)
    updated=review(root,name,request)
    path=root/name/'RT_PRICE_REVIEW_Q00.json'
    assert parse_review_request(updated['price_reviews'][0]['request'])==request
    record=json.loads(path.read_bytes())
    record['values']['uniform_price']='260.00'
    path.write_bytes(json_bytes(record))
    with pytest.raises(ValueError,match='试算结果不一致'):
        load(root,name)


def test_review_json_requires_explicit_full_binding_and_exact_decimal_prices(tmp_path):
    root,name,request=archive(tmp_path)
    with pytest.raises(ValueError,match='格式有误'):
        parse_review_request({"quarter":0,"incident":{"reason":"program_fault","record_id":"x"}})
    rerun=BoundRerun(name,request.rt_sha256,Vector('rt_recomputed',0,{1:Decimal('200.01'),2:Decimal('300.02')},'r2'))
    data=review(root,name,replace(request,rerun=rerun))
    wire=data['price_reviews'][0]['request']
    assert parse_review_request(wire).rerun==rerun
    wire['rerun']['vector']['nodal_yuan_mwh']={'01':'200.01','2':'300.02'}
    with pytest.raises(ValueError,match='格式有误'):
        parse_review_request(wire)


def test_real_ieee39_execution_can_reopen_with_separate_synthetic_review(tmp_path):
    from sichuan_v5_workflow import run

    root=tmp_path/SICHUAN_V5.results_folder
    name,original=run(root,month=7,periods=2,equal_offer_priority=True)
    incident=Incident('program_fault','declared-synthetic-fixture','rerun unavailable in fixture')
    tested=review(root,name,ReviewRequest(name,original['receipt']['data_files']['RT_EXECUTED.json'],0,incident))
    assert tested['receipt']['equal_offer_priority'] is True
    assert tested['price_reviews'][0]['source']=='da_proxy_rt'
    assert tested['price_reviews'][0]['evidence_status']=='declared_synthetic_research'
    assert tested['reviewed_values'][0]['uniform_price'] is not None
    assert original['rt']==tested['rt'] and original['values']==tested['values']
    assert tested==load(root,name)
