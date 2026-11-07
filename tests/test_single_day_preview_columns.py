"""Chinese display headers must not change scenario inputs or preview values."""
from copy import deepcopy
from pathlib import Path
import sys

import pandas as pd
from pandas.testing import assert_frame_equal
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))


HYDRO_HEADERS = {
    "电站": "电站", "季节": "季节", "qin": "来水（m³/s）",
    "v0": "初始库容（万m³）", "vmin": "最小库容（万m³）",
    "vmax": "最大库容（万m³）", "v_end": "日末库容下限（万m³）",
    "qeco": "生态流量（m³/s）", "e_day": "日电量上限（MWh）",
}
STAGE_HEADERS = {
    "stage": "边界阶段", "demand_peak_mw": "峰荷（MW）",
    "demand_mwh": "需求电量（MWh）", "renewable_mwh": "风光可用电量（MWh）",
    "renewable_available_to_demand_ratio": "风光可用电量/负荷（非实发）",
    "net_load_peak_mw": "净负荷峰值（MW）", "net_load_min_mw": "净负荷谷值（MW）",
    "net_ramp_max_abs_mw": "最大相邻净负荷变化（MW）",
    "optimistic_supply_energy_upper_mwh": "乐观供能上界（MWh）",
    "unavoidable_energy_shortfall_lower_mwh": "电量缺口下界（MWh）",
    "instantaneous_shortfall_lower_mw": "功率缺口下界（MW）",
    "energy_or_up_reserve_shortfall_lower_mw": "电能量或上备用缺口下界（MW）",
    "energy_or_up_reserve_deficient_periods": "电能量或上备用不足时段数",
}


def _preview_page(root):
    from pathlib import Path
    from types import SimpleNamespace
    from single_day_scenario import render_conditions

    cfg = SimpleNamespace(root=Path(root), base={"season": "wet", "date": "2026-06-01"},
                          T=96, dt=.25)
    render_conditions(cfg, "列名测试", bids={})


def test_adjustment_preview_chinese_headers_preserve_input_fields_and_values(tmp_path, monkeypatch):
    from smm import typical_scenarios

    hydro = [{"电站": "H1", "季节": "丰水期", "qin": 80., "v0": 1200.,
              "vmin": 600., "vmax": 2500., "v_end": 1100., "qeco": 12., "e_day": 3000.},
             {"电站": "H2", "季节": "丰水期", "qin": None, "v0": 500.,
              "vmin": 100., "vmax": 1000., "v_end": 400., "qeco": 6., "e_day": None}]
    stages = [dict(stage=stage, demand_peak_mw=1000.125, demand_mwh=20000.25,
                   renewable_mwh=8000.5, renewable_available_to_demand_ratio=.40002,
                   net_load_peak_mw=500.75, net_load_min_mw=-123.5, net_ramp_max_abs_mw=42.25,
                   optimistic_supply_energy_upper_mwh=30000., unavoidable_energy_shortfall_lower_mwh=0.,
                   instantaneous_shortfall_lower_mw=2.5, energy_or_up_reserve_shortfall_lower_mw=4.25,
                   energy_or_up_reserve_deficient_periods=2)
              for stage in ("actual", "da", "id", "rt")]
    preview = dict(summary=[{"对象": "负荷", "倍率": 1.2}], hydro=hydro,
                   assessment={"tier": "unverified_candidate", "stages": stages},
                   clipping=[], availability_changes=[])
    before = deepcopy(preview)
    monkeypatch.setattr(typical_scenarios, "prepare_typical", lambda *args, **kwargs: preview)
    at = AppTest.from_function(_preview_page, args=(str(tmp_path),)).run()
    assert not at.exception
    for multiplier in (1.2, .8):
        next(x for x in at.selectbox if x.label == "风电").set_value(multiplier).run()
        assert not at.exception
        details = next(x for x in at.expander if x.label == "查看调整明细")
        assert len(details.dataframe) == 3
        assert_frame_equal(details.dataframe[0].value, pd.DataFrame(before["summary"]))
        for displayed, source, headers in ((details.dataframe[1].value, hydro, HYDRO_HEADERS),
                                           (details.dataframe[2].value, stages, STAGE_HEADERS)):
            assert list(displayed.columns) == list(headers.values())
            restored = displayed.rename(columns={label: key for key, label in headers.items()})
            assert_frame_equal(restored, pd.DataFrame(source), check_exact=True)
        assert preview == before
    assert not list(tmp_path.iterdir())
