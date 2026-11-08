"""Read-only result presentation regression checks."""
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest
from dispatch_detail import dispatch_with_prices


def test_dispatch_prices_match_both_node_and_period_without_mutating():
    units = pd.DataFrame(dict(unit=['A','B','A'], bus=[1,2,1], period=[0,0,1], P_mw=[1.,2.,3.]))
    prices = pd.DataFrame(dict(bus=[2,1,1], period=[0,1,0], lmp=[200.,110.,100.]))
    original = units.copy(deep=True)
    output = dispatch_with_prices(units, prices, '日前')
    added = list(set(output)-set(units))
    assert len(added) == 1
    assert output[added[0]].tolist() == [100.,200.,110.]
    pd.testing.assert_frame_equal(units, original)
    with pytest.raises(ValueError):
        dispatch_with_prices(units, pd.concat([prices, prices.iloc[:1]]), '日前')


def test_dispatch_missing_prices_remain_unknown():
    units = pd.DataFrame(dict(unit=['A'], bus=[1], period=[0], P_mw=[1.]))
    output = dispatch_with_prices(units, pd.DataFrame(), '实时')
    assert output[list(set(output)-set(units))].isna().all().all()


def test_absent_forecast_diagnostics_show_no_empty_section(tmp_path):
    def app():
        import streamlit as st
        from forecast_error_view import render_forecast_diagnostics
        render_forecast_diagnostics(st.session_state['archive'])
    at = AppTest.from_function(app)
    at.session_state['archive'] = str(tmp_path)
    at.run()
    assert not at.exception
    assert not at.expander
    assert not at.info


def test_real_saved_economics_units_and_repeated_phase_selection():
    import os
    from pathlib import Path
    import hashlib
    import common
    archive = os.environ.get('IEEE39_SAVED_RUN_FOR_UI')
    if not archive:
        pytest.skip('saved archive unavailable')
    root=Path(archive)
    def hashes():
        return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    before=hashes()
    result=common.get_result(dict(dir=archive, mode='full', status='done', scenario='read-only'))
    def app():
        import streamlit as st
        from result_economics_view import render_economics
        render_economics(st.session_state['result'], prefix='qa')
    at=AppTest.from_function(app, default_timeout=90)
    at.session_state['result']=result
    at.run()
    assert not at.exception
    assert not at.get("download_button")
    paired=at.dataframe[0].value
    assert {'日前收入 元','日前成本 元','日前利润 元','实际收入 元','实际成本 元','实际利润 元'} <= set(paired)
    from smm.spot_costs import KINDS
    assert len(paired)==result['da_unit_awards'].loc[result['da_unit_awards'].type.isin(KINDS),'unit'].nunique()
    for typ in ('coal','storage','pumped','hydro','wind'):
        units=result['da_unit_awards'].loc[result['da_unit_awards'].type.eq(typ),'unit']
        if units.empty:
            continue
        at.selectbox(key='qa:unit').set_value(units.iloc[0]).run()
        for phase in ('rt','da','rt'):
            at.radio(key='qa:phase').set_value(phase).run()
            assert not at.exception
    assert hashes()==before
