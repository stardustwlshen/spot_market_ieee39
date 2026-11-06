from pathlib import Path
from streamlit.testing.v1 import AppTest
from smm import spot_costs as c
from smm.config import load_config
from test_spot_costs import scene


def app(root,disabled=False):
    return AppTest.from_string(f'''
from pathlib import Path
from smm.config import load_config
from spot_cost_editor import render_spot_cost_editor
render_spot_cost_editor(load_config(root=Path({str(root)!r})),'C1',disabled={disabled!r})
''',default_timeout=30).run()


def item(items,label):return next(x for x in items if x.label==label)


def test_actual_form_check_save_reload_cancel_noop(tmp_path):
    cfg=scene(tmp_path);before=(tmp_path/'config'/c.FILENAME).read_bytes()
    at=app(tmp_path);assert not at.exception and not at.error
    item(at.button,'保存现货成本').click().run();assert not at.exception and not at.error
    assert (tmp_path/'config'/c.FILENAME).read_bytes()==before
    item(at.number_input,'燃料发电成本（元/MWh）').set_value(270.025)
    item(at.button,'检查成本').click().run();assert not at.error and (tmp_path/'config'/c.FILENAME).read_bytes()==before
    item(at.button,'保存现货成本').click().run();assert not at.exception and not at.error
    assert c.load_profile(cfg)['units']['C1']['fuel_cost_yuan_per_mwh']==270.025
    at=app(tmp_path);assert item(at.number_input,'燃料发电成本（元/MWh）').value==270.025
    before=(tmp_path/'config'/c.FILENAME).read_bytes()
    item(at.number_input,'燃料发电成本（元/MWh）').set_value(500)
    item(at.button,'取消成本编辑').click().run();assert (tmp_path/'config'/c.FILENAME).read_bytes()==before


def test_missing_read_no_write_explicit_adoption(tmp_path):
    scene(tmp_path);(tmp_path/'config'/c.FILENAME).unlink()
    at=app(tmp_path);assert not at.exception and not (tmp_path/'config'/c.FILENAME).exists()
    item(at.button,'检查成本').click().run();assert at.error and not (tmp_path/'config'/c.FILENAME).exists()
    item(at.button,'采用本机组现货研究成本').click().run();assert not at.exception and not at.error
    assert set(c.load_profile(load_config(root=tmp_path))['units'])=={'C1'}


def test_disabled_and_concurrent_rejection(tmp_path):
    scene(tmp_path);at=app(tmp_path,True)
    assert item(at.button,'保存现货成本').disabled
    at=app(tmp_path);p=tmp_path/'config'/c.FILENAME
    p.write_bytes(p.read_bytes()+b'\n# another editor\n');before=p.read_bytes()
    item(at.number_input,'燃料发电成本（元/MWh）').set_value(500)
    item(at.button,'保存现货成本').click().run()
    assert not at.exception and at.error and p.read_bytes()==before


def test_independent_manual_editor_survives_unusable_legacy_costs(tmp_path):
    scene(tmp_path);(tmp_path/'config/fixed_costs.yaml').write_text('[broken yaml')
    at=app(tmp_path)
    assert not at.exception and not at.error
    item(at.number_input,'燃料发电成本（元/MWh）').set_value(270.125)
    item(at.button,'保存现货成本').click().run()
    assert not at.exception and not at.error
    assert c.load_profile(load_config(root=tmp_path))['units']['C1']['fuel_cost_yuan_per_mwh']==270.125
