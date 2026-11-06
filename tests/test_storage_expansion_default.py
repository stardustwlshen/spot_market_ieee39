"""Expanded battery defaults preserve archives, typed costs and ordinary UI paths."""
import copy
import json
from pathlib import Path
import shutil

import pytest
import yaml

from smm.config import ROOT, load_config
from smm.bids import load_bids, validate_bids, raise_on_errors
from smm.profit import load_fixed_cost_profile
from smm.bid_cost_basis import research_cost_worksheet
from smm.research_defaults import generate_research_defaults
from test_input_recovery_ui import scenario, item, open_app, files


def test_storage_capacity_energy_costs_and_old_quotes_are_consistent():
    cfg=load_config();units={u['id']:u for u in cfg.units['storage']}
    assert set(units)=={'ES1','ES2','ES3','ES4','ES5'}
    assert sum(u['pmax'] for u in units.values())==700
    assert sum(u['ecap'] for u in units.values())==2000
    assert sum(u['ecap']*(u['soc_max']-u['soc_min'])*u['eta_d'] for u in units.values())==pytest.approx(1520)
    bids=load_bids(cfg);raise_on_errors(validate_bids(cfg,bids,require_complete=True))
    assert bids['version']==5
    old=json.loads((ROOT/'bids/2026-07-15/MKT/bids_v3.json').read_text())
    archived_v4=json.loads((ROOT/'bids/2026-07-15/MKT/bids_v4.json').read_text())
    assert {uid:archived_v4['units'][uid] for uid in old['units']}==old['units']
    assert set(archived_v4['units'])-set(old['units'])=={'ES4','ES5'}
    assert set(bids['units'])==set(archived_v4['units'])
    fixed=load_fixed_cost_profile(cfg);sheet=research_cost_worksheet(cfg);generated=generate_research_defaults(cfg)
    for uid,bus in [('ES4',4),('ES5',8)]:
        spec=units[uid]
        assert (spec['bus'],spec['pmax'],spec['ecap'])==(bus,150,600)
        assert spec['ecap']/spec['pmax']==4
        assert spec['ecap']*(spec['soc_max']-spec['soc_min'])*spec['eta_d']/spec['pmax']==pytest.approx(3.04)
        assert fixed['units'][uid]==generated['profile']['units'][uid]
        assert sheet['storage'][uid]==generated['worksheet']['storage'][uid]
        for curve in ('dis_segments','ch_segments'):
            assert bids['units'][uid][curve][0]['p_start']==0
            assert bids['units'][uid][curve][-1]['p_end']==150
        assert bids['units'][uid]['soc_end']==.5


def test_copying_old_storage_scene_never_adds_new_assets_or_quotes(tmp_path,monkeypatch):
    from smm import scenario as sc
    monkeypatch.setattr(sc,'SCEN_DIR',tmp_path/'scenarios')
    old=sc.create_scenario('old_storage')
    p=old/'config/units.yaml';data=yaml.safe_load(p.read_bytes())
    data['storage']=[u for u in data['storage'] if u['id'] not in ('ES4','ES5')]
    p.write_text(yaml.safe_dump(data,allow_unicode=True))
    for filename,group in [('fixed_costs.yaml','units'),('bid_cost_basis.yaml','storage')]:
        p=old/'config'/filename;data=yaml.safe_load(p.read_bytes())
        for uid in ('ES4','ES5'):data[group].pop(uid,None)
        p.write_text(yaml.safe_dump(data,allow_unicode=True))
    for p in (old/'bids/2026-07-15/MKT').glob('bids_v*.*'):
        if int(p.stem.split('_v')[-1])>=4:p.unlink()
    before={p.relative_to(old).as_posix():p.read_bytes() for p in old.rglob('*') if p.is_file()}
    copied=sc.create_scenario('old_storage_copy',source='old_storage');cfg=load_config(root=copied)
    assert {u['id'] for u in cfg.units['storage']}=={'ES1','ES2','ES3'}
    assert load_bids(cfg)['version']==3
    assert not set(('ES4','ES5')) & set(load_bids(cfg)['units'])
    assert before=={p.relative_to(old).as_posix():p.read_bytes() for p in old.rglob('*') if p.is_file()}


@pytest.mark.parametrize('uid',['ES4','ES5'])
def test_new_battery_ui_cost_preview_stays_selected_and_does_not_write_archives(scenario,uid):
    at=open_app();item(at.selectbox,'模拟机组').select(uid).run()
    assert not at.exception
    key='quote_current:默认';old=copy.deepcopy(at.session_state[key]);before=files(scenario.scenario_path('默认'))
    item(at.button,'按成本生成预览').click().run()
    assert not at.exception
    candidate=at.session_state[key+':preview']['candidate']
    assert {u:q for u,q in candidate['units'].items() if u!=uid}=={u:q for u,q in old['units'].items() if u!=uid}
    for curve in ('dis_segments','ch_segments'):
        assert [(s['p_start'],s['p_end']) for s in candidate['units'][uid][curve]]==[(s['p_start'],s['p_end']) for s in old['units'][uid][curve]]
    assert at.session_state[key]==old
    assert files(scenario.scenario_path('默认'))==before
    assert not scenario.RUNS_DIR.exists()
