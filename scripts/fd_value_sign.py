"""有限差分核对状态价值符号：丰水/枯水日前最终 SCED，给 ES1/PS1/H1 在中间时段动态约束右端注入 ε。"""
import argparse
import sys, json, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT / "src"))
import numpy as np, pyomo.environ as pyo
from smm.config import load_config
from smm.pipeline import prepare
from smm.da import run_da
from smm import scuc

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("season", choices=("wet", "dry"), nargs="?", default="wet")
    parser.add_argument("periods", nargs="?", default="40")
    args = parser.parse_args(argv)
    season = args.season
    try:
        ts = [int(value) for value in args.periods.split(",")]
    except ValueError:
        parser.error("时段须为逗号分隔的整数")
    outputs = [Path(f"outputs/{name}_{season}.json").absolute() for name in ("fd_sign", "fd_snap")]
    for output in outputs:
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"诊断输出已存在，未开始计算且未覆盖：{output}")
    out, snapshot_output = [output.resolve() for output in outputs]
    cfg = load_config(season=season)
    if not ts or any(t < 0 or t + 1 >= cfg.T for t in ts):
        parser.error(f"时段须为0到{cfg.T - 2}之间的整数")
    out.parent.mkdir(parents=True, exist_ok=True)
    case = prepare(cfg, 1, read_only=True)
    da = run_da(case, verbose=False)
    m = da.model.clone()
    md, dt = m._md, m._md.dt
    scuc.solve_sced(m)
    obj0 = pyo.value(m.obj)
    sv = scuc.state_values(m)
    lmp = scuc.prices(m)["lmp"] if hasattr(scuc, "prices") else None
    rows = []

    def own_bid_terms(m):
        st = sum(md.storage[s]["cd"][k] * pyo.value(m.pd[s, k, t]) * dt for (s, k) in m.SD for t in m.T) \
            - sum(md.storage[s]["cc"][k] * pyo.value(m.pc[s, k, t]) * dt for (s, k) in m.SC for t in m.T)
        ps = sum(md.pumped[p]["cg"][k] * pyo.value(m.pg[p, k, t]) * dt for (p, k) in m.PGK for t in m.T) \
            - sum(md.pumped[p]["cp"][k] * pyo.value(m.pp[p, k, t]) * dt for (p, k) in m.PPK for t in m.T) \
            + sum(md.pumped[p]["start_cost_gen"] * pyo.value(m.sg[p, t]) + md.pumped[p]["start_cost_pump"] * pyo.value(m.sp[p, t]) for p in m.PS for t in m.T)
        return st + ps
    own0 = own_bid_terms(m)

    def run(kind, u, con, t, eps):
        c = con[u, t + 1]
        lb = c.lower; base = c.expr
        # 约束为 X[t+1] == X[t] + flow；Pyomo 标准化成 body == 常数。用 set_value 加 +eps 到右侧（即 t 段末多 eps 存量）
        X = getattr(m, kind)
        from pyomo.core.expr.relational_expr import EqualityExpression
        lhs, rhs = base.args
        c.set_value(lhs == rhs + eps)
        res = scuc.solve_sced(m)
        o = pyo.value(m.obj); ob = own_bid_terms(m)
        c.set_value(base)
        return o, ob

    for kind, units, con in (("E", ["ES1", "ES2", "ES3"], m.c_soc), ("Vu", list(m.PS), m.c_pres), ("V", list(m.HS), m.c_water)):
        for u in units:
            if u not in sv[kind]:
                continue
            for t in ts:
                eps = 0.5 if kind == "E" else 0.05
                base_expr = {(u, t): con[u, t + 1].expr}
                try:
                    op, obp = run(kind, u, con, t, +eps)
                    om, obm = run(kind, u, con, t, -eps)
                except RuntimeError:
                    con[u, t + 1].set_value(base_expr[(u, t)]); scuc.solve_sced(m)
                    r = dict(season=season, unit=u, kind=kind, t=t, infeasible=True, v_native=float(sv[kind][u]["value_native"][t]))
                    rows.append(r); print(json.dumps(r, ensure_ascii=False), flush=True); continue
                v = float(sv[kind][u]["value_native"][t]); raw = float(sv[kind][u]["dual_raw"][t + 1])
                conv = 1.0 if kind == "E" else (1 / float(md.pumped[u]["e_gen"]) if kind == "Vu" else .36 * float(md.hydro[u]["water_rate"]))
                r = dict(season=season, unit=u, kind=kind, t=t, eps=eps,
                         dObj_plus=(op - obj0) / eps, dObj_minus=(obj0 - om) / eps,
                         v_native=v, dual_raw_t1=raw,
                         sys_dObj_plus=((op - obp) - (obj0 - own0)) / eps,
                         sys_dObj_minus=((obj0 - own0) - (om - obm)) / eps,
                         per_mwh_factor=conv)
                rows.append(r); print(json.dumps(r, ensure_ascii=False), flush=True)
    with out.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(obj0=obj0, rows=rows), ensure_ascii=False, indent=1))
    # 额外：bids 与调度快照
    # 最后一次扰动的解不是基准解；先重解已恢复的原约束，再导出基准快照。
    scuc.solve_sced(m)
    snap = {}
    for s in ["ES1", "ES2", "ES3"]:
        if s in md.storage:
            d = md.storage[s]
            snap[s] = dict(cc=list(map(float, d["cc"])), cd=list(map(float, d["cd"])), Lc=list(map(float, d["Lc"])), Ld=list(map(float, d["Ld"])), eta_c=d["eta_c"], eta_d=d["eta_d"],
                           Pc=[pyo.value(m.Pc[s, t]) for t in m.T], Pd=[pyo.value(m.Pd[s, t]) for t in m.T], E=[pyo.value(m.E[s, t]) for t in m.T])
    for p in m.PS:
        d = md.pumped[p]
        snap[p] = dict(cg=list(map(float, d["cg"])), cp=list(map(float, d["cp"])), e_gen=d["e_gen"], e_pump=d["e_pump"],
                       Pg=[pyo.value(m.Pg[p, t]) for t in m.T], Pp=[pyo.value(m.Pp[p, t]) for t in m.T], Vu=[pyo.value(m.Vu[p, t]) for t in m.T],
                       end_dual=float(m.dual[m.c_pvend[p]]))
    with snapshot_output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(snap, ensure_ascii=False))
    print("done", time.strftime("%H:%M:%S"))


if __name__ == "__main__":
    main()
