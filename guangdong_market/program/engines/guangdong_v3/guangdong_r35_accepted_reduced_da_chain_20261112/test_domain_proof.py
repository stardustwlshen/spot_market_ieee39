"""Tiny in-memory provenance fixtures, not actual accepted evidence."""
from copy import deepcopy
import pytest
import r35_pipeline as p

def fixture(monkeypatch):
 original={'variables':[{'kind':'B','lb':0.,'ub':1.,'start':0.}for _ in range(2160)]};template=deepcopy(original);template['rows']=[];template['objective']={}
 event={'request':template,'response':{'has_primal':True,'objective':100.,'bound':99.95,'native_mip_gap':.0005,'status':'optimal','native_status':'Optimal'},'event_id':'new','pins':{}}
 audit={'network_elimination_plan_sha256':'PLAN','original_v1_request_sha256':'FULL','full_original_certificate':{'max_primal_violation':0.,'max_integer_violation':0.,'objective':100.,'integers':2160}}
 monkeypatch.setattr(p,'audit_bound',lambda e,a:(audit,{}))
 def obj(h,v):return {'path':'fixture','sha256':h,'verified_content':v}
 proofs={'original_full_math_request':obj('FULL',{}),'original_R25_template':obj('R25',original),'global_native_template':obj('TEMPLATE',template),'network_elimination_plan':obj('PLAN',{'source_payload_sha256':'R25','kept_original_compact_columns':list(range(2160))}),'network_elimination_preparation':obj('PREP',{'artifact_hashes':{'template.json':'TEMPLATE','network_elimination_plan.json':'PLAN'}})}
 return event,proofs

def test_matching_domain_transform_can_be_assessed(monkeypatch):
 e,pr=fixture(monkeypatch);assert p.assess_scuc(e,'fixture',pr)['accepted']
def test_wrong_transform_source_rejected(monkeypatch):
 e,pr=fixture(monkeypatch);pr['network_elimination_plan']['verified_content']['source_payload_sha256']='different'
 with pytest.raises(ValueError,match='original global'):p.assess_scuc(e,'fixture',pr)
def test_restricted_bound_cannot_pass_via_optimal_label(monkeypatch):
 e,pr=fixture(monkeypatch);e['request']['variables'][0]['ub']=0.
 with pytest.raises(ValueError,match='bound/kind'):p.assess_scuc(e,'fixture',pr)
