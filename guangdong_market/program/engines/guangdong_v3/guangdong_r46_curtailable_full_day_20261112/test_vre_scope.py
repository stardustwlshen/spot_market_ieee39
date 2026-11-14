from copy import deepcopy
import pytest
import pyomo.environ as p
from r46smm.gd_r46_vre import attach
from r46smm import highs_solver as hs
from r46_entry import validate_publication,digest

def fixture():
 return {'renewable':{'4':dict(availability_mw=[100.]*24,SCED_reference_mw=[50.]*24,reserve_credit_mw=0.,AGC_credit_mw=0.,bid_proxy_yuan_mwh=0.,M2_dispatch_research=1000.,M2_price_prime=1000.)}}
def build(inp,stage):
 m=p.ConcreteModel();m.zero=p.Var(bounds=(0,0));m.zero.fix(0);m.injection=p.Expression(['4'],range(24 if stage=='RT'else 8),rule=lambda m,b,j:m.zero);m.objective=p.Objective(expr=m.zero);attach(m,inp,stage)
 q,vs,cs=hs.serialize_model(m,{});return q,{v.name.replace("'",''):i for i,v in enumerate(vs)}
@pytest.mark.parametrize('changed,untouched,affected',[('M2_price_prime','RT','PRICE'),('M2_dispatch_research','PRICE','RT')])
def test_cost_isolation(changed,untouched,affected):
 a=fixture();b=deepcopy(a);b['renewable']['4'][changed]=777.
 assert build(a,untouched)[0]==build(b,untouched)[0]
 assert build(a,affected)[0]['objective']!=build(b,affected)[0]['objective']
def test_forecast_not_reference():
 a=fixture();b=deepcopy(a);b['renewable']['4']['availability_mw']=[90.]*24
 qa,na=build(a,'RT');qb,nb=build(b,'RT');assert qa['variables'][na['vre_power[4,0]']]['ub']==100.;assert qb['variables'][nb['vre_power[4,0]']]['ub']==90.
 assert a['renewable']['4']['SCED_reference_mw']==b['renewable']['4']['SCED_reference_mw']
 for x in (a,b):
  q,n=build(x,'PRICE');assert q['variables'][n['vre_quarter_power[4,0]']]['lb']==45.;assert q['variables'][n['vre_quarter_power[4,0]']]['ub']==55.00000000000001
@pytest.mark.parametrize('credit',['reserve_credit_mw','AGC_credit_mw'])
def test_vre_no_credit(credit):
 x=fixture();x['renewable']['4'][credit]=1.
 with pytest.raises(ValueError):build(x,'RT')
def test_empty_band_rejected():
 x=fixture();x['renewable']['4']['availability_mw']=[20.]*24
 with pytest.raises(ValueError,match='empty'):build(x,'PRICE')
def test_physical_release_not_financial():
 physical={'physical_cells':[{}]*576};dto={'synthetic':'fixture'};release=dict(physical_schedule_release=True,raw_PRICE_verified=True,financial_price_available=False,research_publication_allowed=False,execution_authority=False,source_DTO_sha256=digest(dto),source_physical_sha256=digest(physical),rows=[{}]*96)
 bridge=dict(DTO=dto,DTO_sha256=digest(dto),source_receipt=dict(SCUC_accepted=True,diagnostic_only=False,physical_report_sha256=digest(physical)))
 assert validate_publication(release,bridge,physical)
 release['financial_price_available']=True
 with pytest.raises(ValueError):validate_publication(release,bridge,physical)

@pytest.mark.parametrize('raw,accepted',[(300.-2.8421709430404007e-13,True),(300.-.5e-9,True),(300.-2e-9,False)])
def test_reference_roundoff(raw,accepted):
 from r46smm.gd_r46_source_contract import normalize_reference_roundoff
 if accepted:
  value,record=normalize_reference_roundoff(raw,300.,636.);assert value==300. and record['raw_mw']==raw
 else:
  with pytest.raises(ValueError):normalize_reference_roundoff(raw,300.,636.)

@pytest.mark.parametrize('stage', ['RT','PRICE'])
def test_named_injection_update_in_existing_balance(stage):
 m=p.ConcreteModel();m.zero=p.Var(bounds=(0,0));m.zero.fix(0)
 m.injection=p.Expression(['4'],range(24 if stage=='RT' else 8),rule=lambda m,b,j:m.zero)
 m.balance=p.Constraint(expr=m.injection['4',0]==0);m.objective=p.Objective(expr=m.zero)
 attach(m,fixture(),stage);q,vs,cs=hs.serialize_model(m,{})
 i=next(i for i,c in enumerate(cs) if c.name=='balance');terms={vs[j].name.replace("'",''):v for j,v in q['rows'][i]['terms']}
 assert terms=={('vre_power[4,0]' if stage=='RT' else 'vre_quarter_power[4,0]'):1.}
