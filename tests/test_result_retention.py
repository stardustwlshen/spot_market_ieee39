import copy
from types import SimpleNamespace
import numpy as np
import pytest
from smm import result_retention as retention,rolling,scuc
from smm.hydro_daily_energy import id_state_values
from smm.state_bridge import digest
from test_handoff_integrity import context,id_round


def completed_probe(context):
    cfg,plan=context
    r,values=id_round(plan,4)
    r['snap']['surplus']=np.zeros(cfg.T-4)
    r['model']=SimpleNamespace(dual={},_md=SimpleNamespace(pumped={u['id']:u for u in cfg.units['pumped']},hydro={u['id']:u for u in cfg.units['hydro']}));r['pricing_model']=object()
    for kind,units in values.items():
        for unit,row in units.items():
            factor=1. if kind=='E' else 1./r['model']._md.pumped[unit]['e_gen'] if kind=='Vu' else .36*r['model']._md.hydro[unit]['water_rate']
            row['value_native']=np.r_[-row['dual_raw'][1:],0.]
            row['value_yuan_per_mwh']=row['value_native']*factor
            row['native_unit']='元/MWh' if kind=='E' else '元/万m³'
            row['terminal_target_dual']=dict(available=False,value_native=None,value_yuan_per_mwh=None,native_unit=row['native_unit'],constraint=None)
    r.update(issue_t=4,prices={},dispatch_prices={},pricing_info={},slack={},mip={},
             sced={'hydro_water_selection':{}},solution_quality={},repriced=True,pricing_source='bounded_auxiliary_pricing',
             stage_diagnostics={k:dict(units=[],summary={},integer_values=[],prices=[],hydro_states=[]) for k in ('scuc_preplan','energy_sced','auxiliary_pricing')})
    return cfg,plan,r,values


@pytest.mark.parametrize('broken',[None,'partial','missing_model','nonfinite_error'])
def test_same_solved_values_keep_handoff_and_strict_contract(context,monkeypatch,broken):
    cfg,plan,r,values=completed_probe(context)
    first=next(iter(values['E']))
    def read(model,*,errors=None):
        out=copy.deepcopy(values)
        if broken in ('partial','nonfinite_error'):
            exc=KeyError('missing') if broken=='partial' else ValueError('nonfinite balance dual')
            if errors is None:raise exc
            del out['E'][first]
            errors['E',first]=f'intraday_SCED_dual_invalid:E.{first}: {type(exc).__name__}: {exc}'
        return out
    monkeypatch.setattr(scuc,'state_values',read)
    if broken=='missing_model':r.pop('model')
    before=rolling.intraday_reference(plan,plan,r,cfg.T,cfg=cfg)
    expected=None
    if broken=='nonfinite_error':
        with pytest.raises(ValueError,match='nonfinite'):id_state_values(r)
    else:expected=id_state_values(r)
    evidence=retention.capture_state_evidence(r)
    compact=retention.detach_id_models(r)
    assert compact['snap'] is r['snap'] and compact['stage_diagnostics'] is r['stage_diagnostics']
    assert 'model' not in compact and 'pricing_model' not in compact
    monkeypatch.setattr(scuc,'state_values',lambda *a,**kw:pytest.fail('compact must never reread/reprice model'))
    after=rolling.intraday_reference(plan,plan,compact,cfg.T,cfg=cfg)
    def same(a,b):
        if isinstance(a,np.ndarray):
            assert a.dtype==b.dtype;np.testing.assert_array_equal(a,b)
        elif isinstance(a,dict):
            assert set(a)==set(b)
            for k in a:same(a[k],b[k])
        else:assert a==b
    same(before,after)
    if broken=='nonfinite_error':
        with pytest.raises(ValueError,match='nonfinite'):id_state_values(compact)
    else:assert digest(expected)==digest(id_state_values(compact))
    if broken=='partial':
        assert after['reference_metadata']['E'][first]['trajectory_source']=='day_ahead'
        assert any(x['trajectory_source'].startswith('intraday') for kind in ('E','Vu','V') for x in after['reference_metadata'][kind].values())
        assert evidence['resource_errors'] and evidence['strict_audit']['status']=='unavailable'


