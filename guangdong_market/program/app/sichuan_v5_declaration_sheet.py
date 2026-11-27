"""Interactive basin filing viewer. The client receives only the published filing."""
from pathlib import Path

import streamlit as st


ASSETS = Path(__file__).with_name("dadu_sheet")
atlas = st.components.v2.component(
    "dadu_declaration_atlas",
    html='<div class="atlas"></div>',
    css=(ASSETS/"atlas.css").read_text(encoding="utf-8"),
    js=(ASSETS/"atlas.js").read_text(encoding="utf-8"),
)


def render_sheet(sheet, scenario):
    company = sheet["company"]
    reaches = company["hydraulic_declaration"]["reaches"]
    stations = []
    for station in sheet["inputs"]["stations"]:
        uid = station["id"]
        offer = company["offers"][uid]
        stations.append(dict(id=uid, name=station["name"], kind=station["model"],
                             capacity=station["capacity_mw"], bus=station["bus"],
                             peak=offer["sustained_3h_mw"], start=offer["sustained_start_quarter"],
                             rate=offer["consumption_m3_kwh"], inflow=offer["natural_inflow_m3s"],
                             low=offer["minimum_mw"], high=offer["available_mw"],
                             segments=offer["segments"]))
    atlas(data=dict(stations=stations, scenario=scenario, reaches=[
        dict(up=r["upstream"], down=r["downstream"], minutes=15*r["travel_quarters"])
        for r in reaches]), key="dadu_filing_atlas", width="stretch")
