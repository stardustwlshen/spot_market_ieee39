"""Advanced floats display useful precision without changing stored doubles."""
import pytest
import yaml
from streamlit.testing.v1 import AppTest

from smm.advanced_parameters import Field
from smm.config import load_config
from test_unit_editor import scene,input_bytes
from test_advanced_parameters_ui import app,item


@pytest.mark.parametrize('optional',[False,True])
@pytest.mark.parametrize('value',[.68,.025,.026,.1,1.2,1e-6,-.025])
def test_float_widget_has_precise_compact_display_format(optional,value):
    at=AppTest.from_string(f'''
from smm.advanced_parameters import Field
from advanced_parameters import _widget
_widget(Field('mode.test','test','精度测试','test',optional={optional!r}),{value!r},'precision',False)
''',default_timeout=30).run()
    assert not at.exception
    number=at.number_input[0]
    assert number.proto.format=='%.15g'
    assert float(number.proto.format % number.value)==value
    assert number.value==value
    assert number.proto.format % number.value in {'0.68','0.025','0.026','0.1','1.2','1e-06','-0.025'}


def test_integer_widget_remains_integer():
    at=AppTest.from_string('''
from smm.advanced_parameters import Field
from advanced_parameters import _widget
_widget(Field('base.seed','test','种子','test',kind='integer'),20260715,'precision',False)
''',default_timeout=30).run()
    assert not at.exception
    assert at.number_input[0].proto.format=='%d'
    assert type(at.number_input[0].value) is int
    assert at.number_input[0].value==20260715


def precise_scene(scene,load=.025,limit=-.025):
    path=scene/'config/base.yaml';base=yaml.safe_load(path.read_bytes())
    base['load_scale']=load
    path.write_text(yaml.safe_dump(base))
    path=scene/'config/modes.yaml';modes=yaml.safe_load(path.read_bytes())
    modes['MKT']['bid_rules'].setdefault('by_type',{}).setdefault('coal',{})['price_min']=limit
    path.write_text(yaml.safe_dump(modes))


@pytest.mark.parametrize('value',[.025,.12345678901234567])
def test_noop_save_preserves_exact_input_bytes_and_optional_precision(scene,value):
    precise_scene(scene,load=value,limit=-value)
    before=input_bytes(scene)
    at=app(scene);item(at.button,'编辑高级参数').click().run()
    for label,expected in [('区内负荷倍率',value),('煤电覆盖下限（元/MWh）',-value)]:
        number=item(at.number_input,label)
        assert number.proto.format=='%.15g'
        assert number.value==expected
        assert float(number.proto.format % number.value)==pytest.approx(expected,rel=1e-14)
    item(at.button,'保存高级参数').click().run()
    assert not at.exception and not at.error
    assert input_bytes(scene)==before


def test_changed_precise_float_persists_and_reopens_without_numeric_rounding(scene):
    precise_scene(scene)
    at=app(scene);item(at.button,'编辑高级参数').click().run()
    item(at.number_input,'区内负荷倍率').set_value(.026)
    item(at.number_input,'煤电覆盖下限（元/MWh）').set_value(-.026)
    item(at.button,'保存高级参数').click().run()
    assert not at.exception and not at.error
    cfg=load_config(root=scene)
    assert cfg.base['load_scale']==.026
    assert cfg.mode['bid_rules']['by_type']['coal']['price_min']==-.026
    saved=input_bytes(scene)
    at=app(scene);item(at.button,'编辑高级参数').click().run()
    for label,value in [('区内负荷倍率',.026),('煤电覆盖下限（元/MWh）',-.026)]:
        number=item(at.number_input,label)
        assert number.value==value
        assert float(number.proto.format % number.value)==value
    item(at.button,'保存高级参数').click().run()
    assert not at.exception and not at.error
    assert input_bytes(scene)==saved
