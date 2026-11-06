"""Same-solve exact comparison, not two possibly degenerate MIP trajectories."""
import copy,gc,json,hashlib
from pathlib import Path
import numpy as np
import pytest
from smm import full,rolling,series,result_retention as retention,state_bridge as bridge
from smm.case import build_da_case
from multiday_fixture import micro_day


def same(a,b,path='root'):
    if isinstance(a,np.ndarray):
        assert a.dtype==b.dtype,path;np.testing.assert_array_equal(a,b,err_msg=path)
    elif isinstance(a,dict):
        assert set(a)==set(b),path
        for key in a:same(a[key],b[key],path+'.'+str(key))
    elif isinstance(a,(list,tuple)):
        assert type(a)==type(b) and len(a)==len(b),path
        for i,(x,y) in enumerate(zip(a,b)):same(x,y,path+f'[{i}]')
    else:assert a==b,path


def test_same_solve_full_and_compact_archive_exact(tmp_path):
    day,initial=micro_day(intraday=True)
    day.cfg.mode['intraday']['interval_h']=2.
    day.cfg.mode['auxiliary_pricing']['enabled']=True
    case=build_da_case(day.cfg,day.net,day.profiles,day.bid_data,init=bridge.state_to_case_init(initial),require_complete_bids=True,require_actual_state=True)
    result=full.run_full(day.cfg,case,periods=4,log=lambda *a:None)
    series._freeze(day,tmp_path/'frozen_inputs')
    original=tmp_path/'debug';compact=tmp_path/'compact'
    full.write_full_outputs(result,original,make_plots=False)
    rr=copy.copy(result['rr']);rr.id_rounds=[];rr.id_models_retained=False
    before=[]
    for record in result['rr'].id_rounds:
        numeric={k:v for k,v in record.items() if k not in ('model','pricing_model')}
        r=dict(record);retention.capture_state_evidence(r)
        out=retention.detach_id_models(r)
        same(numeric,{k:v for k,v in out.items() if k not in ('models_retained','state_value_evidence')})
        rr.id_rounds.append(out)
        before.append(rolling.intraday_reference(rolling._da_plan_arrays(result['da']),rolling._da_plan_arrays(result['da']),record,day.cfg.T,cfg=day.cfg))
        same(before[-1],rolling.intraday_reference(rolling._da_plan_arrays(result['da']),rolling._da_plan_arrays(result['da']),out,day.cfg.T,cfg=day.cfg))
    compact_result=dict(result,rr=rr)
    assert full.checks(result['da'],rr,result['rt'])==result['checks']
    full.write_full_outputs(compact_result,compact,make_plots=False)
    files={p.relative_to(original) for p in original.rglob('*') if p.is_file()}
    compared=[]
    for name in sorted(files):
        a=(original/name).read_bytes();b=(compact/name).read_bytes()
        metadata_only=False
        if name.name=='summary.json':
            parsed=json.loads(b);assert parsed.pop('id_result_retention')==retention.ARCHIVE_CONTRACT
            assert parsed.pop('id_state_value_parameters')==retention.conversion_parameters(day.cfg)
            assert json.loads(a)==parsed;metadata_only=True
        elif name.name=='output_manifest.json':
            old,new=json.loads(a),json.loads(b)
            added=[x for x in new['artifacts'] if x['file']=='id_state_value_evidence.json']
            assert len(added)==1 and added[0]['present']
            new['artifacts']=[x for x in new['artifacts'] if x['file']!='id_state_value_evidence.json']
            for item in new['artifacts']:
                if item['file']=='summary.json':
                    original_summary=next(x for x in old['artifacts'] if x['file']=='summary.json')
                    assert set(item)==set(original_summary)
                    item['bytes']=original_summary['bytes'];item['sha256']=original_summary['sha256']
            assert old==new;metadata_only=True
        elif name.name=='结果文件索引.md':
            filtered='\n'.join(x for x in b.decode().split('\n') if 'id_state_value_evidence.json' not in x)
            assert a.decode()==filtered;metadata_only=True
        else:assert a==b,str(name)
        compared.append(dict(path=str(name),sha256=hashlib.sha256(a).hexdigest(),metadata_only_difference=metadata_only))
    assert {p.relative_to(compact) for p in compact.rglob('*') if p.is_file()} == files|{Path('id_state_value_evidence.json')}
    evidence=json.loads((compact/'id_state_value_evidence.json').read_text())
    restored=retention.validate_archived_state_evidence(evidence)
    assert len(restored)==len(rr.id_rounds)==2
    assert retention.audit_archive(compact,json.loads((compact/'summary.json').read_text()))['valid']
    for a,b in zip(restored,rr.id_rounds):same(a,b['state_value_evidence'])
    corrupt=copy.deepcopy(evidence);corrupt['rounds'][0]['evidence']['values']['E']['ES1']['dual_raw'][0]+=1
    with pytest.raises(ValueError):retention.validate_archived_state_evidence(corrupt)
    missing=copy.deepcopy(rr);missing.id_rounds[0].pop('state_value_evidence')
    with pytest.raises(ValueError):full.checks(result['da'],missing,result['rt'])
    report=dict(scope='same solved 2bus 4 executed 1h periods, 2 ID, aux enabled; not full day',
        compared_files=compared,new_audit_file='id_state_value_evidence.json',existing_numeric_evidence_exact=True,metadata_only_files=['summary.json','output_manifest.json','结果文件索引.md'],
        da_gap=result['da'].scuc_info['gap'],id_gaps=[x['mip']['gap'] for x in rr.id_rounds],rt_gaps=[x['mip']['gap'] for x in rr.rt])
    (tmp_path/'same_solve_report.json').write_text(json.dumps(report,indent=2))
