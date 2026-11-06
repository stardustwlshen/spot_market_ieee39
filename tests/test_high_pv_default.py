"""High-PV defaults stay consistent without migrating old scenarios or quotes."""
import json
from pathlib import Path
import shutil
import numpy as np
import pytest
import yaml
from smm.config import ROOT,load_config
from smm.bids import load_bids,validate_bids,raise_on_errors
from smm.forecast import generate_profiles,apply_profile_overrides
from smm.case import build_da_case
from smm.network import build_network
from smm.scenario_assessment import assess_boundaries
from smm import scenario


def test_high_pv_capacity_quotes_costs_and_input_ratios_are_consistent():
    cfg=load_config();cfg.base['forecast']={'model':'multiscale_v2'}
    pv={u['id']:u for u in cfg.units['renewable'] if u['type']=='pv'}
    assert {k:(v['cap'],v['bus']) for k,v in pv.items()}=={'PV1':(652,33),'PV2':(750,4),'PV3':(750,8)}
    bids=load_bids(cfg);raise_on_errors(validate_bids(cfg,bids,require_complete=True))
    for uid,spec in pv.items():
        assert bids['units'][uid]['segments'][0]['p_start']==0
        assert bids['units'][uid]['segments'][-1]['p_end']==spec['cap']
    old=json.loads((ROOT/'bids/2026-07-15/MKT/bids_v2.json').read_text())
    archived_v4=json.loads((ROOT/'bids/2026-07-15/MKT/bids_v4.json').read_text())
    for uid,quote in old['units'].items():assert archived_v4['units'][uid]==quote
    assert bids['version']==5  # New cost baseline; old expansion archives stay unchanged
    fixed=yaml.safe_load((ROOT/'config/fixed_costs.yaml').read_text())['units']
    sheet=yaml.safe_load((ROOT/'config/bid_cost_basis.yaml').read_text())
    for uid in pv:
        assert fixed[uid]['scalar_marginal_cost_yuan_per_mwh']==sheet['renewable_vom'][uid]==5
    profiles=generate_profiles(cfg)
    for uid,spec in pv.items():
        for stage in ('actual','da','id','rt'):
            values=profiles[f'{uid}_{stage}'].to_numpy()
            assert np.all(values>=0) and np.all(values<=spec['cap'])
            assert np.all(values[:24]==0) and np.all(values[80:]==0)
    case=build_da_case(cfg,build_network(cfg),profiles,bids)
    result=assess_boundaries(case)
    assert result['capacities_mw']['pv']==2152
    assert result['renewable_local_generation_nameplate_share']==pytest.approx(3756/7487)
    actual=next(x for x in result['stages'] if x['stage']=='actual')
    assert actual['renewable_available_to_demand_ratio']==pytest.approx(.3596438472688824)


def test_old_scenario_copy_preserves_assets_profiles_and_old_quotes(tmp_path,monkeypatch):
    monkeypatch.setattr(scenario,'SCEN_DIR',tmp_path/'scenarios')
    old=scenario.create_scenario('old')
    path=old/'config/units.yaml';units=yaml.safe_load(path.read_text())
    units['renewable']=[u for u in units['renewable'] if u['id'] not in {'PV2','PV3'}]
    path.write_text(yaml.safe_dump(units,allow_unicode=True))
    for filename in ('fixed_costs.yaml','bid_cost_basis.yaml'):
        p=old/'config'/filename;d=yaml.safe_load(p.read_text())
        target=d['units'] if filename=='fixed_costs.yaml' else d['renewable_vom']
        for uid in ('PV2','PV3'):target.pop(uid,None)
        p.write_text(yaml.safe_dump(d,allow_unicode=True))
    for p in (old/'bids/2026-07-15/MKT').glob('bids_v*.*'):
        if int(p.stem.split('_v')[-1]) >= 3:p.unlink()
    cfg=load_config(root=old);profiles=generate_profiles(cfg);profiles[['W1_actual','PV1_da']].to_csv(old/'data/profile_overrides.csv',index=False)
    before={str(p.relative_to(old)):p.read_bytes() for p in old.rglob('*') if p.is_file()}
    copied=scenario.create_scenario('old_copy',source='old')
    new_cfg=load_config(root=copied)
    assert {u['id'] for u in new_cfg.units['renewable']}=={'W1','W2','PV1'}
    assert sum(u['cap'] for u in new_cfg.units['renewable'] if u['type']=='pv')==652
    assert load_bids(new_cfg)['version']==2
    assert (copied/'data/profile_overrides.csv').read_bytes()==before['data/profile_overrides.csv']
    assert before=={str(p.relative_to(old)):p.read_bytes() for p in old.rglob('*') if p.is_file()}
