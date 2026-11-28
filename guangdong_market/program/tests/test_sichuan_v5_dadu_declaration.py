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
        from sichuan_v5_dadu_view import _load_sheet
        sheet = _load_sheet(scenario)
        assert len(sheet['company']['offers']) == 19
        assert all(len(o['available_mw']) == len(o['minimum_mw']) == len(o['natural_inflow_m3s']) == 96
                   for o in sheet['company']['offers'].values())
        assert app.button(key='dadu_freeze')
    case, meta = backend.build_case('wet_low',39)
    sheet = backend.default_declaration(case,meta)
    from sichuan_v5_dadu_da import run_day
    with pytest.raises(ValueError, match='已冻结申报'):
        run_day(tmp_path,scenario='wet_low',declaration_sha256='mismatch')
    assert app.button(key='dadu_run').disabled
    app.button(key='dadu_freeze').click().run()
    assert not backend.list_dadu(tmp_path) and not app.button(key='dadu_run').disabled
    app.button(key='dadu_run').click().run()
    assert not app.exception and not app.error
    from sichuan_v5_dadu_da import list_days, read_day
    name, = list_days(tmp_path)
    archive = read_day(tmp_path,name)
    assert archive['company']['declaration_sha256']==sheet['declaration_sha256']
    assert len(archive['da'])==len(archive['scuc'])==96
    assert 'rt' not in archive and 'money' not in archive and not backend.list_dadu(tmp_path)
    assert archive['audit']['max_slack_mw'] < 1e-5
    assert len(archive['audit']['terminal_transit']) == 18
    assert app.radio(key='dadu_workspace').value == '日前预出清'
    assert app.multiselect(key='da_compare').value == ['瀑布沟','深溪沟']
    app.multiselect(key='da_compare').set_value(['双江口','安谷']).run()
    assert not app.exception and not app.error
    assert len(list_days(tmp_path)) == 1


def test_reviewed_filing_and_ancillary_limits_bind_both_stages():
    from app.sichuan_v5_energy import Case, ClearingError, Resource, day_ahead
    hydro = Resource('H',1,'provincial_hydro','hydro',100.,1.)
    competitor = Resource('B',1,'central_renewable','renewable',100.,20.)
    case = Case((1,),(),(hydro,competitor),({1:70.},)*96,({'H':100.,'B':100.},)*96)
    scuc = []
    bounds = ({'H':(10.,40.)},)*96
    rows = day_ahead(case,hydro_bounds=bounds,holds=({'H':(5.,2.)},)*96,on_scuc_dispatch=scuc.extend)
    assert len(scuc)==len(rows)==96
    assert all(r['power']['H']==pytest.approx(35.) for r in scuc+rows)
    assert all(r['raw_nodal_prices'][1]==pytest.approx(20.) for r in rows)
    with pytest.raises(ClearingError,match='占位'):
        day_ahead(case,hydro_bounds=bounds,holds=({'H':(35.,2.)},)*96)

