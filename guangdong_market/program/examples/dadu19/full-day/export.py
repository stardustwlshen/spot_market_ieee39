"""Rebuild the offline Dadu forecast-error tables from the two archived runs."""
import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
DEFAULT_ARCHIVES = HERE.parents[2] / "runs_sichuan_v5_consultation" / "dadu19"
RUNS = {"water-value": "dadu19_20261127T094442Z_9a78ae68318c",
        "naive": "dadu19_20261127T094442Z_88b5e1e39adf"}
LABEL = {"water-value": "水价值报价", "naive": "朴素报价"}
TYPES = {"hydro": "水电", "thermal": "煤电", "renewable": "光伏", "fixed": "气电"}


def digest(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, allow_nan=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def load_runs(root):
    pair, boundaries = {}, set()
    for key, name in RUNS.items():
        folder = root / name
        with (folder / "receipt.json").open(encoding="utf-8") as stream:
            receipt = json.load(stream)
        compressed = (folder / "result.json.gz").read_bytes()
        if hashlib.sha256(compressed).hexdigest() != receipt["sha256"]:
            raise ValueError(f"{name} 结果档案摘要不符")
        d = json.loads(gzip.decompress(compressed))
        inp, c, s = d["inputs"], d["company"], d["summary"]
        if (d["run"] != name or s["n_executed"] != 96 or len(d["da"]) != 96
                or len(d["rt"]) != 96 or not d["money"]["complete_day"]
                or inp["scenario"] != "forecast_error" or c["strategy"] != key.replace("-", "_")):
            raise ValueError(f"{name} 不匹配指定的完整日与策略")
        if (digest({f: c[f] for f in ("schema", "strategy", "as_of", "offers")}) != s["bid_hash"]
                or digest(d["issued_forecasts"]) != s["issued_boundary_hash"]):
            raise ValueError(f"{name} 申报或滚动边界摘要不符")
        boundaries.add((digest(inp["source_hashes"]), s["boundary_hash"], s["issued_boundary_hash"],
                        c["company_forecast_sha256"], digest(c["water_value_yuan_m3"])))
        pair[key] = d
    if len(boundaries) != 1:
        raise ValueError("两个策略须使用相同的代码版本、物理边界、发行预测与水价值")
    return pair


def write_table(path, fields, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(fields.split("|"))
        for row in records:
            writer.writerow([format(v, ".6f").rstrip("0").rstrip(".") if isinstance(v, float)
                             else v for v in row])


def clock(t):
    return f"{t // 4:02}:{15 * (t % 4):02}"


def total_mwh(rows, ids):
    return .25 * sum(row["power"][i] for row in rows for i in ids)


def summary_row(key, d):
    inp, c, s = d["inputs"], d["company"], d["summary"]
    cascade, handoff = inp["market_case"]["cascade"], d["rt"][-1]["water_handoff"]
    values = c["water_value_yuan_m3"]
    stock = sum(values[v["id"]] * (handoff["storage_m3"][v["id"]] - v["initial_storage_m3"])
                for v in cascade["reservoirs"])
    queues = {(r["upstream"], r["downstream"]): sum(r["next_arrivals_m3"]) for r in handoff["in_transit"]}
    transit = sum(values[r["downstream"]] * (
        queues[r["upstream"], r["downstream"]] - sum(r["initial_arrivals_m3"])) for r in cascade["reaches"])
    if abs(stock + transit - s["water_stock_value_change"]) > .01:
        raise ValueError("库内及在途价值未与运行摘要对齐")
    return (LABEL[key], total_mwh(d["rt"], c["offers"]), s["company_revenue"], stock, transit,
            s["water_adjusted_value"], s["unserved_energy_mwh"], sum(not p["reliable"] for p in d["money"]["quarters"]),
            s["max_slack_mw"], s["gas_mwh"], s["solar_curtail_mwh"], s["water_spill_m3"],
            d["policy"]["power_ratio"], d["policy"]["energy_ratio"], d["policy"]["cap"])


def resource_rows(key, d):
    inp, c = d["inputs"], d["company"]
    settled = {}
    for row in d["money"]["hourly"]:
        settled.setdefault(row["resource_id"], []).append(row["revenue"])
    for a in inp["assets"]:
        i, typ = a["id"], a["kind"]
        offer = c["offers"].get(i, {})
        avail = [r.get(i, a["capacity_mw"]) for r in inp["available"]]
        cash = settled.get(i)
        yield (LABEL[key], i, a["name"], TYPES[typ], a["bus"], a["capacity_mw"], min(avail), max(avail),
               {"reservoir": "库容式", "run_of_river": "径流式", "ror": "径流式"}.get(a.get("model")),
               "公司按站竞价" if typ == "hydro" else ("非市场计划" if typ == "fixed" else "市场竞价"),
               offer.get("daily_energy_mwh"), offer.get("sustained_3h_mw"), total_mwh(d["da"], [i]),
               total_mwh(d["rt"], [i]), sum(cash) if cash and None not in cash else None)


def bid_rows(key, d):
    units = {r["id"]: r for r in d["inputs"]["market_case"]["resources"]}
    for i, declared in d["raw_offers"].items():
        start = units[i]["minimum_mw"] if units[i]["kind"] == "thermal" else 0
        effective = d["effective_offers"][i]
        if len(declared) != len(effective):
            raise ValueError(f"{i} 缓解前后的段数不匹配")
        for k, ((width, quote), (effective_width, price)) in enumerate(zip(declared, effective), 1):
            if abs(width - effective_width) > .001:
                raise ValueError(f"{i} 报价段宽变化")
            end = round(start + width, 2)
            yield (LABEL[key], i, k, start, end, quote, price)
            start = end


def export_quarters(key, d, out):
    inp, c, money = d["inputs"], d["company"], d["money"]
    assets = inp["assets"]
    station_ids = sorted(c["offers"])
    kinds = {typ: [a["id"] for a in assets if a["kind"] == typ] for typ in TYPES}
    line_ids = [l["id"] for l in inp["lines"]]
    system, dispatch, water, nodes, line_flows = [], [], [], [], []
    for t, (da, real, price) in enumerate(zip(d["da"], d["rt"], money["quarters"])):
        tick = (t+1, clock(t), clock(t+1))
        da_mw = [sum(da["power"][i] for i in ids) for ids in kinds.values()]
        rt_mw = [sum(real["power"][i] for i in ids) for ids in kinds.values()]
        short = real["system_shortfalls"]
        da_ratio = max(abs(da["flows"][l["id"]])/l["limit_mw"] for l in inp["lines"])
        rt_ratio, tight_line = max((abs(real["flows"][l["id"]])/l["limit_mw"], l["id"]) for l in inp["lines"])
        system.append((LABEL[key], *tick, sum(da["demand"].values()), sum(real["demand"].values()),
                       *da_mw, *rt_mw, short["unserved_load_mw"], short["excess_injection_mw"],
                       short["reserve_short_mw"], sum(real["curtailed_mw"].values()),
                       100*da_ratio, 100*rt_ratio, tight_line, price["uniform_price"],
                       "有效" if price["reliable"] else "暂停估值"))
        for a in assets:
            i, node = a["id"], str(a["bus"])
            da_power, rt_power = da["power"][i], real["power"][i]
            dispatch.append((*tick, i, a["bus"], c["internal_plan_mw"][t].get(i),
                             c["offers"][i]["available_mw"][t] if i in c["offers"] else None,
                             inp["available"][t].get(i, a["capacity_mw"]), inp["fixed_schedule"][t].get(i),
                             da_power, rt_power, rt_power-da_power, c["expected_price_yuan_mwh"][t].get(i),
                             da["raw_nodal_prices"][node], price["effective_price_by_bus"][node]))
        for i in station_ids:
            w = real["hydro"][i]
            water.append((*tick, i, c["offers"][i]["natural_inflow_m3s"][t],
                          da["hydro"][i]["natural_inflow_m3s"], w["natural_inflow_m3s"],
                          w["upstream_arrival_m3"]/900, w["turbine_m3s"], w["eco_bypass_m3s"],
                          w["water_spill_m3s"], w["release_m3s"], w["storage_m3"]))
        for node in sorted(real["demand"], key=int):
            nodes.append((*tick, node, da["demand"][node], real["demand"][node], real["shed"][node],
                          real["spill"][node], da["raw_nodal_prices"][node], real["raw_nodal_prices"][node],
                          price["effective_price_by_bus"][node]))
        for market_stage, row in (("日前", da), ("实时", real)):
            line_flows.append((*tick, market_stage, *(row["flows"][line] for line in line_ids)))
    p = out / key
    write_table(p / "dispatch.csv", "时段|起|止|资源|节点|公司内部计划_MW|公司申报上限_MW|日前运行可用上限_MW|气电计划_MW|日前参考_MW|实时执行_MW|实发减日前_MW|事前预期价_元每MWh|日前原始节点价_元每MWh|实时有效节点价_元每MWh", dispatch)
    write_table(p / "water.csv", "时段|起|止|电站|公司区间来水预测_m3每s|日前区间来水_m3每s|执行区间来水_m3每s|上游到达_m3每s|过机_m3每s|生态旁泄_m3每s|弃水_m3每s|总出库_m3每s|时段末可调库存_m3", water)
    write_table(p / "nodes.csv", "时段|起|止|节点|日前负荷_MW|实时负荷_MW|失负荷_MW|过剩注入_MW|日前原始价_元每MWh|实时原始价_元每MWh|实时有效价_元每MWh", nodes)
    write_table(p / "flows.csv", "时段|起|止|阶段|" + "|".join(line_ids), line_flows)
    return system


def export(archives, out):
    pair = load_runs(archives)
    systems, hourly = [], []
    for key, d in pair.items():
        systems.extend(export_quarters(key, d, out))
        money = d["money"]
        for h in [*money["hourly"], *money["portfolio"]]:
            hourly.append((LABEL[key], h["hour"], h.get("resource_id", "19站组合"), h["energy_mwh"],
                           h["rt_nodal_price"], h["revenue"], "暂停估值" if h["revenue"] is None else "有效"))
    lines = next(iter(pair.values()))["inputs"]["lines"]
    write_table(out / "network.csv", "支路|起点|终点|电抗_pu|双向限额_MW",
                ((l["id"], l["source"], l["target"], l["reactance"], l["limit_mw"]) for l in lines))
    write_table(out / "overview.csv", "策略|组合实发_MWh|组合现货毛收入_元|库内水价值变化_元|在途水价值变化_元|含水价值的研究收益_元|失负荷_MWh|暂停段数|最大可靠性松弛_MW|气电实发_MWh|弃光_MWh|各站弃水之和_m3|功率供需比|电量供需比|有效价格上限_元每MWh", (summary_row(k,d) for k,d in pair.items()))
    write_table(out / "system.csv", "策略|时段|起|止|日前总负荷_MW|实时总负荷_MW|日前水电_MW|日前煤电_MW|日前光伏_MW|日前气电_MW|实时水电_MW|实时煤电_MW|实时光伏_MW|实时气电_MW|失负荷_MW|过剩注入_MW|备用缺额_MW|弃光_MW|日前最高线路负载率_百分比|实时最高线路负载率_百分比|实时最高负载线路|实时统一结算点价_元每MWh|价格状态", systems)
    write_table(out / "resources.csv", "策略|资源|名称|类型|节点|设计装机_MW|日前运行可用上限最小_MW|日前运行可用上限最大_MW|储调近似|参与方式|申报可发电量_MWh|申报持续三小时能力_MW|日前参考_MWh|实时实发_MWh|全天现货毛收入_元", (row for k,d in pair.items() for row in resource_rows(k,d)))
    write_table(out / "bids.csv", "策略|资源|报价段|起点_MW|终点_MW|冻结申报价_元每MWh|市场采用价_元每MWh", (row for k,d in pair.items() for row in bid_rows(k,d)))
    write_table(out / "hourly.csv", "策略|小时序号|资源或组合|上网电量_MWh|上网量加权实时价_元每MWh|现货毛收入_元|估值状态", hourly)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archives", type=Path, default=DEFAULT_ARCHIVES, help="包含两个运行目录的位置")
    parser.add_argument("--out", type=Path, default=HERE)
    args = parser.parse_args()
    export(args.archives, args.out)