@pytest.mark.parametrize('damage',['missing','hash','clock','dtype','strict','stage','aux','water'])
def test_compact_evidence_corruption_fails_closed(context,monkeypatch,damage):
    cfg,plan,r,values=completed_probe(context)
    monkeypatch.setattr(scuc,'state_values',lambda *a,**kw:copy.deepcopy(values))
    retention.capture_state_evidence(r);compact=retention.detach_id_models(r)
    if damage=='missing':compact.pop('state_value_evidence')
    elif damage=='hash':compact['state_value_evidence']['evidence_hash']='bad'
    elif damage=='clock':compact['tau']+=1
    elif damage=='dtype':
        rec=next(iter(compact['state_value_evidence']['values']['E'].values()));rec['dual_raw']=rec['dual_raw'].astype('float32')
        compact['state_value_evidence']['evidence_hash']=digest(retention._payload(compact['state_value_evidence']))
    elif damage=='strict':
        compact['state_value_evidence'].pop('strict_audit')
        compact['state_value_evidence']['evidence_hash']=digest(retention._payload(compact['state_value_evidence']))
    elif damage=='stage':compact['stage_diagnostics'].pop('energy_sced')
    elif damage=='aux':compact['pricing_info']=None
    elif damage=='water':compact['sced'].pop('hydro_water_selection')
    with pytest.raises(ValueError):retention.detach_id_models(compact)
    if damage in ('missing','hash','clock','dtype','strict'):
        with pytest.raises(ValueError):id_state_values(compact)
        with pytest.raises(ValueError):rolling.intraday_reference(plan,plan,compact,cfg.T,cfg=cfg)


def test_chunked_hash_matches_full_file(tmp_path):
    from smm.series import _hash_file
    import hashlib
    p=tmp_path/'binary';p.write_bytes(bytes(range(256))*10003)
    assert _hash_file(p)==hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.mark.parametrize('flag',[None,0,1,'false'])
def test_debug_retention_flags_reject_non_boolean(flag):
    from smm.full import run_full
    with pytest.raises(ValueError,match='retain_id_models'):run_full(None,None,retain_id_models=flag)
    with pytest.raises(ValueError,match='retain_id_models'):rolling.run_rolling(None,retain_id_models=flag)


@pytest.mark.parametrize('damage',['bool_version','bool_start','unexplained_resource','foreign_error','empty_error','strict_missing_resource'])
def test_rehashed_cache_structure_and_resource_coverage_fail(context,monkeypatch,damage):
    _,_,r,values=completed_probe(context)
    monkeypatch.setattr(scuc,'state_values',lambda *a,**kw:copy.deepcopy(values))
    record=retention.capture_state_evidence(r)
    first=next(iter(record['values']['E']))
    if damage=='bool_version':record['schema_version']=True
    elif damage=='bool_start':record['window_start']=True;r['tau']=1
    elif damage=='unexplained_resource':record['values']['E'].pop(first)
    elif damage=='foreign_error':record['resource_errors']=[dict(kind='E',unit='foreign',reason='missing')]
    elif damage=='empty_error':
        record['values']['E'].pop(first);record['resource_errors']=[dict(kind='E',unit=first,reason='')]
    elif damage=='strict_missing_resource':record['strict_audit']['values']['E'].pop(first)
    record['evidence_hash']=digest(retention._payload(record))
    with pytest.raises(ValueError):retention.cached_state_evidence(r)


@pytest.mark.parametrize('damage',['split_value','fabricated_partial_error'])
def test_successful_same_source_caches_cannot_diverge(context,monkeypatch,damage):
    _,_,r,values=completed_probe(context)
    monkeypatch.setattr(scuc,'state_values',lambda *a,**kw:copy.deepcopy(values))
    record=retention.capture_state_evidence(r)
    first=next(iter(record['values']['E']))
    if damage=='split_value':record['values']['E'][first]['value_native'][0]+=123.
    else:
        record['values']['E'].pop(first)
        record['resource_errors']=[dict(kind='E',unit=first,reason='fabricated invalid dual')]
    record['evidence_hash']=digest(retention._payload(record))
    with pytest.raises(ValueError):retention.cached_state_evidence(r)
