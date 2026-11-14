import pytest
from prepare import ROOT,BASE,read
from r42_model import build
@pytest.fixture(scope='module')
def data():
 from smm.gd_october_job_codec import loads
 old=ROOT/'guangdong_after_reset_r23_native_20261112/native_once_v1';p=BASE/'prepared_v1'
 return read(old/'ieee39_separate_prepared_scuc/request.json'),read(p/'full_template.json'),read(p/'full_names.json'),loads((old/'ieee39_separate_prepared_input.json').read_bytes()),read(p/'SCENARIO_DELTA.json')

def test_unchanged_unrelated_math(data):
 old,q,n,c,d=data;changed=set(d['changed_original_rows']);ids=set(d['changed_variable_indices'])
 assert all(a==b for i,(a,b)in enumerate(zip(old['rows'],q['rows']))if i not in changed)
 assert all(a==b for i,(a,b)in enumerate(zip(old['variables'],q['variables']))if i not in ids)
 assert len(q['rows'])-len(old['rows'])==1440

def test_zero_curtail_restores_original_algebra_before_agc(data):
 old,q,n,c,d=data;vi={s.replace("'",''):i for i,s in enumerate(n['variables'])};G=list(c['energy_inputs']['thermal']);sub={z['p_index']:z['available_mw']for z in d['inventory']};sub.update({z['c_index']:0. for z in d['inventory']})
 for i in d['changed_original_rows']:
  name=n['constraints'][i].replace("'",'');r=q['rows'][i];terms=dict(r['terms']);lo=r['lb'];hi=r['ub']
  constant=sum(terms.pop(j,0.)*v for j,v in sub.items())
  if lo is not None:lo-=constant
  if hi is not None:hi-=constant
  if name.startswith(('free_lower[','free_upper[')):
   tail=name.split('[')[1][:-1];g,tt=tail.split(',');t=int(tt)
   if t<288:terms[vi[f'stable[{g},{t}]']]-=10.
  elif name.startswith('reserves['):
   k=int(name.split('[')[1][:-1])-1;t=k//24;o=k%24
   if t<288:
    if o in(0,1):
     for g in G:terms[vi[f'stable[{g},{t}]']]-=(-10. if o==0 else 10.)
    elif 2<=o<22 and (o-2)%4 in(0,1):terms[vi[f'stable[{G[(o-2)//4]},{t}]']]-=10.
  terms={j:v for j,v in terms.items()if v}
  assert terms==dict(old['rows'][i]['terms'])
  assert lo==old['rows'][i]['lb'] or (lo is not None and abs(lo-old['rows'][i]['lb'])<1e-8)
  assert hi==old['rows'][i]['ub'] or (hi is not None and abs(hi-old['rows'][i]['ub'])<1e-8)

def test_penalty_and_bounds(data):
 old,q,n,c,d=data
 for z in d['inventory']:
  for j in(z['p_index'],z['c_index']):assert q['variables'][j]['lb']==0 and q['variables'][j]['ub']==z['available_mw']
 assert q['objective']['constant']==old['objective']['constant']
 obj=dict(q['objective']['terms']);assert all(obj[z['c_index']]==1000/12 for z in d['inventory'])
 assert not any(z['p_index']in obj for z in d['inventory'])

def test_no_new_vre_service_credit_and_reserve400(data):
 old,q,n,c,d=data;ri={s:i for i,s in enumerate(n['constraints'])}
 for t in range(576):
  assert q['rows'][ri[f'reserves[{24*t+23}]']]==old['rows'][ri[f'reserves[{24*t+23}]']]
  assert q['rows'][ri[f'reserves[{24*t+24}]']]==old['rows'][ri[f'reserves[{24*t+24}]']]
 assert len([s for s in n['constraints']if s.startswith('r42_AGC_stable[')])==1440
@pytest.mark.parametrize('p',[-1,float('nan'),True])
def test_invalid_penalty(p):
 with pytest.raises(ValueError):build({}, {}, {},curtailment_penalty=p,penalty_basis='explicit_research_parameter')

def test_penalty_not_official():
 with pytest.raises(ValueError):build({}, {}, {},curtailment_penalty=1000,penalty_basis='official')
