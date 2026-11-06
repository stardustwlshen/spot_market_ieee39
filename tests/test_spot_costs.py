from copy import deepcopy
from pathlib import Path
import json
import shutil
import pytest
import yaml
import pandas as pd
from smm import spot_costs as c
from smm.profit import analyze
from smm.config import load_config, ROOT
from test_operating_profit import sample
from test_profit_recalculation import fixture_run, hashes


def record(typ, **kwargs):
    r=dict(type=typ,source='Explicit non-O&M test costs',source_kind='user_defined')
    r.update({key:0. for key in c.FIELDS[typ]})
    if typ in c.THERMAL:r['online_cost_basis']='no_load_fuel'
    elif not c.FIELDS[typ]:r['cost_scope']='no_fuel_or_defined_start_stop_costs_excluding_om'
    r.update(kwargs);return r


def save(root, units):
    p=c.empty_profile();p['units']=units
    (root/'config'/c.FILENAME).write_text(yaml.safe_dump(p))
    return p


def scene(tmp_path):
    for f in ('config','data','bids'):
        shutil.copytree(ROOT/f,tmp_path/f)
    return load_config(root=tmp_path)


def test_new_default_missing_is_unknown_never_legacy(tmp_path):
    res,_=sample(tmp_path);result=analyze(res)
    assert result['summary']['profit_basis']==c.BASIS
    assert result['by_unit'].cash_operating_cost_yuan.isna().all()
    assert analyze(res,policy=c.LEGACY)['summary']['cash_operating_cost_yuan']>0


def test_formula_no_vom_no_double_charge_and_separate_offers(tmp_path):
    res,_=sample(tmp_path)
    save(tmp_path,{'G':record('gas_gt',fuel_cost_yuan_per_mwh=10,online_cost_yuan_per_h=40,startup_cost_yuan_per_start=100),
      'W':record('wind'),'H':record('hydro'),'B':record('storage'),
      'PS':record('pumped',gen_start_yuan_per_unit_start=30,pump_start_yuan_per_unit_start=50)})
    r=analyze(res);u=r['by_unit'].set_index('unit')
    assert u.loc['G','cash_operating_cost_yuan']==620
    assert u.loc['B','cash_operating_cost_yuan']==0 and u.loc['B','simulated_operating_profit_yuan']==80
    assert u.loc['PS','cash_operating_cost_yuan']==30 and u.loc['PS','simulated_operating_profit_yuan']==270
    assert u.loc['H','cash_operating_cost_yuan']==0
    (tmp_path/'config/bid_cost_basis.yaml').write_text('thermal: {G: {fuel_price: 99999}}')
    assert analyze(res)['summary']==r['summary']
    for frame in (r['by_period'],r['by_unit'],r['by_type']):assert set(frame.profit_basis)=={c.BASIS}
    res['settle']['detail'].loc[lambda d:d.entity=='B_充电',['total','da_amount','rt_amount']]=[60,48,12]
    assert analyze(res)['by_unit'].set_index('unit').loc['B','simulated_operating_profit_yuan']==320


def test_shutdown_uses_actual_t0_and_units_not_dt(tmp_path):
    res,_=sample(tmp_path)
    save(tmp_path,{'G':record('gas_gt',shutdown_cost_yuan_per_stop=70),
        'PS':record('pumped',gen_stop_yuan_per_unit_stop=30,pump_stop_yuan_per_unit_stop=50)})
    res['rr'].states[0].thermal['G']['online']=True;res['rr'].rt[0]['snap']['u']['G']=[0]
    res['rr'].states[0].pumped['PS'].update(ng=2,np=1)
    r=analyze(res)['by_period'].set_index(['unit','period'])
    assert r.loc[('G',0),'shutdown_cost_yuan']==70
    assert r.loc[('PS',0),'shutdown_cost_yuan']==80
    assert r.loc[('PS',0),'shutdown_count']==1 and r.loc[('PS',0),'pump_shutdown_count']==1


