from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest


def archive():
    assets = [{"id":"C","name":"煤一","kind":"thermal"},{"id":"S","kind":"renewable"},
              {"id":"G","kind":"fixed"}]
    base = dict(power={"C":10.,"S":10.,"G":1.}, demand={1:21.}, online={"C":1},
                flows={"X":-50.}, thermal_reliability_reserve_mw={"C":4.})
    inputs = dict(assets=assets, lines=[{"id":"X","limit_mw":100.}],
                  available=[{"S":12.}]*2,fixed_schedule=[{"G":1.}]*2,
                  market_case={"resources":[{"id":"C","initial_mw":0.,"minimum_mw":2.,"ramp_mw_15min":8.}],
                               "reserve_mw":0.})
    da = [dict(base,quarter=t,forecast_stress_flows_mw={"X":-99.},forecast_activation_mw={"C":4.})
          for t in range(2)]
    rt = [dict(base,quarter=0,demand={1:23.},shed={1:2.},spill={1:0.},
               reliability_reserve_short_mw=0.,curtailed_mw={"S":1.})]
    return {"run":"example","da":da,"rt":rt,"inputs":inputs,"money":{"quarters":[{"quarter":0,"reliable":False}]}}


def test_grid_tables_preserve_direction_partial_coverage_and_startup_ramps(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/"app"))
    from sichuan_v5_dadu_grid_results import grid_tables
    main,coal,lines = grid_tables(archive())
    assert main.loc[0,"负荷偏差"] == 2.
    assert main.loc[0,"光伏可发偏差"] == -1.
    assert main.loc[0,"净负荷偏差（扣光伏可发）"] == 3.
    assert main.loc[0,"功率平衡残差"] == 0.
    assert main.loc[0,"气电计划偏差"] == 0.
    assert main.loc[0,"日前激活后最高 %"] == 99.
    assert lines.loc[0,"日前激活后 MW"] == -99.
    assert coal.loc[0,"实时状态"] == "启动"
    assert coal.loc[0,"爬坡使用率 %"] == pytest.approx(100.)
    assert pd.isna(main.loc[1,"实时负荷"])
    assert pd.isna(main.loc[1,"实时备用要求"])
    assert pd.isna(coal.loc[1,"实发 MW"])


def test_render_older_partial_archive_and_all_three_selectors(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/"app"))
    pack = archive()
    for d in pack["da"]:
        d.pop("forecast_stress_flows_mw")
        d.pop("forecast_activation_mw")
    app = AppTest.from_string(
        "from sichuan_v5_dadu_grid_results import render_grid_detail\n"
        f"render_grid_detail({pack!r}, 0)"
    ).run()
    assert not app.exception
    assert any("全网失供 2.0 MW" in r.value for r in app.warning)
    app.radio(key="grid_area_example").set_value("线路与备用").run()
    assert not app.exception
    assert any("没有备用网络激活记录" in r.value for r in app.caption)
    app.radio(key="grid_area_example").set_value("煤机与气电").run()
    assert not app.exception
    assert any("煤一" in row.value["机组"].tolist() for row in app.dataframe if "机组" in row.value)
