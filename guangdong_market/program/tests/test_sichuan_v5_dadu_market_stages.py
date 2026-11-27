import gzip
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


PROGRAM = Path(__file__).resolve().parents[1]
RUNS = PROGRAM / "runs_sichuan_v5_consultation" / "dadu19"


@pytest.fixture
def archive(monkeypatch):
    monkeypatch.syspath_prepend(str(PROGRAM / "app"))
    for receipt in sorted(RUNS.glob("*/receipt.json")):
        options = json.loads(receipt.read_text(encoding="utf-8"))
        if (options["scenario"], options["strategy"], options["periods"]) == ("forecast_error", "naive", 96):
            with gzip.open(receipt.with_name("result.json.gz"), "rt", encoding="utf-8") as stream:
                return json.load(stream)
    pytest.fail("缺少用于逐段核对的预测偏差朴素报价完整归档")


def test_day_ahead_uses_recorded_commitment_and_priced_dispatch(archive):
    from sichuan_v5_dadu_market_stages import _commitment, _group_rows
    q = 78
    row = archive["da"][q]
    assets = archive["inputs"]["assets"]
    coal = [a for a in assets if a["kind"] == "thermal"]
    text = _commitment(archive["inputs"], row)
    for state, label in ((1, "开机"), (0, "停机")):
        names = [a["name"] for a in coal if row["online"][a["id"]] == state]
        if names:
            assert label + " " + "、".join(names) in text.split("。")
    groups = {a["电源"]: a for a in _group_rows(archive, q, row, {}, False)}
    assert groups["水电"]["设计装机 MW"] == 22056
    assert groups["煤电"]["日前预出清 MW"] == pytest.approx(sum(row["power"][a["id"]] for a in coal))
    assert groups["气电"]["有效报价范围 元/MWh"] == "计划"
    gas = sum(archive["inputs"]["fixed_schedule"][q].values())
    assert groups["气电"]["气电保供计划 MW"] == pytest.approx(gas)
    assert groups["气电"]["日前预出清 MW"] == pytest.approx(gas)
    assert groups["集中式光伏"]["有效报价范围 元/MWh"] == "0"


def test_app_aligns_first_executed_quarter_and_withholds_unverified_price(archive):
    q = max(range(96), key=lambda t:sum(archive["rt"][t]["shed"].values()))
    row, planned = archive["rt"][q], archive["da"][q]
    assert sum(row["shed"].values()) > 1 and not archive["money"]["quarters"][q]["reliable"]
    stage = AppTest.from_string(
        "import streamlit as st\n"
        "from sichuan_v5_dadu_market_stages import render_day_ahead, render_real_time\n"
        "r, q = st.session_state['a'], st.session_state['q']\n"
        "render_day_ahead(r, 'D10', q)\nrender_real_time(r, 'D10', q)", default_timeout=15
    )
    stage.session_state["a"], stage.session_state["q"] = archive, q
    stage.run()
    assert not stage.exception
    assert [m.value for m in stage.markdown if m.value.startswith("**")] == [
        "**日前 SCUC · 煤机开停**", "**日前 SCED · 固定煤机组合后预出清**", "**实时滚动 SCED · 每次执行首段**"]
    metrics = {m.label: m for m in stage.metric}
    assert metrics["瀑布沟预出清"].value == f"{planned['power']['D10']:,.2f} MW"
    assert metrics["瀑布沟实发"].value == f"{row['power']['D10']:,.2f} MW"
    assert any("实时采用价" in k and m.value == "待核" for k, m in metrics.items())
    assert any("失负荷" in w.value and "收入暂停" in w.value for w in stage.warning)
    assert any("（8 段）" in c.value for c in stage.caption)
    stage.session_state["q"] = 95
    stage.run()
    assert not stage.exception
    assert any("23:45–24:00（1 段）" in c.value and "收盘" in c.value for c in stage.caption)


def test_sparse_archive_monitors_only_executed_rows_and_unknown_source_fields(monkeypatch):
    monkeypatch.syspath_prepend(str(PROGRAM / "app"))
    from sichuan_v5_dadu_market_stages import _commitment, _group_rows, _price_range
    assets = [dict(id="A", name="甲", kind="thermal"), dict(id="B", name="乙", kind="thermal")]
    r = {"inputs": {"assets": assets}, "da": [{"quarter": 1}], "rt": []}
    assert _commitment(r["inputs"], {"online":{"B":0}}) == "停机 乙。状态待核 甲"
    assert _price_range({"A": [[10, 0]]}, ["A", "B"]) == "待核"
    assert _price_range({"A": [[10, 0], [20, 45]]}, ["A"]) == "0–45"
    group = _group_rows(r, 1, {"power":{"A":2}}, {}, False)[0]
    assert group["日前预出清 MW"] is None
    app = AppTest.from_string(
        "from sichuan_v5_dadu_market_stages import render_real_time\n"
        f"render_real_time({r!r}, 'A', 1)"
    ).run()
    assert not app.exception and "尚无实时执行记录" in app.info[0].value
    assert not app.metric