@pytest.mark.parametrize('bad',[float('nan'),float('inf'),-1,True])
def test_strict_fees(bad):
    with pytest.raises(ValueError):c.validate_record('G',record('coal',fuel_cost_yuan_per_mwh=bad))


def test_online_definition_and_unknown_keys_fail():
    for r in (record('coal',online_cost_basis='fixed_om'),record('coal',variable_om=5)):
        with pytest.raises(ValueError):c.validate_record('G',r)


def test_legacy_default_fuel_retained_custom_not_guessed(tmp_path):
    cfg=scene(tmp_path);generated,missing=c.research_defaults(cfg)
    assert not missing
    assert [generated['units'][u]['fuel_cost_yuan_per_mwh'] for u in ('C1','C2','C3','CCGT','GT')]==[270,285,300,390,600]
    path=tmp_path/'config/fixed_costs.yaml';raw=yaml.safe_load(path.read_text());raw['units']['C1']['scalar_marginal_cost_yuan_per_mwh']=281;path.write_text(yaml.safe_dump(raw))
    generated,missing=c.research_defaults(load_config(root=tmp_path));assert 'C1' in missing and 'C1' not in generated['units']


def test_noop_save_concurrent_running_and_quote_immutable(tmp_path):
    cfg=scene(tmp_path);before={str(p.relative_to(tmp_path)):p.read_bytes() for p in (tmp_path/'config').iterdir() if p.is_file()};current=c.load_profile(cfg)
    c.apply_edit(c.prepare_edit(cfg,{'C1':current['units']['C1']}))
    assert {str(p.relative_to(tmp_path)):p.read_bytes() for p in (tmp_path/'config').iterdir() if p.is_file()}==before
    edit=deepcopy(current['units']['C1']);edit['fuel_cost_yuan_per_mwh']=271.025
    plan=c.prepare_edit(cfg,{'C1':edit})
    with pytest.raises(ValueError):c.apply_edit(plan,running=True)
    c.apply_edit(plan);assert c.load_profile(cfg)['units']['C1']['fuel_cost_yuan_per_mwh']==271.025
    with pytest.raises(ValueError):c.apply_edit(plan)
    for path,raw in before.items():
        if not path.endswith(c.FILENAME):assert (tmp_path/path).read_bytes()==raw
    assert len(list((tmp_path/'spot_cost_versions').glob('*.yaml')))==2


def test_legacy_to_new_supplement_tie_and_no_rewrite(tmp_path):
    from smm.profit_recalculation import prepare_recalculation,apply_recalculation,read_recalculation
    run,source=fixture_run(tmp_path)
    save(source,{'C1':record('coal',fuel_cost_yuan_per_mwh=8,online_cost_yuan_per_h=4,startup_cost_yuan_per_start=20),'ES1':record('storage')})
    rt=pd.read_csv(run/'rt_units.csv');tie=pd.DataFrame([dict(unit='TIE',type='tie',period=t,on=1,P_mw=1) for t in range(2)]);pd.concat([rt,tie]).to_csv(run/'rt_units.csv',index=False)
    before=hashes(run);plan=prepare_recalculation(run,source,policy=c.BASIS)
    assert plan['summary']['profit_basis']==c.BASIS and plan['summary']['cash_operating_cost_yuan']==102
    path=apply_recalculation(plan);r=read_recalculation(path,run_dir=run)
    assert r['summary']['simulated_operating_profit_yuan']==138 and hashes(run)==before
    assert apply_recalculation(plan)==path
    rt=pd.read_csv(run/'rt_units.csv');rt.loc[rt.unit=='TIE','unit']='BAD';rt.to_csv(run/'rt_units.csv',index=False)
    with pytest.raises(ValueError):prepare_recalculation(run,source,policy=c.BASIS)


