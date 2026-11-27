from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

PROGRAM = Path(__file__).resolve().parents[1]


def test_submission_workspace(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(PROGRAM/'app'))
    import market_rule_profile as profiles
    import sichuan_v5_dadu_run as backend
    monkeypatch.setattr(profiles.MarketProfile, 'default_results', lambda self,_:tmp_path)
    def unexpected(*args, **kwargs):
        pytest.fail("The default submission must never run a company optimizer")
    monkeypatch.setattr(backend,'prepare_company_submission',unexpected)
    app = AppTest.from_file(str(PROGRAM/'app/四川.py'), default_timeout=240).run()
    assert not app.exception and not app.error
    for scenario in ('dry_peak','forecast_error','wet_low'):
        app.selectbox(key='dadu_scenario').select(scenario).run()
        assert not app.exception and not app.error
        assert not app.multiselect and not backend.list_dadu(tmp_path)
        from sichuan_v5_dadu_view import _load_sheet
        sheet = _load_sheet(scenario)
        assert len(sheet['company']['offers']) == 19
        assert all(len(o['available_mw']) == len(o['minimum_mw']) == len(o['natural_inflow_m3s']) == 96
                   for o in sheet['company']['offers'].values())
        assert app.button(key='dadu_freeze')
    case, meta = backend.build_case('wet_low',39)
    sheet = backend.default_declaration(case,meta)
    with pytest.raises(ValueError, match='已确认|确认的'):
        backend.run_dadu(tmp_path,declaration_sha256='mismatch',periods=4)
    assert app.button(key='dadu_run').disabled
    app.button(key='dadu_freeze').click().run()
    assert not backend.list_dadu(tmp_path) and not app.button(key='dadu_run').disabled
    app.button(key='dadu_run').click().run()
    assert not app.exception and not app.error
    name, = backend.list_dadu(tmp_path)
    archive = backend.read_dadu(tmp_path,name)
    assert archive['company']['declaration_sha256']==sheet['declaration_sha256']
    assert len(archive['da'])==96 and len(archive['rt'])==4
    assert len(sheet['hydraulic_declaration']['reaches'])==18
    assert len(sheet['hydraulic_declaration']['reservoirs'])==19
    assert all(archive['da'][q]['hydro'] and archive['rt'][q]['water_handoff'] for q in range(4))
    app.radio(key='dadu_workspace').set_value('市场结果').run()
    assert not app.exception and not app.error
