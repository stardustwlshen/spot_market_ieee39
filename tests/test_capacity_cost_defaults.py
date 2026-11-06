"""Versioned research defaults: size/technology, independent cash costs, old archives."""
import copy
import hashlib
import json
from pathlib import Path
import shutil

import pytest
import yaml

from smm.config import ROOT, load_config
from smm.bids import default_bids, legacy_default_bids, load_bids, validate_bids
from smm.research_defaults import (ALGORITHM_VERSION, generate_research_defaults,
                                  research_record_hash, thermal_research_parameters)
from smm.research_cost_setup import (FILENAME, prepare_generated_cost_setup,
                                  read_generated_costs, _bytes)
from smm.profit import load_fixed_cost_profile
from smm.unit_editor import unit_template


def archive_hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ('config', 'bids', 'scenarios') for p in (root / folder).rglob('*')
            if p.is_file()}


@pytest.mark.parametrize('typ,reference,fuel,heat,vom', [
    ('coal',646,30,9,10),('gas_ccgt',687,60,6.5,15),('gas_gt',300,60,10,20)])
def test_size_changes_incremental_cost_and_shared_startup_template(typ, reference, fuel, heat, vom):
    costs=[]
    for capacity in (300.,600.,1000.):
        d=thermal_research_parameters(typ,capacity)
        h=heat*min(1.15,max(.90,(capacity/reference)**(-.08)))
        v=vom*min(1.15,max(.90,(capacity/reference)**(-.05)))
        assert d['heat_rate']==pytest.approx(h)
        assert d['variable_om']==pytest.approx(v)
        assert d['heat_rate_basis']=='incremental_hhv_excludes_separate_online_intercept'
        spec=unit_template(typ,'NEW',31,capacity)
        cfg=load_config();cfg.units={key:[] for key in ('thermal','renewable','storage','hydro','pumped','tie')}
        cfg.units['thermal']=[spec]
        output=generate_research_defaults(cfg)
        rec=output['profile']['units']['NEW']
        costs.append(rec['scalar_marginal_cost_yuan_per_mwh'])
        assert costs[-1]==pytest.approx(fuel*h+v)
        assert spec['noload']==rec['fixed_online_cost_yuan_per_h']
        assert spec['startup']['hot']==rec['fixed_startup_cost_yuan_per_start']
        assert output['evidence']['NEW']['algorithm_version']==ALGORITHM_VERSION
        assert output['evidence']['NEW']['source_kind']=='research_assumption_not_observed_plant_data'
    assert costs[0]>costs[1]>costs[2]


def test_equal_capacity_thermal_technologies_have_distinct_costs_and_startups():
    d={t:thermal_research_parameters(t,300.) for t in ('coal','gas_ccgt','gas_gt')}
    marginal={t:x['fuel_price']*x['heat_rate']+x['variable_om'] for t,x in d.items()}
    assert marginal['coal']<marginal['gas_ccgt']<marginal['gas_gt']
    assert len({x['fixed_startup_cost_yuan_per_start'] for x in d.values()})==3


def test_bounds_and_nonfinite_inputs_fail_closed():
    assert thermal_research_parameters('coal',1.)['heat_rate_capacity_factor']==1.15
    assert thermal_research_parameters('coal',10000.)['heat_rate_capacity_factor']==.90
    for value in (None,0,-1,True,float('nan'),float('inf'),'100'):
        with pytest.raises(ValueError):thermal_research_parameters('coal',value)


def test_nonthermal_capacity_quantity_and_efficiency_are_not_fake_price_multipliers():
    cfg=load_config();old=generate_research_defaults(cfg)
    cfg.units['hydro'][0]['n_units']*=2
    cfg.units['pumped'][0]['n_units']*=2
    cfg.units['renewable'][0]['cap']*=2
    cfg.units['storage'][0]['pmax']*=2;cfg.units['storage'][0]['ecap']*=2
    new=generate_research_defaults(cfg)
    for uid in ('H1','PS1','W1','ES1'):
        assert new['profile']['units'][uid]==old['profile']['units'][uid]
        assert new['evidence'][uid]['capacity_mw']==old['evidence'][uid]['capacity_mw']*2
    assert new['evidence']['ES1']['technical_parameters']['duration_h']==2
    # Four-hour cell quantity changes energy, not an invented scale multiplier on throughput expense.
    cfg.units['storage'][0]['ecap']*=2
    four=generate_research_defaults(cfg)
    assert four['evidence']['ES1']['technical_parameters']['duration_h']==4
    assert four['profile']['units']['ES1']==old['profile']['units']['ES1']


