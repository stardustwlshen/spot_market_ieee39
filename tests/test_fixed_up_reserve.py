"""Fixed total upward reserve, migration isolation and archived formula semantics."""
import copy
import json
from pathlib import Path
import numpy as np
import pytest
import yaml
from smm.config import load_config
from smm.case import reserve_requirement, build_da_case
from smm.reserve_policy import for_new_run, reserve_policy_label
from smm.forecast import generate_profiles
from smm.bids import default_bids
from smm.network import build_network
from smm.rolling import initial_state, make_window_case
from smm.live_runner import freeze_inputs
from test_unit_editor import scene, input_bytes

@pytest.mark.parametrize('size',[0,1,8,96])
@pytest.mark.parametrize('amount',[0.,400.,725.5])
def test_fixed_total_not_proportional_or_additive(size,amount):
    cfg=load_config();cfg.mode['reserve']['fixed_up_mw']=amount
    cfg.base['n1_loss_mw']=9000.
    load=np.linspace(0.,10000.,size);ren=np.linspace(0.,20000.,size)
    up,dn,info=reserve_requirement(cfg,load,ren,extra_n1=10000.)
    np.testing.assert_array_equal(up,np.full(size,amount))
    np.testing.assert_array_equal(dn,np.zeros(size))
    assert info['R_ctg']==0 and info['up_requirement_mode']=='fixed'

@pytest.mark.parametrize('bad',[-1,float('nan'),float('inf'),True,'400',None])
def test_invalid_fixed_total_rejected(bad):
    cfg=load_config();cfg.mode['reserve']['fixed_up_mw']=bad
    with pytest.raises(ValueError,match='fixed_up_mw'):reserve_requirement(cfg,np.ones(2),np.zeros(2))

@pytest.mark.parametrize('bad',['unknown',None,False,[],{}])
def test_invalid_mode_rejected(bad):
    cfg=load_config();cfg.mode['reserve']['up_requirement_mode']=bad
    with pytest.raises(ValueError,match='up_requirement_mode'):reserve_requirement(cfg,np.ones(2),np.zeros(2))

def test_missing_mode_archive_still_formula_new_run_detached_fixed():
    cfg=load_config();cfg.mode['reserve'].pop('up_requirement_mode');cfg.mode['reserve'].pop('fixed_up_mw')
    cfg.mode['reserve']['down_enabled']=True
    before=copy.deepcopy(cfg.mode)
    up,dn,info=reserve_requirement(cfg,np.array([100.,300.]),np.array([50.,0.]))
    np.testing.assert_allclose(up,np.array([8.,9.])+info['R_ctg'])
    candidate=for_new_run(cfg)
    np.testing.assert_array_equal(reserve_requirement(candidate,np.array([100.,300.]),np.array([50.,0.]))[0],[400.,400.])
    assert candidate.mode['reserve']['down_enabled'] is False and cfg.mode==before
    cfg.mode['reserve']['up_requirement_mode']='formula'
    assert for_new_run(cfg).mode['reserve']['up_requirement_mode']=='formula'

def test_all_case_builders_share_fixed_total():
    cfg=load_config();cfg.base['forecast']={'model':'legacy_v1'}
    case=build_da_case(cfg,build_network(cfg),generate_profiles(cfg),default_bids(cfg))
    np.testing.assert_array_equal(case.R_up,np.full(96,400.))
    state=initial_state(cfg)
    for stage,t0,t1 in [('ID',0,96),('RT',0,8),('RT',95,96)]:
        state.t=t0
        from datetime import datetime, timedelta
        state.hydro_commitment['state_at']=(datetime.fromisoformat(cfg.base['date'])+timedelta(minutes=15*t0)).isoformat()+'Z'
        window=make_window_case(case,cfg,t0,t1,t0,stage=='RT',state,{}, {},stage)
        np.testing.assert_array_equal(window.R_up,np.full(t1-t0,400.))
        np.testing.assert_array_equal(window.R_dn,np.zeros(t1-t0))

def test_freeze_migrates_only_new_snapshot_and_preserves_explicit_choice(scene,tmp_path):
    path=scene/'config/modes.yaml';doc=yaml.safe_load(path.read_text(encoding='utf-8'))
    doc['MKT']['reserve'].pop('up_requirement_mode');doc['MKT']['reserve'].pop('fixed_up_mw')
    path.write_text(yaml.safe_dump(doc,allow_unicode=True),encoding='utf-8')
    before=input_bytes(scene)
    run=tmp_path/'run';run.mkdir();snap=freeze_inputs(scene,run)
    cfg=load_config(root=snap)
    assert cfg.mode['reserve']['fixed_up_mw']==400. and cfg.mode['reserve']['up_requirement_mode']=='fixed'
    assert input_bytes(scene)==before
    assert (run/'reserve_source_modes.yaml').read_bytes()==before['config/modes.yaml']
    record=json.loads((run/'reserve_policy_migration.json').read_text())
    assert record['before']['up_requirement_mode']=='formula' and record['after']['fixed_up_mw']==400.
    assert load_config(root=scene).mode['reserve'].get('up_requirement_mode') is None
    assert '400' in reserve_policy_label(load_config(root=scene))
    with pytest.raises(FileExistsError):freeze_inputs(scene,run)
    doc['MKT']['reserve']['up_requirement_mode']='formula'
    path.write_text(yaml.safe_dump(doc,allow_unicode=True),encoding='utf-8')
    run2=tmp_path/'formula';run2.mkdir();snap2=freeze_inputs(scene,run2)
    assert load_config(root=snap2).mode['reserve']['up_requirement_mode']=='formula'
    assert not (run2/'reserve_policy_migration.json').exists()