def test_new_archived_policy_recalculates_without_legacy_formula(tmp_path):
    from smm.profit_recalculation import prepare_recalculation,apply_recalculation,read_recalculation
    from types import SimpleNamespace
    run,source=fixture_run(tmp_path)
    profile=save(source,{'C1':record('coal',fuel_cost_yuan_per_mwh=8,online_cost_yuan_per_h=4,startup_cost_yuan_per_start=20),'ES1':record('storage')})
    first=read_recalculation(apply_recalculation(prepare_recalculation(run,source,policy=c.BASIS)))
    # Materialize a separate, valid native v2 archive from the same explicit evidence.
    new=tmp_path/'new_run';shutil.copytree(run,new,ignore=shutil.ignore_patterns('cost_recalculations'))
    for key in ('by_period','by_unit','by_type'):first[key].to_csv(new/f'operating_profit_{key}.csv',index=False,float_format='%.4f')
    shutil.copy2(source/'config'/c.FILENAME,new/'scenario_snapshot/config'/c.FILENAME)
    cfg=SimpleNamespace(root=new/'scenario_snapshot',units=yaml.safe_load((new/'scenario_snapshot/config/units.yaml').read_text()))
    (new/'fixed_cost_profile.json').write_text(json.dumps(c.load_profile(cfg)))
    summary=json.loads((new/'summary.json').read_text());summary['operating_profit']=first['summary'];(new/'summary.json').write_text(json.dumps(summary))
    result=read_recalculation(apply_recalculation(prepare_recalculation(new,source,policy=c.BASIS)))
    assert result['summary']['cash_operating_cost_yuan']==102
    assert result['summary']['original_profit_basis']==c.BASIS
    frame=pd.read_csv(new/'operating_profit_by_period.csv');mask=(frame.unit=='C1')&(frame.period==0)
    for key in ('variable_cost_yuan','cash_operating_cost_yuan'):frame.loc[mask,key]+=.00016
    frame.loc[mask,'simulated_operating_profit_yuan']-=.00016
    frame.to_csv(new/'operating_profit_by_period.csv',index=False,float_format='%.4f')
    # 8 yuan/MWh times a sub-0.00005 MWh rounding difference is allowed.
    assert prepare_recalculation(new,source,policy=c.BASIS)['summary']['profit_basis']==c.BASIS


def test_no_mixed_custom_inheritance_and_bad_yaml(tmp_path):
    cfg=scene(tmp_path);p=tmp_path/'config'/c.FILENAME
    p.write_text(p.read_text()+'\nversion: corrupt\n')
    with pytest.raises(ValueError,match='重复'):c.load_profile(cfg)


def test_native_type_change_is_incomplete_until_explicit_repair(tmp_path):
    cfg=scene(tmp_path)
    cfg.units['thermal'][0]['type']='gas_gt'
    p=c.load_profile(cfg)
    params,why=c.parameters(p,'C1','gas_gt')
    assert params is None and '类型' in why


def test_forged_save_cannot_drop_unedited_costs(tmp_path):
    cfg=scene(tmp_path);before=(tmp_path/'config'/c.FILENAME).read_bytes()
    plan=c.prepare_edit(cfg,{})
    del plan['profile']['units']['C1']
    with pytest.raises(ValueError,match='计划'):c.apply_edit(plan)
    assert (tmp_path/'config'/c.FILENAME).read_bytes()==before


def test_single_day_freeze_captures_spot_cost_and_rejects_bad_before_creation(tmp_path):
    from smm.live_runner import freeze_inputs
    cfg=scene(tmp_path/'scene');target=tmp_path/'run';target.mkdir()
    frozen=freeze_inputs(cfg.root,target)
    assert (frozen/'config'/c.FILENAME).read_bytes()==(cfg.root/'config'/c.FILENAME).read_bytes()
    p=cfg.root/'config'/c.FILENAME;p.write_text('version: corrupt')
    bad=tmp_path/'bad';bad.mkdir()
    with pytest.raises(ValueError):freeze_inputs(cfg.root,bad)
    assert not (bad/'scenario_snapshot').exists()
