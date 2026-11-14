"""从 pandapower 的 case39（MATPOWER 数据）导出 DC 潮流所需的支路与负荷表。

输出 data/case39/branches.csv、buses.csv。母线编号采用 MATPOWER 的 1 基编号。
线路电抗按 Zbase = 345^2/100 换算为标幺值；变压器电抗取 vk%·(100/Sn)（忽略电阻与变比，DC 近似）。
线路额定 = √3·345 kV·max_i_ka（还原 MATPOWER 的 rateA），变压器额定 = Sn。
"""
import math
from pathlib import Path

import pandas as pd
import pandapower.networks as pn

ROOT = Path(__file__).resolve().parents[1]


def main():
    net = pn.case39()
    zbase = 345.0 ** 2 / 100.0
    rows = []
    for i, r in net.line.iterrows():
        x = r.x_ohm_per_km * r.length_km / r.parallel / zbase
        rate = math.sqrt(3) * 345.0 * r.max_i_ka
        rows.append(dict(branch_id=f"L{int(r.from_bus)+1}-{int(r.to_bus)+1}", from_bus=int(r.from_bus) + 1,
                         to_bus=int(r.to_bus) + 1, x_pu=round(x, 6), rate_mw=round(rate, 1), kind="line"))
    for i, r in net.trafo.iterrows():
        x = r.vk_percent / 100.0 * (100.0 / r.sn_mva)
        rows.append(dict(branch_id=f"T{int(r.hv_bus)+1}-{int(r.lv_bus)+1}", from_bus=int(r.hv_bus) + 1,
                         to_bus=int(r.lv_bus) + 1, x_pu=round(x, 6), rate_mw=float(r.sn_mva), kind="trafo"))
    br = pd.DataFrame(rows)
    out = ROOT / "data" / "case39"
    out.mkdir(parents=True, exist_ok=True)
    br.to_csv(out / "branches.csv", index=False)
    load = net.load.groupby("bus").p_mw.sum()
    buses = pd.DataFrame({"bus": range(1, len(net.bus) + 1)})
    buses["load_mw"] = [float(load.get(b - 1, 0.0)) for b in buses.bus]
    buses.to_csv(out / "buses.csv", index=False)
    print(f"branches={len(br)}, buses={len(buses)}, total load={buses.load_mw.sum():.1f} MW")


if __name__ == "__main__":
    main()
