from copy import deepcopy

from smm.config import load_config
from smm.unit_editor import catalog, prepare_unit_edit, apply_unit_edit
from smm import hydro_commitment as hc
from test_unit_editor import scene, input_bytes
from test_unit_editor_ui import app, item


def test_ui_enable_save_duration_and_reopen(scene):
    # Opt-in migration starts from an old scene; new defaults are already enabled.
    import yaml
    path=scene/'config/units.yaml';units=yaml.safe_load(path.read_text())
    for h in units['hydro']:h.pop('commitment',None)
    path.write_text(yaml.safe_dump(units,allow_unicode=True))
    at=app(scene)
    item(at.selectbox,'要编辑的机组').set_value('H1').run()
    item(at.button,'核对技术参数').click().run()
    assert item(at.number_input,'最小开机时间（h）').disabled
    before=input_bytes(scene)
    item(at.checkbox,'启用逐台最小开停时间').check().run()
    assert not at.exception
    assert not item(at.number_input,'最小开机时间（h）').disabled
    item(at.number_input,'最小开机时间（h）').set_value(.6)
    item(at.number_input,'最小停机时间（h）').set_value(.3)
    item(at.button,'检查参数').click().run()
    assert not at.exception and not at.error
    assert input_bytes(scene)==before
    item(at.button,'保存机组').click().run()
    assert not at.exception and not at.error
    cfg=load_config(root=scene);h=next(u for u in cfg.units['hydro'] if u['id']=='H1')
    assert hc.enabled(h) and h['ut']==.6 and h['dt']==.3
    assert len(h['commitment']['initial_units'])==4
    item(at.selectbox,'要编辑的机组').set_value('H1').run()
    item(at.button,'核对技术参数').click().run()
    assert not at.exception
    assert item(at.number_input,'最小开机时间（h）').value==.6
    item(at.button,'取消编辑').click().run()
    assert load_config(root=scene).units['hydro'][0]['ut']==.6


def test_editable_initial_age_is_saved_and_changes_residual(scene):
    h=next(deepcopy(u) for group,u in catalog(scene) if u['id']=='H1')
    h['commitment']=hc.research_initial(h)
    h['ut']=1.;h['commitment']['initial_units'][0]['elapsed_h']=.2
    apply_unit_edit(prepare_unit_edit(scene,'hydro',h,original_id='H1'))
    cfg=load_config(root=scene);state=hc.initial_state(cfg)
    row=state['stations']['H1']['units']['H1_U01']
    assert row['elapsed_h']==.2
    assert (hc.seconds(row['required_until'])-hc.seconds(state['state_at']))==hc.number(.8,'residual')*3600
    saved=input_bytes(scene)
    h['commitment']['initial_units'][0]['elapsed_h']=-1
    try:prepare_unit_edit(scene,'hydro',h,original_id='H1')
    except ValueError:pass
    else:raise AssertionError('invalid initial age was accepted')
    assert input_bytes(scene)==saved


def test_ui_initial_duration_table_save_consumes_edited_cell(scene):
    at=app(scene)
    item(at.selectbox,'要编辑的机组').set_value('H1').run()
    item(at.button,'核对技术参数').click().run()
    item(at.checkbox,'启用逐台最小开停时间').check().run()
    key='unit_editor:测试:fields:0:hydro_initial_units'
    at.session_state[key]={'edited_rows':{0:{'elapsed_h':.2}},'added_rows':[],'deleted_rows':[]}
    item(at.button,'保存机组').click().run()
    assert not at.exception and not at.error
    cfg=load_config(root=scene)
    assert hc.initial_state(cfg)['stations']['H1']['units']['H1_U01']['elapsed_h']==.2


def test_thermal_fractional_hours_survive_save_and_reopen(scene):
    at=app(scene)
    item(at.selectbox,'要编辑的机组').set_value('C1').run()
    item(at.button,'核对技术参数').click().run()
    item(at.number_input,'最小开机时间（h）').set_value(1.5)
    item(at.number_input,'最小停机时间（h）').set_value(.25)
    item(at.number_input,'初始状态 / 已持续时间（h）').set_value(.25)
    item(at.button,'保存机组').click().run()
    assert not at.exception and not at.error
    cfg=load_config(root=scene);g=next(g for g in cfg.units['thermal'] if g['id']=='C1')
    assert g['ut']==1.5 and g['dt']==.25 and g['init']['hours']==.25
    item(at.selectbox,'要编辑的机组').set_value('C1').run()
    item(at.button,'核对技术参数').click().run()
    assert item(at.number_input,'初始状态 / 已持续时间（h）').value==.25
    assert item(at.number_input,'最小开机时间（h）').value==1.5
    item(at.button,'取消编辑').click().run()