def test_new_default_uses_per_unit_archives_without_rewriting_them():
    cfg=load_config();assert cfg.base['default_bid_model']=='cost_basis_v2'
    before=archive_hashes(ROOT);b=default_bids(cfg)
    assert b['default_bid_model']=='cost_basis_v2'
    assert not [i for i in validate_bids(cfg,b,require_complete=True) if i.level=='error']
    for uid,anchor in {'C1':280,'C2':297,'C3':315,'CCGT':405,'GT':620}.items():
        q=b['units'][uid];assert q['segments'][0]['price']==q['segments'][1]['price']==anchor
    fixed=load_fixed_cost_profile(cfg)['units']
    for uid in ('C1','C2','C3','CCGT','GT'):
        assert b['units'][uid]['startup_cost_yuan']==fixed[uid]['fixed_startup_cost_yuan_per_start']
        assert b['units'][uid]['noload_cost']==fixed[uid]['fixed_online_cost_yuan_per_h']
    assert before==archive_hashes(ROOT)
    # Separate inherited per-unit study records win over a generic new-resource proxy.
    assert fixed['C2']['scalar_marginal_cost_yuan_per_mwh']==297
    assert generate_research_defaults(cfg)['profile']['units']['C2']['scalar_marginal_cost_yuan_per_mwh']!=297


def test_legacy_selector_missing_keeps_old_shapes_and_bad_selector_fails():
    cfg=load_config();cfg.base.pop('default_bid_model')
    b=default_bids(cfg);old=legacy_default_bids(cfg)
    assert b['units']==old['units']
    assert [s['price'] for s in b['units']['C2']['segments']]==[0,300,320,340,370,410]
    cfg.base['default_bid_model']='typo'
    with pytest.raises(ValueError,match='default_bid_model'):default_bids(cfg)


def test_cost_default_never_falls_back_when_independent_cost_archives_missing(tmp_path):
    cfg=load_config();cfg.root=tmp_path
    with pytest.raises(ValueError,match='成本研究档案'):default_bids(cfg)


def test_frozen_v5_does_not_change_with_season_and_explicit_preview_does():
    wet=load_config();dry=load_config(season='dry')
    assert load_bids(wet)['version']==load_bids(dry)['version']==5
    assert load_bids(wet)==load_bids(dry)
    assert default_bids(wet)['units']['H1']['segments']!=default_bids(dry)['units']['H1']['segments']
    for uid in ('C1','C2','C3','CCGT','GT','ES1','PS1'):
        assert default_bids(wet)['units'][uid]==default_bids(dry)['units'][uid]


def test_v1_overlay_still_loads_unchanged_and_keeps_original_version_source(tmp_path):
    shutil.copytree(ROOT/'config',tmp_path/'config')
    cfg=load_config(root=tmp_path);data=prepare_generated_cost_setup(cfg)['data']
    # Standalone historical envelope with exact old record, not a relabeled v2 numeric result.
    data['version']='typed-research-defaults-v1'
    data['profile']['units']['C2']['scalar_marginal_cost_yuan_per_mwh']=280
    data['worksheet']['thermal']['C2']={'fuel_price':30,'heat_rate':9,'variable_om':10}
    for uid,record in data['profile']['units'].items():
        data['evidence'][uid]['algorithm_version']='typed-research-defaults-v1'
        data['evidence'][uid]['record_hash']=research_record_hash(record)
    for group in ('thermal','renewable_vom','hydro','storage','pumped'):
        for uid,record in data['worksheet'][group].items():
            data['worksheet_evidence'][group][uid]['algorithm_version']='typed-research-defaults-v1'
            data['worksheet_evidence'][group][uid]['record_hash']=research_record_hash(record)
    data.pop('fingerprint');data['fingerprint']=hashlib.sha256(_bytes(data)).hexdigest()
    path=tmp_path/'config'/FILENAME;path.write_bytes(_bytes(data));before=path.read_bytes()
    assert read_generated_costs(tmp_path)==data
    fixed=load_fixed_cost_profile(cfg)
    assert fixed['units']['C2']['scalar_marginal_cost_yuan_per_mwh']==280
    assert fixed['unit_sources']['C2']['generator_version']=='typed-research-defaults-v1'
    assert path.read_bytes()==before


