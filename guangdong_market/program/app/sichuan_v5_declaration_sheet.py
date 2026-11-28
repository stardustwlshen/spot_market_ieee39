"""Station filings and their catalog and hydraulic assumptions, before market clearing."""
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
    boundaries = {r["id"]: r for r in company["hydraulic_declaration"]["reservoirs"]}
    stations = []
    for station in sheet["inputs"]["stations"]:
        uid = station["id"]
        offer = company["offers"][uid]
        water = boundaries[uid]
        stations.append(dict(id=uid, name=station["name"], kind=station["model"],
                             capacity=station["capacity_mw"], bus=station["bus"],
                             peak=offer["sustained_3h_mw"], start=offer["sustained_start_quarter"],
                             rate=offer["consumption_m3_kwh"], inflow=offer["natural_inflow_m3s"],
                             low=offer["minimum_mw"], high=offer["available_mw"],
                             technical=offer["technical_available_mw"], segments=offer["segments"],
                             referenceStorage=station["storage_m3"], meanFlow=station["mean_flow_m3s"], rateMethod=station["consumption_method"],
                             initialStock=water["initial_storage_m3"], storageMax=water["storage_max_m3"],
                             terminalLow=water["terminal_min_m3"], terminalHigh=water["terminal_max_m3"],
                             minimumRelease=water["min_release_m3s"], bypass=water["min_bypass_m3s"]))
    atlas(data=dict(stations=stations, scenario=scenario, reaches=[
        dict(up=r["upstream"], down=r["downstream"], minutes=15*r["travel_quarters"])
        for r in reaches]), key="dadu_filing_atlas", width="stretch")
