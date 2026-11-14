from copy import deepcopy
import pytest
import r27_agc as a

def test_pure_template_and_analytic_witness():
    c=a.synthetic_case();q,n,meta=a.template(c);x={'q[C1]':30.,'q[C2]':10.,'q[C3]':0.,'accident[C1]':170.,'accident[C2]':190.,'accident[C3]':200.};v=[x[z]for z in n['variables']]
    for i,z in enumerate(q['variables']):assert(z['lb']is None or v[i]>=z['lb'])and(z['ub']is None or v[i]<=z['ub'])
    for r in q['rows']:
        y=sum(v[i]*b for i,b in r['terms']);assert(r['lb']is None or y>=r['lb']-1e-8)and(r['ub']is None or y<=r['ub']+1e-8)
    assert sum(v[i]*b for i,b in q['objective']['terms'])+q['objective']['constant']==200.
    assert meta['sorting_price']['C1']==4. and meta['sorting_price']['C2']==8.

def test_sced_interface_deducts_once_preserves400():
    c=a.synthetic_case();orig=deepcopy(c);r=a.adapt_awards(c,{'C1':30.,'C2':10.,'C3':0.},new_agc_result_id='synthetic_test',stage='day_ahead_prearrangement')
    assert r['adjusted_envelope']['C1']['lower_mw']==130 and r['adjusted_envelope']['C1']['upper_mw']==470
    assert r['positive_remaining_mw']==r['accident_remaining_mw']==560 and c==orig

@pytest.mark.parametrize('mutation',[
 lambda c:c.update(mode='official'),lambda c:c.update(positive_reserve_mw=300),
 lambda c:c['units']['C1'].update(kind='battery'),lambda c:c['units']['C1'].update(stable_online=False),
 lambda c:c['units']['C1'].update(qualification='unknown'),lambda c:c['units']['C1'].update(basis='after_AGC'),
 lambda c:c.update(available_at='2026-11-11T01:00:00+08:00')])
def test_false_qualification_scope_or_double_deduction(mutation):
    c=a.synthetic_case();mutation(c)
    with pytest.raises(ValueError):a.build(c)

def test_shortage_not_relaxed_or_paid():
    c=a.synthetic_case();c['units']['C3']['ramp_10min_mw']=10
    with pytest.raises(ValueError,match='400'):a.adapt_awards(c,{'C1':30.,'C2':10.,'C3':0.},new_agc_result_id='x',stage='day_ahead_prearrangement')

def test_mileage_not_award_no_time_or_Fm_factor():
    z=a.service_gross_statement(measured_mileage_mw=120.,uniform_price_yuan_mw=8.,settlement_performance=.9,designated_test=False,adjustments=None,source='synthetic meter/performance')
    assert z['gross_formula_yuan']==864. and z['reconciled_research_yuan']is None and z['payable_yuan']is None
    z=a.service_gross_statement(measured_mileage_mw=120.,uniform_price_yuan_mw=8.,settlement_performance=.9,designated_test=True,adjustments=[],source='synthetic test')
    assert z['gross_formula_yuan']==0