def test_handwritten_quote_cost_separation_and_selected_preview_no_cross_resource_mutation():
    from smm.bid_simulation import research_cost_bid_preview
    cfg=load_config();base=default_bids(cfg);base['units']['C2']['segments'][-1]['price']+=50
    original=copy.deepcopy(base);before=archive_hashes(ROOT)
    candidate=research_cost_bid_preview(cfg,selected_unit_ids=['C1'],base_bids=base,protected_unit_ids=['C2'])
    assert candidate['units']['C2']==original['units']['C2']
    assert base==original
    assert archive_hashes(ROOT)==before
    with pytest.raises(ValueError,match='受保护'):
        research_cost_bid_preview(cfg,selected_unit_ids=['C2'],base_bids=base,protected_unit_ids=['C2'])


def test_complete_default_96_period_model_has_exact_offer_cost_coefficients():
    from pyomo.repn.standard_repn import generate_standard_repn
    from smm.pipeline import prepare
    from smm.scuc import build_model
    cfg=load_config();case=prepare(cfg,read_only=True,verbose=False)
    assert cfg.T==96 and len(case.bids['units'])==17 and case.bids['version']==5
    model=build_model(case)
    repn=generate_standard_repn(model.obj.expr)
    coeff={id(v):float(c) for v,c in zip(repn.linear_vars,repn.linear_coefs)}
    get=lambda var:coeff.get(id(var),0.)
    for uid in model.G:
        q=case.bids['units'][uid]
        for t in model.T:
            assert get(model.u[uid,t])==pytest.approx(q['noload_cost']*cfg.dt)
            assert get(model.v[uid,t])==pytest.approx(q['startup_cost_yuan'])
            for k,segment in enumerate(q['segments']):
                assert get(model.p[uid,k,t])==pytest.approx(segment['price']*cfg.dt)
    for uid in model.S:
        q=case.bids['units'][uid]
        for t in model.T:
            for k,segment in enumerate(q['dis_segments']):
                assert get(model.pd[uid,k,t])==pytest.approx(segment['price']*cfg.dt)
            for k,segment in enumerate(q['ch_segments']):
                assert get(model.pc[uid,k,t])==pytest.approx(-segment['price']*cfg.dt)
    for uid in model.PS:
        q=case.bids['units'][uid];tech=model._md.pumped[uid]
        for t in model.T:
            assert get(model.sg[uid,t])==pytest.approx(tech['start_cost_gen'])
            assert get(model.sp[uid,t])==pytest.approx(tech['start_cost_pump'])
            for k,segment in enumerate(q['gen_segments']):
                assert get(model.pg[uid,k,t])==pytest.approx(segment['price']*cfg.dt)
            for k,segment in enumerate(q['pump_segments']):
                assert get(model.pp[uid,k,t])==pytest.approx(-segment['price']*cfg.dt)
    for uid,k in model.JK:
        for t in model.T:
            assert get(model.ph[uid,k,t])==pytest.approx(model._md.hydro_units[uid]['c'][k]*cfg.dt)
    for uid,k in model.WK:
        for t in model.T:
            assert get(model.pw[uid,k,t])==pytest.approx(case.bids['units'][uid]['segments'][k]['price']*cfg.dt)


def test_random_translation_retires_prior_cost_basis_without_losing_evidence():
    from smm.bid_simulation import simulate_bids, research_cost_bid_preview
    cfg=load_config();base=default_bids(cfg);old=copy.deepcopy(base)
    random=simulate_bids(cfg,selected_unit_ids=['C1'],seed=42,base_bids=base)
    assert '_cost_basis' not in random
    assert random['_cost_basis_history']==[old['_cost_basis']]
    assert base==old
    cost_random=research_cost_bid_preview(cfg,selected_unit_ids=['C1'],strategy_seed=42,base_bids=base)
    assert cost_random['_cost_basis']['selected_unit_ids']==['C1']
    assert cost_random['_cost_basis']['strategy_seed']==42
