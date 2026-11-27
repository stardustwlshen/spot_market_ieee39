"""The planning preview and the execution cursor may move independently on a short run."""
import json
import os
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

PROGRAM = Path(__file__).resolve().parents[1]


def test_short_run_routes_only_planning_tabs_to_preview_and_keeps_execution_focus(monkeypatch):
    monkeypatch.syspath_prepend(str(PROGRAM / "app"))
    import sichuan_v5_dadu_view as view
    import sichuan_v5_dadu_basin as basin
    import sichuan_v5_dadu_bid_decision as decision
    import sichuan_v5_dadu_company_results as company
    import sichuan_v5_dadu_grid_results as grid
    import sichuan_v5_dadu_market_stages as stages
    import sichuan_v5_dadu_overview as overview
    import sichuan_v5_dadu_portfolio as portfolio
    import sichuan_v5_dadu_water_results as water

    def probes(module, labels):
        for func, label in labels.items():
            def probe(*args, _label=label):
                st.metric(_label, args[-1] + 1)
            monkeypatch.setattr(module, func, probe)

    probes(portfolio, {"render_company_portfolio": "组合计划"})
    probes(decision, {"render_bid_decision": "单站报价"})
    probes(stages, {"render_day_ahead": "日前阶段", "render_real_time": "实时阶段"})
    probes(view, {"_market_tab": "资源报价", "_realtime_tab": "本站实发"})
    probes(overview, {"render_node_prices": "实时节点价对照", "render_pair": "成交对照"})
    probes(basin, {"render_basin": "流域水账"})
    probes(water, {"render_water_detail": "本站水账"})
    probes(grid, {"render_grid_detail": "系统调度"})
    probes(company, {"render_company_detail": "站级收入"})
    for module, func in ((view, "_company_tab"), (overview, "render_overview"), (company, "render_cash_detail")):
        monkeypatch.setattr(module, func, lambda *args: None)

    app = AppTest.from_string(
        "import streamlit as st\nfrom pathlib import Path\nfrom sichuan_v5_dadu_view import _results\n"
        "name=st.selectbox('归档', ('short','full'), key='archive')\n"
        "n=4 if name=='short' else 96\n"
        "station=dict(id='D10',name='示例站',model='run_of_river')\n"
        "r=dict(inputs=dict(stations=[station],scenario='wet_low',seed=39),"
        "company=dict(strategy='water_value',internal_plan_mw=[{}]*96),policy={},summary={},"
        "da=[dict(raw_nodal_prices={str(b):q+b for b in range(1,40)}) for q in range(96)],"
        "rt=[dict(quarter=t,shed={1:0}) for t in range(n)])\n"
        "_results(name,r,Path('.'),{}, {})", default_timeout=20
    ).run()
    planning = {"组合计划", "单站报价", "日前阶段", "资源报价"}
    execution = {"实时阶段", "本站实发", "实时节点价对照", "系统调度", "流域水账", "本站水账", "站级收入", "成交对照"}

    def check(planned, actual):
        assert not app.exception and not app.error
        metrics = {m.label:int(m.value) for m in app.metric}
        assert all(metrics[k] == planned for k in planning)
        assert all(metrics[k] == actual for k in execution)
        prices = app.dataframe[0].value
        assert list(prices) == ["节点", "日前节点参考价（元/MWh）"]
        assert len(prices) == 39 and prices.iloc[0, 1] == planned

    check(1, 1)
    app.selectbox(key="dadu_q_short").select(2).run()
    app.toggle(key="dadu_day_preview_short").set_value(True).run()
    selector = app.selectbox(key="dadu_day_q_short")
    assert len(selector.options) == 96 and selector.value == 2
    selector.select(95).run()
    check(96, 3)
    app.selectbox(key="dadu_q_short").select(3).run()
    check(96, 4)
    app.toggle(key="dadu_day_preview_short").set_value(False).run()
    check(4, 4)
    app.selectbox(key="archive").select("full").run()
    assert not app.toggle
    app.selectbox(key="dadu_q_full").select(78).run()
    check(79, 79)
    app.selectbox(key="archive").select("short").run()
    check(4, 4)
    assert len(app.selectbox(key="dadu_q_short").options) == 4


def test_real_short_archive_shows_last_day_ahead_period_and_preserves_first_execution(monkeypatch):
    location = os.environ.get("DADU_NAVIGATION_SHORT_RUNS")
    if not location:
        pytest.skip("Set DADU_NAVIGATION_SHORT_RUNS to a directory containing a short Dadu archive")
    monkeypatch.syspath_prepend(str(PROGRAM / "app"))
    from sichuan_v5_dadu_run import list_dadu, read_dadu

    root = Path(location)
    name = next(n for n in list_dadu(root) if
                json.loads((root / "dadu19" / n / "receipt.json").read_bytes())["periods"] < 96)
    r = read_dadu(root, name)
    app = AppTest.from_string(
        "from pathlib import Path\nfrom sichuan_v5_dadu_view import _results\n"
        "import streamlit as st\n"
        f"_results({name!r},st.session_state['result'],Path({str(root)!r}),{{}},{{}})",
        default_timeout=40
    )
    app.session_state["result"] = r
    app.run()
    app.toggle(key="dadu_day_preview_" + name).set_value(True).run()
    app.selectbox(key="dadu_day_q_" + name).select(95).run()
    assert not app.exception and not app.error
    assert app.selectbox(key="dadu_q_" + name).value == 0
    metrics = {m.label:m.value for m in app.metric}
    assert metrics["瀑布沟预出清"] == f"{r['da'][95]['power']['D10']:,.2f} MW"
    assert metrics["瀑布沟实发"] == f"{r['rt'][0]['power']['D10']:,.2f} MW"
    stations = next(d.value for d in app.dataframe if "当段内部计划 MW" in d.value)
    assert stations.set_index("电站").loc["瀑布沟","当段内部计划 MW"] == r["company"]["internal_plan_mw"][95]["D10"]
    if r['company']['strategy'] == 'default':
        assert not any("为何这样报" in m.value for m in app.markdown)
    else:
        assert any("23:45–24:00 为何这样报" in m.value for m in app.markdown)