def test_fixed_evidence_audits_and_detects_tampering():
    from test_da_stages import tiny_case
    from smm.da import run_da
    from smm.results import reserve_snapshot
    from smm.reserve_checks import audit
    c=tiny_case();c.cfg.mode['reserve'].update(up_requirement_mode='fixed',fixed_up_mw=400.,down_enabled=False)
    c.R_up,c.R_dn,c.reserve_info=reserve_requirement(c.cfg,np.array([100.]),np.array([0.]))
    res=run_da(c,verbose=False);evidence=reserve_snapshot(res.model)
    assert evidence['reserve_requirement_parameters']['fixed_up_mw']==400.
    assert audit({'da':evidence,'rt_windows':[]})['passed']
    bad=copy.deepcopy(evidence);bad['system'][0]['R_up_req']=401.
    assert not audit({'da':bad,'rt_windows':[]})['passed']


def legacy_scene(scene):
    path=scene/'config/modes.yaml';doc=yaml.safe_load(path.read_text(encoding='utf-8'))
    doc['MKT']['reserve'].pop('up_requirement_mode',None);doc['MKT']['reserve'].pop('fixed_up_mw',None)
    doc['MKT']['reserve']['down_enabled']=True
    path.write_text(yaml.safe_dump(doc,allow_unicode=True),encoding='utf-8')
    return path


def test_legacy_reserve_edit_materializes_complete_effective_policy(scene):
    from smm.advanced_parameters import prepare_parameter_edit,apply_parameter_edit
    legacy_scene(scene)
    apply_parameter_edit(prepare_parameter_edit(scene,{'mode.reserve.T_dur_h':.5}))
    r=load_config(root=scene).mode['reserve']
    assert (r['up_requirement_mode'],r['fixed_up_mw'],r['down_enabled'])==('fixed',400.,False)


def test_legacy_custom_fixed_edit_and_explicit_formula_are_preserved(scene):
    from smm.advanced_parameters import prepare_parameter_edit,apply_parameter_edit
    legacy_scene(scene)
    apply_parameter_edit(prepare_parameter_edit(scene,{'mode.reserve.fixed_up_mw':275.}))
    cfg=load_config(root=scene);assert for_new_run(cfg).mode['reserve']['fixed_up_mw']==275.
    apply_parameter_edit(prepare_parameter_edit(scene,{'mode.reserve.up_requirement_mode':'formula'}))
    assert for_new_run(load_config(root=scene)).mode['reserve']['up_requirement_mode']=='formula'


def test_migration_hash_includes_regulation_override(scene,tmp_path):
    import hashlib
    path=legacy_scene(scene);original=path.read_bytes()
    out=tmp_path/'override';out.mkdir()
    snap=freeze_inputs(scene,out,regulation={'enabled':False})
    record=json.loads((out/'reserve_policy_migration.json').read_text())
    assert record['effective_modes_sha256']==hashlib.sha256((snap/'config/modes.yaml').read_bytes()).hexdigest()
    assert (out/'reserve_source_modes.yaml').read_bytes()==original==path.read_bytes()


def test_new_policy_marks_old_run_stale_without_changing_archive(scene,tmp_path):
    from smm.input_version import source_version,compare_source_version,run_options
    from smm.live_runner import atomic_json
    import shutil
    legacy_scene(scene);old=tmp_path/'old';old.mkdir()
    shutil.copytree(scene,old/'scenario_snapshot')
    evidence=source_version(scene,options=run_options());evidence.pop('reserve_policy')
    atomic_json(old/'frozen_input_provenance.json',{'source_version':evidence})
    before=input_bytes(old/'scenario_snapshot')
    result=compare_source_version(old,scene,options=run_options())
    assert result['status']=='changed' and any('备用' in v for v in result['changes'])
    assert input_bytes(old/'scenario_snapshot')==before


def test_ui_exposes_effective_fixed_policy_and_explicit_formula(scene):
    import sys
    from smm.config import ROOT
    sys.path.insert(0,str(ROOT/'app'))
    from streamlit.testing.v1 import AppTest
    from test_unit_editor_ui import item
    legacy_scene(scene);before=input_bytes(scene)
    at=AppTest.from_string(f"from pathlib import Path\nfrom smm.config import load_config\nfrom advanced_parameters import render_advanced_parameters\nfrom reserve_policy_controls import render_reserve_policy\ncfg=load_config(root=Path({str(scene)!r}))\nrender_reserve_policy(cfg)\nrender_advanced_parameters(cfg,'fixed_test')",default_timeout=30).run()
    assert not at.exception and any('400 MW' in x.value for x in at.caption)
    assert input_bytes(scene)==before
    item(at.button,'编辑高级参数').click().run()
    assert item(at.selectbox,'上备用需求方式').value=='fixed'
    assert item(at.number_input,'上备用负荷比例').disabled
    assert not item(at.number_input,'固定上备用总量（MW）').disabled
    item(at.number_input,'固定上备用总量（MW）').set_value(275.)
    item(at.button,'保存高级参数').click().run()
    assert not at.exception and not at.error
    assert load_config(root=scene).mode['reserve']['fixed_up_mw']==275.
    item(at.button,'编辑高级参数').click().run()
    item(at.selectbox,'上备用需求方式').set_value('formula')
    item(at.button,'检查高级参数').click().run()
    assert not at.exception and not at.error
    assert not item(at.number_input,'上备用负荷比例').disabled
    item(at.button,'保存高级参数').click().run()
    assert not at.exception and not at.error
    assert load_config(root=scene).mode['reserve']['up_requirement_mode']=='formula'
