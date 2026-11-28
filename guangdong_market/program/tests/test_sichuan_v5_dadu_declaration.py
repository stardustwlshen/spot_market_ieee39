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
    for season,level,scenario in [('dry','normal','dry_peak'),('dry','extreme','dry_peak'),
                                  ('wet','extreme','wet_low'),('wet','normal','wet_low')]:
        app.radio(key='da_season').set_value(season)
        app.radio(key='da_error_level').set_value(level).run()
        assert app.query_params['scenario'] == season+'_'+level
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
    app.selectbox(key='da_op_station').set_value('D11').run()
    app.selectbox(key='da_op_kind').set_value('island').run()
    app.selectbox(key='da_op_first').set_value(48)
    app.selectbox(key='da_op_end').set_value(52)
    app.number_input(key='da_op_value_island_D11').set_value(80.)
    app.button(key='da_op_add').click().run()
    assert not app.exception and not app.error
    app.button(key='dadu_freeze').click().run()
    assert not backend.list_dadu(tmp_path) and not app.button(key='dadu_run').disabled
    app.radio(key='da_error_level').set_value('extreme').run()
    assert not app.button(key='dadu_run').disabled
    app.radio(key='da_error_level').set_value('normal').run()
    app.button(key='dadu_run').click().run()
    assert not app.exception and not app.error
    from sichuan_v5_dadu_da import list_days, read_day
    name, = list_days(tmp_path)
    archive = read_day(tmp_path,name)
    assert archive['scenario']=='wet_normal'
    assert archive['margin']['da_extra_up_mw']==230
    assert archive['company']['base_declaration_sha256']==sheet['declaration_sha256']
    assert archive['company']['operations']['entries']==[dict(station='D11',first=48,last=51,kind='island',value=80.)]
    assert archive['audit']['island_energy_mwh']==pytest.approx(80.)
    assert archive['audit']['max_flow_error_m3s'] < 1e-4
    from sichuan_v5_dadu_da_view import pump_rows
    from sichuan_v5_pumped_storage import build_day_case,audit_pumped
    from copy import deepcopy
    market,_ = build_day_case('wet_low')
    detail = pump_rows(archive)
    assert len(detail)==96
    assert all(abs(r['净注入 MW']-r['日前净计划 MW'])<1e-5 for r in detail)
    assert all(r['SCUC 抽水 MW']==r['SCED 抽水 MW'] and r['SCUC 发电 MW']==r['SCED 发电 MW'] for r in detail)
    assert archive['audit']['pumped_storage']==dict(pumping_mwh=4800.,generation_mwh=3600.,cycle_loss_mwh=1200.,
                                                   terminal_mwh=3600.,max_inventory_error_mwh=0.)
    assert 'PSH1' not in archive['company']['offers'] and 'PSH1' not in archive['raw_offers']
    assert all('PSH1' not in row['hydro'] for row in archive['da'])
    damaged = deepcopy(archive['da'])
    damaged[0]['energy']['PSH1'] += 1
    with pytest.raises(ValueError,match='库存'):
        audit_pumped(market,damaged)
    from sichuan_v5_dadu_da_view import tables
    water,_ = tables(archive,48)
    island = next(s for s in water if s['电站']=='深溪沟')
    assert island['孤网计划 MW']==80. and island['日前并网 MW']==pytest.approx(0.)
    level = next(s['折算段末水位 m'] for s in water if s['电站']=='瀑布沟')
    assert 790. <= level <= 850.

    for t in range(48,52):
        assert archive['da'][t]['power']['D11']==pytest.approx(0.)
        assert archive['scuc'][t]['power']['D11']==pytest.approx(0.)
        assert archive['da'][t]['hydro']['D11']['turbine_m3s']==pytest.approx(80.*sheet['offers']['D11']['consumption_m3_kwh']/3.6)

    assert len(archive['da'])==len(archive['scuc'])==96
    assert 'rt' not in archive and 'money' not in archive and not backend.list_dadu(tmp_path)
    assert archive['audit']['max_slack_mw'] < 1e-5
    assert len(archive['audit']['terminal_transit']) == 18
    assert app.radio(key='dadu_workspace').value == '日前预出清'
    assert app.multiselect(key='da_compare').value == ['瀑布沟','深溪沟']
    app.multiselect(key='da_compare').set_value(['双江口','安谷']).run()
    assert not app.exception and not app.error
    assert len(list_days(tmp_path)) == 1


@pytest.mark.parametrize('season,expected', [('wet',(230,670)),('dry',(250,750))])
def test_forecast_grade_changes_only_the_market_margin(monkeypatch,season,expected):
    monkeypatch.syspath_prepend(str(PROGRAM/'app'))
    from dataclasses import replace
    from sichuan_v5_dadu_da import NET_ERROR_SIGMA, SEASON_TEMPLATE
    from sichuan_v5_dadu_case import build_case
    from sichuan_v5_dadu_run import _uncertainty_boundary
    case,_ = build_case(SEASON_TEMPLATE[season],39)
    for level,mw in zip(('normal','extreme'),expected):
        market, note = _uncertainty_boundary(case,season+'_'+level,error_sigma=NET_ERROR_SIGMA[level])
        assert market==replace(case,reserve_mw=case.reserve_mw+mw,deliverable_up_mw=mw)
        assert note['da_extra_up_mw']==mw


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



def test_special_requirements_freeze_validation(tmp_path,monkeypatch):
    monkeypatch.syspath_prepend(str(PROGRAM/'app'))
    from sichuan_v5_dadu_case import build_case
    from sichuan_v5_dadu_run import default_declaration,freeze_declaration
    from sichuan_v5_dadu_operations import DayOperations,with_operations
    from sichuan_v5_dadu_da import run_day,review_filing
    case,meta = build_case('wet_low',39)
    base = default_declaration(case,meta)
    t = 48
    def entry(uid,kind,x):
        return dict(station=uid,first=t,last=t,kind=kind,value=x)
    offer = base['offers']['D10']
    fixed = (offer['minimum_mw'][t]+offer['available_mw'][t])/2
    entries = [entry('D10','fixed',fixed),entry('D10','level_min',800.),entry('D11','offline',0.)]
    company = with_operations(base,entries)
    ops = DayOperations(company,meta['stations'])
    bounds = review_filing(case,company,ops)
    assert bounds[t]['D10']==(fixed,fixed)
    assert bounds[t]['D11']==(0.,0.)
    assert bounds[t-1]['D11'][1]>0.
    assert ops.slots['D10',t]['level_min_m3']>0
    for wrong in ([entry('D11','level_min',605.)],[entry('D10','maximum',1e8),entry('D10','offline',0.)],
                  [entry('D11','island',1e8)],entries+[entries[0]]):
        with pytest.raises(ValueError):
            DayOperations(with_operations(base,wrong),meta['stations'])
    assert company['declaration_sha256']==with_operations(base,list(reversed(entries)))['declaration_sha256']
    frozen = freeze_declaration(tmp_path,company)
    frozen.write_bytes(frozen.read_bytes()+b' ')
    with pytest.raises(ValueError,match='校验失败'):
        run_day(tmp_path,scenario='wet_normal',declaration_sha256=company['declaration_sha256'])
