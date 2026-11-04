"""New-scene boundary compositions preserve originals and reach model inputs."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys

import numpy as np
import pandas as pd
import pytest
import yaml

from smm import scenario
from smm.config import ROOT, load_config
from smm.bids import default_bids, save_bids
from smm.forecast import generate_profiles, apply_profile_overrides, forecast_view
from smm.typical_scenarios import prepare_typical, create_typical, fingerprint, MANIFEST, BASELINE


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(scenario, 'SCEN_DIR', tmp_path/'scenarios')
    root = tmp_path/'source'
    scenario._copy_tree(ROOT, root)
    cfg = load_config(root=root)
    bids = default_bids(cfg)
    # Deliberate user price edit must survive every boundary operation.
    bids['units']['C1']['segments'][1]['price'] += 10
    save_bids(cfg, bids)
    record = {'schema': 1, 'draft': bids, 'baseline': default_bids(cfg), 'basis_label': 'test current quotation', 'history': {}, 'manual': ['C1'], 'initial_latest': bids}
    (root/'current_quote.json').write_text(json.dumps(record), encoding='utf-8')
    return root


def file_hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('season', ['wet', 'dry'])
@pytest.mark.parametrize('renewable,load', [(1.,1.), (1.2,1.1),(.8,.9),(1.2,.9),(.8,1.1)])
def test_combination_changes_full_series_and_true_seasonal_model_inputs(source, season, renewable, load):
    before = file_hashes(source)
    cfg = load_config(root=source)
    expected = apply_profile_overrides(cfg, generate_profiles(cfg))
    preview = prepare_typical(source, season=season, renewable_multiplier=renewable, load_multiplier=load)
    assert preview['season'] == season
    for stage in ('actual','da','id','rt'):
        np.testing.assert_allclose(preview['profiles'][f'load_{stage}'], expected[f'load_{stage}']*load)
        for unit in cfg.units['renewable']:
            np.testing.assert_allclose(preview['profiles'][f"{unit['id']}_{stage}"], np.minimum(expected[f"{unit['id']}_{stage}"]*renewable, unit['cap']))
    for row in preview['hydro']:
        spec = next(u for u in cfg.units['hydro'] if u['id'] == row['电站'])['seasons'][season]
        assert row['qin'] == spec['qin'] and row['v0'] == spec['v0'] and row['e_day'] == spec['e_day']
    assert file_hashes(source) == before


def test_uploaded_four_stages_and_declared_da_are_real_baseline(source):
    cfg = load_config(root=source)
    upload = pd.DataFrame({f'load_{stage}': [3000+i]*cfg.T for i,stage in enumerate(('actual','da','id','rt'))})
    for i,stage in enumerate(('actual','da','id','rt')):
        upload[f'W1_{stage}'] = np.arange(cfg.T)+200+i*30
    upload.to_csv(source/'data/profile_overrides.csv', index=False)
    current = json.loads((source/'current_quote.json').read_text())['draft']
    current['units']['W1']['avail_pmax'] = [500.]*cfg.T
    preview = prepare_typical(source, bids=current, renewable_multiplier=.8, load_multiplier=1.1)
    assert preview['availability_changes'] == ['W1']
    assert preview['profiles'].W1_da.tolist() == [400.]*cfg.T
    assert preview['bids']['units']['W1']['avail_pmax'] == [400.]*cfg.T
    np.testing.assert_allclose(preview['profiles'].W1_actual, upload.W1_actual*.8)
    np.testing.assert_allclose(preview['profiles'].W1_id, upload.W1_id*.8)
    with pytest.raises(ValueError, match='明确同意'):
        create_typical('未确认', preview, bids=current)
    assert not scenario.scenario_path('未确认').exists()
    before = file_hashes(source)
    created = create_typical('组合', preview, bids=current, confirm_availability=True)
    assert file_hashes(source) == before
    assert (created/'data/typical_source_overrides.csv').read_bytes() == (source/'data/profile_overrides.csv').read_bytes()
    from smm.pipeline import prepare
    case = prepare(load_config(root=created), verbose=False)
    np.testing.assert_allclose(case.ren_fc['W1'], 400.)
    converted = sum((preview['profiles'][f"{t['id']}_da"] for t in cfg.units.get('tie', [])), start=np.zeros(cfg.T)) if cfg.base.get('tie_line_mode') == 'fixed_load' else 0
    np.testing.assert_allclose(sum(case.load_bus.values()), 3001*1.1+converted)
    assert case.bids['units']['C1']['segments'] == current['units']['C1']['segments']
    # Rolling forecast uses the changed ID/RT series, and first actual is exact.
    np.testing.assert_allclose(forecast_view(case.profiles,'W1',0,[0,1,2],first_actual=True)[0], upload.W1_actual.iloc[0]*.8)


def test_recombine_is_not_cumulative_and_restore_keeps_new_price_edits(source):
    first = create_typical('第一组合', prepare_typical(source, season='dry', renewable_multiplier=.8, load_multiplier=1.1))
    baseline = pd.read_csv(first/BASELINE)
    recombined = prepare_typical(first, season='wet', renewable_multiplier=1.2, load_multiplier=.9)
    np.testing.assert_allclose(recombined['profiles'].load_actual, baseline.load_actual*.9)
    # Change a price only, which is not a reason to lose the original curve basis.
    quote = json.loads((first/'current_quote.json').read_text())['draft']
    quote['units']['C1']['segments'][1]['price'] += 10
    restored = prepare_typical(first, bids=quote, restore=True)
    destination = create_typical('基准恢复', restored, bids=quote)
    result = apply_profile_overrides(load_config(root=destination), generate_profiles(load_config(root=destination)))
    np.testing.assert_allclose(result.load_actual, baseline.load_actual)
    assert load_config(root=destination).base['season'] == load_config(root=source).base['season']
    saved = json.loads((destination/'current_quote.json').read_text())['draft']
    assert saved['units']['C1']['segments'] == quote['units']['C1']['segments']


def test_clipping_does_not_change_nameplate_and_is_reported(source):
    cfg=load_config(root=source)
    upload=pd.DataFrame({f'W1_{stage}':[cfg.units['renewable'][0]['cap']]*cfg.T for stage in ('actual','da','id','rt')})
    upload.to_csv(source/'data/profile_overrides.csv',index=False)
    preview=prepare_typical(source,renewable_multiplier=1.2)
    assert sum(row['触及装机上限点数'] for row in preview['clipping']) >= cfg.T*4
    assert preview['profiles'].W1_actual.max()==cfg.units['renewable'][0]['cap']
    assert load_config(root=source).units==cfg.units


def test_stale_input_quote_duplicate_name_and_lost_basis_fail_closed(source):
    preview=prepare_typical(source,load_multiplier=.9)
    current=json.loads((source/'current_quote.json').read_text())['draft']
    current['units']['C1']['segments'][1]['price']+=10
    with pytest.raises(RuntimeError,match='报价已变化'):
        create_typical('changed_quote',preview,bids=current)
    created=create_typical('first',preview)
    before=file_hashes(created)
    with pytest.raises(FileExistsError):create_typical('first',preview)
    assert file_hashes(created)==before
    profiles=pd.read_csv(created/'data/profile_overrides.csv');profiles['load_actual']*=1.01
    profiles.to_csv(created/'data/profile_overrides.csv',index=False)
    with pytest.raises(ValueError,match='自定义修改'):prepare_typical(created)
    restored=prepare_typical(created,restore=True)
    np.testing.assert_allclose(restored['profiles'].load_actual, pd.read_csv(created/BASELINE).load_actual)
    reset=prepare_typical(created,use_current_as_baseline=True,load_multiplier=1.)
    np.testing.assert_allclose(reset['profiles'].load_actual,profiles.load_actual)
    (source/'config/base.yaml').write_text((source/'config/base.yaml').read_text()+'\n# changed\n')
    with pytest.raises(RuntimeError,match='源场景'):create_typical('stale',preview)
    (created/BASELINE).unlink()
    with pytest.raises(ValueError):prepare_typical(created)


def test_converted_load_scaled_once_before_model_allocation(source):
    # The conversion module may not yet be merged; explicit four-stage input is
    # enough to verify the composition contract independently of dispatch code.
    path=source/'config/base.yaml';base=yaml.safe_load(path.read_text());base['tie_line_mode']='fixed_load';path.write_text(yaml.safe_dump(base))
    import smm.typical_scenarios as module
    original=module.generate_profiles
    def generated(cfg):
        frame=original(cfg)
        for stage in ('actual','da','id','rt'):frame[f'TIE_{stage}']=np.arange(cfg.T)+700
        return frame
    from unittest.mock import patch
    with patch.object(module,'generate_profiles',generated):
        preview=prepare_typical(source,load_multiplier=1.1)
    for stage in ('actual','da','id','rt'):
        np.testing.assert_allclose(preview['profiles'][f'TIE_{stage}'],(np.arange(96)+700)*1.1)
    original_cfg=load_config(root=source)
    np.testing.assert_allclose(preview['profiles'].load_da,original(original_cfg).load_da*1.1)
    assert any(row['对象']=='全网区内外生负荷合计' for row in preview['summary'])


def typical_app():
    import sys
    from pathlib import Path
    from smm.config import ROOT as project_root
    sys.path.insert(0, str(project_root/'app'))
    import json
    import streamlit as st
    from smm.config import load_config
    from typical_scenario_controls import render_typical_scenarios
    cfg=load_config(root=st.session_state['source'])
    bids=json.loads((cfg.root/'current_quote.json').read_text())['draft']
    render_typical_scenarios(cfg,cfg.root.name,bids=bids,disabled=st.session_state.get('disabled',False))


def test_ui_combination_preview_create_and_repeated_creation_guard(source):
    from streamlit.testing.v1 import AppTest
    before=file_hashes(source)
    at=AppTest.from_function(typical_app,default_timeout=30)
    at.session_state['source']=str(source)
    at.run()
    assert not at.exception
    at.selectbox(key='typical:source:season').set_value('枯水期').run()
    at.selectbox(key='typical:source:renewable').set_value('大发').run()
    at.selectbox(key='typical:source:load').set_value('低负荷').run()
    at.button(key='typical:source:preview_button').click().run()
    assert not at.exception
    preview=at.session_state['typical:source:preview']
    assert preview['season']=='dry' and preview['renewable_multiplier']==1.2 and preview['load_multiplier']==.9
    assert any('合成研究情景' in c.value and '不参与报价' in c.value for c in at.caption)
    at.text_input(key='typical:source:destination').set_value('UI组合').run()
    at.button(key='typical:source:create').click().run()
    assert not at.exception
    assert at.session_state['typical_switch_to']=='UI组合'
    assert scenario.scenario_path('UI组合').exists()
    assert file_hashes(source)==before
    at.button(key='typical:source:preview_button').click().run()
    at.text_input(key='typical:source:destination').set_value('UI组合').run()
    at.button(key='typical:source:create').click().run()
    assert any('未创建新场景' in e.value for e in at.error)
    assert file_hashes(source)==before


def test_ui_declared_available_requires_explicit_new_scene_confirmation(source):
    from streamlit.testing.v1 import AppTest
    path=source/'current_quote.json';record=json.loads(path.read_text())
    record['draft']['units']['W1']['avail_pmax']=[500.]*96
    path.write_text(json.dumps(record))
    at=AppTest.from_function(typical_app,default_timeout=30)
    at.session_state['source']=str(source);at.run()
    at.selectbox(key='typical:source:renewable').set_value('小发').run()
    at.button(key='typical:source:preview_button').click().run()
    assert at.button(key='typical:source:create').disabled
    at.checkbox(key='typical:source:confirm_availability').check().run()
    assert not at.button(key='typical:source:create').disabled
    at.selectbox(key='typical:source:load').set_value('高负荷').run()
    assert at.button(key='typical:source:create').disabled
    assert any('重新预览' in w.value for w in at.warning)


def test_composed_scene_reaches_actual_da_and_rt_solve(source):
    """Small authorized test scene, not a claim about full-day scenario feasibility."""
    path=source/'config/base.yaml';base=yaml.safe_load(path.read_text())
    base['periods']=8;base['solver']['time_limit_s']=20;base['solver']['mip_rel_gap']=.05
    path.write_text(yaml.safe_dump(base))
    path=source/'config/units.yaml';units=yaml.safe_load(path.read_text())
    for p in units.get('pumped',[]):p['enabled']=False
    path.write_text(yaml.safe_dump(units))
    cfg=load_config(root=source)
    record=json.loads((source/'current_quote.json').read_text());record['draft']=default_bids(cfg)
    (source/'current_quote.json').write_text(json.dumps(record))
    preview=prepare_typical(source,season='dry',renewable_multiplier=.8,load_multiplier=.9)
    created=create_typical('端到端组合',preview)
    from smm.pipeline import prepare
    from smm.da import run_da
    from smm.rolling import run_rolling,rt_tables
    case=prepare(load_config(root=created),verbose=False)
    da=run_da(case,verbose=False)
    rr=run_rolling(da,periods=1,log=lambda *a:None)
    tables=rt_tables(da,rr)
    assert len(rr.rt)==1
    assert da.case.cfg.base['season']=='dry'
    converted_actual = sum(preview['profiles'][f"{u['id']}_actual"].iloc[0] for u in case.cfg.units.get('tie', [])) if case.cfg.base.get('tie_line_mode') == 'fixed_load' else 0
    converted_da = sum((preview['profiles'][f"{u['id']}_da"] for u in case.cfg.units.get('tie', [])), start=np.zeros(case.cfg.T)) if case.cfg.base.get('tie_line_mode') == 'fixed_load' else 0
    assert tables['system'].load_mw.iloc[0]==pytest.approx(preview['profiles'].load_actual.iloc[0]+converted_actual)
    np.testing.assert_allclose(sum(da.case.load_bus.values()),preview['profiles'].load_da+converted_da)
    for bus,values in da.case.load_bus.items():
        np.testing.assert_allclose(values,(preview['profiles'].load_da+converted_da)*case.net.load_share[bus])
    for unit in case.cfg.units['renewable']:
        np.testing.assert_allclose(da.case.ren_fc[unit['id']],preview['profiles'][f"{unit['id']}_da"])


def test_uploaded_time_order_is_not_silently_ignored(source):
    cfg=load_config(root=source)
    frame=pd.DataFrame({'period': list(reversed(range(1,cfg.T+1))), 'load_actual': [3000.]*cfg.T})
    frame.to_csv(source/'data/profile_overrides.csv',index=False)
    with pytest.raises(ValueError,match='顺序'):prepare_typical(source)


def test_generic_copy_and_run_freeze_keep_typical_baseline(source):
    first=create_typical('原组合',prepare_typical(source,renewable_multiplier=.8,load_multiplier=1.1))
    copied=scenario.create_scenario('通用副本',source='原组合')
    assert (copied/BASELINE).read_bytes()==(first/BASELINE).read_bytes()
    assert (copied/MANIFEST).read_bytes()==(first/MANIFEST).read_bytes()
    recombined=prepare_typical(copied,load_multiplier=.9)
    np.testing.assert_allclose(recombined['profiles'].load_actual,pd.read_csv(first/BASELINE).load_actual*.9)
    from smm.live_runner import freeze_inputs
    directory=source.parent/'run_snapshot';directory.mkdir()
    frozen=freeze_inputs(copied,directory)
    assert (frozen/BASELINE).read_bytes()==(copied/BASELINE).read_bytes()
    assert (frozen/MANIFEST).read_bytes()==(copied/MANIFEST).read_bytes()


def test_full_workspace_switches_to_created_scene_without_losing_quote(source):
    from streamlit.testing.v1 import AppTest
    sys.path.insert(0,str(ROOT/'app'))
    destination=scenario.scenario_path(scenario.DEFAULT)
    scenario._copy_tree(source,destination)
    # Current source quote is intentionally part of this UI fixture.
    shutil.copy2(source/'current_quote.json',destination/'current_quote.json')
    at=AppTest.from_file(str(ROOT/'app/pages/2_运行.py'),default_timeout=60).run()
    assert not at.exception,[e.value for e in at.exception]
    key='typical:'+scenario.DEFAULT
    at.selectbox(key=key+':season').set_value('枯水期').run()
    at.selectbox(key=key+':load').set_value('低负荷').run()
    at.button(key=key+':preview_button').click().run()
    at.text_input(key=key+':destination').set_value('整页组合').run()
    at.button(key=key+':create').click().run()
    assert not at.exception,[e.value for e in at.exception]
    assert at.session_state['scenario']=='整页组合'
    assert at.selectbox(key='_sel_scenario').value=='整页组合'
    record=json.loads((scenario.scenario_path('整页组合')/'current_quote.json').read_text())
    original=json.loads((source/'current_quote.json').read_text())
    assert record['draft']['units']['C1']['segments']==original['draft']['units']['C1']['segments']
