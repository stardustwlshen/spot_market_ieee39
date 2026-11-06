"""第二轮K项：多场景全流程结果的指标对比与自动检查（只读结果目录，不重算）。

用法：python scripts/validate_runs.py 目录1[=名称] 目录2[=名称] ... --out 汇总.csv
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml


def _csv(d: Path, name: str):
    p = d / name
    if not p.exists():
        return None
    try:
        return pd.read_csv(p)
    except (OSError, ValueError, pd.errors.ParserError):
        return pd.DataFrame()


def _snapshot(d: Path):
    root = d / "scenario_snapshot" / "config"
    def y(n):
        p = root / n
        if not p.is_file():
            raise ValueError(f"缺少本次运行冻结配置 {p.relative_to(d)}；不使用当前仓库默认值")
        value = yaml.safe_load(p.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"冻结配置 {n} 为空或格式错误")
        return value
    return y("units.yaml"), y("modes.yaml"), y("base.yaml")


def metrics(d: Path) -> dict:
    S = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    sp, rt, ts = S.get("spread", {}), S.get("realtime", {}), S.get("time_s", {})
    st = S.get("settlement_by_type", {})
    tot = lambda *ks: sum(float(st.get(k, {}).get("total", 0.) or 0.) for k in ks)
    rs = _csv(d, "rt_system.csv")
    try:
        dt = float(_snapshot(d)[2]["dt_h"])
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError):
        dt = float("nan")  # unknown, never borrow a current or assumed timestep
    def hourly_range(values):
        # Undefined zero-load hourly prices remain unknown, never zero or an
        # exception when reading a standards-compliant JSON null.
        if not values or any(v is None for v in values):
            return None
        values = np.asarray(values, dtype=float)
        return float(values.max() - values.min()) if np.isfinite(values).all() else None
    out = dict(
        日前负荷加权均价=sp.get("da_lw_avg"), 实时负荷加权均价=sp.get("rt_lw_avg"),
        价差均值_实时减日前=sp.get("lw_spread_mean"), 价差绝对均值=sp.get("lw_spread_abs_mean"),
        日前小时价差=hourly_range(sp.get("da_lw_hourly")),
        实时小时价差=hourly_range(sp.get("rt_lw_hourly")),
        日前弃电MWh=S.get("da", {}).get("curtail_mwh"), 实时弃电MWh=rt.get("curtail_mwh"),
        实时备用缺额MWh=float(rs.R_up_short.sum() * dt) if rs is not None and "R_up_short" in rs else None,
        实时备用缺额时段=int((rs.R_up_short > 1e-6).sum()) if rs is not None and "R_up_short" in rs else None,
        线路松弛最大MW=float(max(S.get("checks", {}).get("da_dispatch_line_slack_max_mw", 0) or 0,
                            rs.line_slack_max_win.max() if rs is not None and "line_slack_max_win" in rs else 0)),
        回退次数=rt.get("fallback_count", 0),
        储能电能量收入万元=tot("storage_dis", "storage_ch") / 1e4,
        抽蓄电能量收入万元=tot("pumped_gen", "pumped_pump") / 1e4,
        用时s=ts.get("total"), 日前目标万元=S.get("da", {}).get("objective", 0) / 1e4,
        储能模式=rt.get("storage_mode"), 水电模式=rt.get("hydro"))
    for stage in ("da", "rt"):
        h = _csv(d, f"{stage}_hydro.csv")
        if h is not None and len(h):
            for stn, g in h.groupby("station"):
                out[f"{'日前' if stage == 'da' else '实时'}{stn}日末库容万m3"] = float(g.sort_values("period").V_1e4m3.iloc[-1])
    return out


def _hydro_lookahead_audit(d, cfg):
    """Cross-check full-precision equations and scalar CSV against source rounds."""
    from types import SimpleNamespace
    from smm.full import hydro_terminal_lookahead_checks
    evidence=json.loads((d/"hydro_terminal_lookahead_evidence.json").read_text())
    if evidence.get("scope") != "reference_continuation_forecast_not_executed_cost_or_feasibility_proof":
        raise ValueError("水电前瞻scope不明确")
    rows=evidence["rows"]
    if not isinstance(rows,list):raise ValueError("水电前瞻rows不是列表")
    hydro=json.loads((d/"hydro_window_evidence.json").read_text())
    windows=hydro["rt_windows"]
    if [r["t"] for r in windows] != list(range(cfg.T)):
        raise ValueError("水电前瞻缺少完整RT状态来源")
    if any(not isinstance(w,dict) or w.get("window_start") not in range(cfg.T) for w in rows):
        raise ValueError("水电前瞻窗口时标无效")
    rounds=[dict(t=r["t"],reference_metadata=r.get("reference_metadata",{}),
        snap=dict(r["snapshot"],hydro_terminal_lookahead=[w for w in rows if w["window_start"]==r["t"]])) for r in windows]
    ids=[dict(tau=r["tau"],snap=r["snapshot"]) for r in hydro.get("id_rounds",[])]
    failures=[]
    if cfg.mode.get("intraday",{}).get("enabled",False):
        success=pd.read_csv(d/"id_rounds_summary.csv")
        if sorted(x["tau"] for x in ids) != sorted(success.tau.tolist()):
            raise ValueError("水电前瞻ID源轮次与成功表不一致")
        failures=pd.read_csv(d/"id_failed_rounds.csv").to_dict("records")
    actual_path=d/'rolling_actual_states.json'
    states=[]
    if actual_path.exists():
        payload=json.loads(actual_path.read_text());windows=payload.get('windows')
        if not isinstance(windows,list) or not windows or windows[0].get('t') != 0:
            raise ValueError('Invalid frozen actual initial state for hydro lookahead')
        states=[SimpleNamespace(**row) for row in windows]
    elif (d/'admission.json').exists() or (d/'actual_checkpoint.json').exists():
        raise ValueError('Continuous-day archive lacks actual initial history')
    result=hydro_terminal_lookahead_checks(cfg,SimpleNamespace(rt=rounds,id_rounds=ids,id_failures=failures,states=states,actual_state_required=(d/'admission.json').exists() or (d/'actual_checkpoint.json').exists()),hydro["da"],
        input_registry=hydro.get("causal_input_registry"))
    if not result["hydro_terminal_lookahead_evidence_complete"]:
        raise ValueError("; ".join(result["hydro_terminal_lookahead_errors"]))
    csv=pd.read_csv(d/"rt_hydro_terminal_lookahead.csv")
    keys=["window_start","tau","unit"]
    if not set(keys)<=set(csv) or len(csv)!=len(rows) or csv.duplicated(keys).any():
        raise ValueError("水电前瞻CSV缺行、重复或缺键")
    for row in rows:
        selected=csv[(csv.window_start==row["window_start"])&(csv.tau==row["tau"])&(csv.unit==row["unit"])]
        if len(selected)!=1:raise ValueError("水电前瞻JSON/CSV时标主体不一致")
        saved=selected.iloc[0]
        for key,value in row.items():
            if isinstance(value,(dict,list)):continue
            if key not in csv:raise ValueError(f"水电前瞻CSV缺字段 {key}")
            other=saved[key]
            if value is None:
                ok=pd.isna(other)
            elif isinstance(value,bool):
                ok=(isinstance(other,(bool,np.bool_)) and bool(other)==value)
            elif isinstance(value,(int,float)):
                ok=(not isinstance(other,(bool,np.bool_)) and np.isfinite(float(other))
                    and np.isclose(float(other),value,atol=5.1e-5,rtol=1e-10))
            else:ok=str(other)==str(value)
            if not ok:raise ValueError(f"水电前瞻CSV与完整精度证据不一致 {key}")
    return result



def _frozen_resolved_bids(directory):
    """Exact effective bid snapshots, never current bid libraries or self-reported caps."""
    candidates=[directory/"resolved_bids.json", directory/"scenario_snapshot/resolved_bids.json"]
    found=[]
    for path in candidates:
        if path.exists():
            data=json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data,dict) or not isinstance(data.get("units"),dict):
                raise ValueError("冻结有效报价不是完整对象记录")
            found.append(data)
    if not found:
        raise ValueError("缺少本次冻结有效报价resolved_bids.json，不能核实水电续程报价容量")
    if any(data != found[0] for data in found[1:]):
        raise ValueError("根目录与快照的有效报价证据互相冲突")
    return found[0]


def _csv_daily_minimum_audit(directory, cfg):
    import json, math
    import numpy as np
    import pandas as pd
    from pathlib import Path
    directory=Path(directory);T=cfg.T;dt=cfg.dt
    limits={}
    for h in cfg.units.get('hydro',[]):
        if h.get('regulation','reservoir')!='reservoir' or h.get('e_day_min') is None:continue
        value=h['e_day_min']
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
            raise ValueError(f"{h['id']} 冻结日电量下限无效")
        limits[h['id']]=float(value)
    if not limits:return dict(active=False,records=[])
    tables={}
    for stage in ('da','rt'):
        table=pd.read_csv(directory/f'{stage}_hydro.csv')
        if not {'period','station','P_mw'}<=set(table):raise ValueError(f'{stage}缺少水电功率字段')
        result={}
        for h in limits:
            rows=table[table.station==h].copy()
            periods=pd.to_numeric(rows.period,errors='coerce')
            power=pd.to_numeric(rows.P_mw,errors='coerce')
            if (len(rows)!=T or not np.isfinite(periods).all() or set(periods)!=set(range(T))
                    or periods.duplicated().any() or not np.isfinite(power).all() or (power < -5.1e-5).any()):
                raise ValueError(f'{stage} {h}水电实际/计划时段或功率证据不完整')
            rows['period']=periods;rows['P_mw']=power
            result[h]=rows.sort_values('period').P_mw.to_numpy(dtype=float)
        tables[stage]=result
    # Original CSV P_mw is rounded to four decimals. Propagate that known
    # serialization error once through the energy sum; no arbitrary percentage.
    tolerance=T*dt*5.1e-5+1e-6
    records=[]
    for h,minimum in limits.items():
        da=float(tables['da'][h].sum()*dt);actual=float(tables['rt'][h].sum()*dt)
        if min(da,actual)<minimum-tolerance:raise ValueError(f'{h}日前或实际日电量未达到冻结下限')
        records.append(dict(unit=h,minimum_mwh=minimum,da_mwh=da,executed_mwh=actual))
    if cfg.mode.get('intraday',{}).get('enabled',False):
        successes=pd.read_csv(directory/'id_rounds_summary.csv')
        plans=pd.read_csv(directory/'id_plans.csv')
        if 'tau' not in successes or not {'tau','period','unit','type','P_mw'}<=set(plans):
            raise ValueError('日内成功轮次或水电计划字段缺失')
        taus=pd.to_numeric(successes.tau,errors='coerce')
        if not np.isfinite(taus).all() or not taus.mod(1).eq(0).all() or taus.duplicated().any():
            raise ValueError('日内成功轮次无效')
        hydro=plans[(plans.type=='hydro')&plans.unit.isin(limits)].copy()
        for col in ('tau','period','P_mw'):hydro[col]=pd.to_numeric(hydro[col],errors='coerce')
        if not np.isfinite(hydro[['tau','period','P_mw']]).all().all() or (hydro.P_mw < -5.1e-5).any():
            raise ValueError('日内水电计划含无效数字')
        expected={(int(t),k,h) for t in taus for k in range(int(t),T) for h in limits}
        if (set(zip(hydro.tau,hydro.period,hydro.unit))!=expected
                or hydro.duplicated(['tau','period','unit']).any()):
            raise ValueError('日内水电计划主体/轮次/剩余时段覆盖不全')
        for tau in taus.astype(int):
            for h,minimum in limits.items():
                before=float(tables['rt'][h][:tau].sum()*dt)
                future=float(hydro[(hydro.tau==tau)&(hydro.unit==h)].P_mw.sum()*dt)
                if before+future<minimum-tolerance:
                    raise ValueError(f'ID@{tau} {h}实际已发加剩余计划不足日电量下限')
    return dict(active=True,records=records,csv_energy_rounding_tolerance_mwh=tolerance)


def _reserve_shortage_audit(ev, rs, da_slack, mode, T, dt):
    """Full precision shortage measures; integrate only each executed first step."""
    down = mode.get("reserve", {}).get("down_enabled", False)
    if type(down) is not bool:
        raise ValueError("冻结下备用开关不是布尔值")
    H = int(round(float(mode["realtime"]["lookahead_h"])/dt))
    if H <= 0 or rs is None:
        raise ValueError("备用时域或实际系统证据缺失")
    def number(value):
        if isinstance(value, bool) or not isinstance(value, (int,float)) or not np.isfinite(value) or value < 0:
            raise ValueError("备用缺额必须是有限非负数")
        return float(value)
    def rows(window, start, length):
        if window.get("down_enabled") is not down:
            raise ValueError("备用窗口下备用开关与冻结配置不一致")
        system = window.get("system")
        if not isinstance(system,list) or len(system) != length:
            raise ValueError("备用system缺少完整时段")
        by_t = {}
        for row in system:
            t = row.get("relative_period")
            if type(t) is not int or t not in range(length) or t in by_t or row.get("period") != start+t:
                raise ValueError("备用system时标重复、缺失或错误")
            values = [number(row.get("R_up_short")), number(row.get("R_dn_short"))]
            if not down and values[1] != 0:
                raise ValueError("禁用下备用却存在下备用缺额")
            by_t[t] = values
        return np.array([by_t[t] for t in range(length)])
    da = rows(ev["da"],0,T)
    windows = [rows(w,t,min(H,T-t)) for t,w in enumerate(ev["rt_windows"])]
    actual = np.array([w[0] for w in windows])
    maxima = np.array([w.max(axis=0) for w in windows])
    result = {"备用缺额证据完整": True, "备用缺额检查范围": "正式DA、实际RT首段、RT全规划窗；不含未导出的ID备用窗口"}
    for i, (direction, label) in enumerate((("up","上"),("dn","下"))):
        for col, expected in ((f"R_{direction}_short", actual[:,i]),
                              (f"reserve_{direction}_short_max_win", maxima[:,i])):
            if col not in rs:
                raise ValueError(f"rt_system.csv缺少{col}")
            observed = pd.to_numeric(rs.sort_values("period")[col],errors="coerce").to_numpy()
            if not np.isfinite(observed).all() or (observed < 0).any() or not np.allclose(observed,expected,atol=5.1e-5,rtol=0):
                raise ValueError(f"rt_system.csv {col}与完整精度备用证据不一致")
        saved = number(da_slack.get(f"reserve_{direction}_short_max"))
        if not np.isclose(saved,da[:,i].max(),atol=5.1e-5,rtol=0):
            raise ValueError("DA备用缺额汇总与完整精度证据不一致")
        result[f"日前{label}备用缺额最大MW"] = float(da[:,i].max())
        result[f"日前{label}备用缺额MWh"] = float(da[:,i].sum()*dt)
        result[f"实际{label}备用缺额最大MW"] = float(actual[:,i].max())
        result[f"实际{label}备用缺额MWh"] = float(actual[:,i].sum()*dt)
        result[f"RT规划窗{label}备用缺额最大MW"] = float(maxima[:,i].max())
    result["零备用缺额"] = bool(max(da.max(),maxima.max()) < 1e-6)
    result["备用缺额能量口径"] = "DA全日计划；RT仅逐窗已执行首段；重叠规划窗不累加"
    return result


def _hydro_reserve_water_policy_audit(ev, summary, mode, hydro, season, audit_result):
    """Bind new water evidence to its saved policy and frozen physical inputs.

    Legacy archives keep their historical arithmetic scope, explicitly without
    certifying the new hydro water rule. New schemas cannot downgrade evidence.
    """
    windows = [ev['da'], *ev.get('id_windows', []), *ev['rt_windows']]
    saved = summary.get('reserve', {})
    if not isinstance(saved, dict):
        raise ValueError('summary.reserve须为对象')
    new = 'evidence_version' in saved or any('evidence_version' in w for w in windows)
    formulation = 'period_end_stock_incremental_up_water_v1'
    if not new:
        if ('hydro_reserve_water_formulation' in saved
                or any('hydro_reserve_water_formulation' in w for w in windows)):
            raise ValueError('水电备用水量证据缺少规则版本，不能退回历史口径')
        return dict(水电备用水量已验证=None, 水电备用水量规则='历史容量口径，水量未核验',
                    水电备用出库已验证=None,水电备用出库规则='历史口径，激活出库未核验')
    expected_id = summary.get('intraday', {}).get('rounds', 0)
    if not isinstance(ev.get('id_windows'), list) or len(ev['id_windows']) != expected_id:
        raise ValueError('新水电备用证据缺少完整成功日内窗口')
    starts = [w.get('window_start') for w in ev['id_windows']]
    if len(set(starts)) != len(starts) or any(not isinstance(t, int) or isinstance(t, bool)
            or not 0 <= t < ev['da']['periods'] for t in starts):
        raise ValueError('日内备用窗口时标无效或重复')
    if any(w.get('periods') != ev['da']['periods']-w['window_start'] for w in ev['id_windows']):
        raise ValueError('日内备用窗口必须覆盖剩余运行日')
    reserve = mode.get('reserve', {})
    enabled = bool(hydro) and 'hydro' in reserve.get('providers', [])
    def finite(value, label):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
            raise ValueError(f'{label}须为有限数值')
        return float(value)
    def same(a, b, label):
        if abs(finite(a,label)-finite(b,label)) > 1e-7:
            raise ValueError(f'{label}与冻结输入/规则不一致')
    version=saved.get('evidence_version')
    if version not in (2,3):
        raise ValueError('未知水电备用证据版本')
    for record in [saved, *windows]:
        if (record.get('evidence_version') != version
                or record.get('hydro_reserve_water_formulation') != formulation
                or record.get('hydro_reserve_water_enabled') is not enabled):
            raise ValueError('新水电备用水量规则版本、参与标记或逐窗证据缺失/降级')
    required = {h['id']: h for h in hydro}
    for window in windows:
        if enabled:
            same(window.get('T_dur_h'), reserve.get('T_dur_h'), '水电备用持续时间')
        rows = [r for r in window['resources'] if r.get('unit') in required]
        if (len(rows) != len(required)*window['periods']
                or any(row.get('type') != 'hydro' for row in rows)):
            raise ValueError('新水电备用证据主体/类型缺失')
        if not enabled:
            continue
        for row in rows:
            h = required[row['unit']]; d = h['seasons'][season]
            lo, hi = (d['v0']-(h.get('pondage', 0) or 0), d['v0']) if h.get('regulation','reservoir')=='run_of_river' else (d['vmin'],d['vmax'])
            same(row.get('water_rate_m3s_per_mw'), h['water_rate'], '水电耗水率')
            same(row.get('V_min_1e4m3'), lo, '水电最低库容')
            same(row.get('V_max_1e4m3'), hi, '水电最高库容')
    verified = audit_result.get('hydro_water_check_passed')
    if 'hydro_water_check_passed' not in saved or saved['hydro_water_check_passed'] is not verified:
        raise ValueError('summary水电水量验收状态与独立复核不一致或缺失')
    if enabled and verified is None:
        raise ValueError('新水电备用参与却没有水量核验结果')
    result=dict(水电备用水量已验证=verified, 水电备用水量规则=formulation)
    result.update(_hydro_reserve_outflow_policy_audit(ev,summary,mode,hydro,season,audit_result))
    return result


def _hydro_reserve_outflow_policy_audit(ev,summary,mode,hydro,season,audit_result):
    windows=[ev['da'],*ev.get('id_windows',[]),*ev['rt_windows']]
    saved=summary['reserve'];version=saved['evidence_version']
    formulation='generation_flow_with_reducible_spill_v1'
    if version<3:
        if any('hydro_reserve_outflow_formulation' in r or 'hydro_reserve_outflow_enabled' in r for r in [saved,*windows]):
            raise ValueError('新增水电出库证据不能降为旧版本')
        return dict(水电备用出库已验证=None,水电备用出库规则='历史口径，激活出库未核验')
    enabled=('hydro' in mode.get('reserve',{}).get('providers',[])
             and any(h.get('qout_max') is not None for h in hydro))
    for record in [saved,*windows]:
        if (record.get('evidence_version')!=3
                or record.get('hydro_reserve_outflow_formulation')!=formulation
                or record.get('hydro_reserve_outflow_enabled') is not enabled):
            raise ValueError('水电备用出库规则、标记缺失或降级')
    required={h['id']:h for h in hydro}
    def same(a,b,label):
        if a is None or b is None:
            if a is not b:raise ValueError(f'{label}与冻结输入不一致')
        elif (isinstance(a,bool) or isinstance(b,bool) or not isinstance(a,(int,float))
                or not isinstance(b,(int,float)) or not np.isfinite([a,b]).all() or abs(a-b)>1e-7):
            raise ValueError(f'{label}与冻结输入不一致或无效')
    for w in windows:
        for row in w['resources']:
            if row.get('unit') not in required:continue
            h=required[row['unit']]
            if 'qout_max_m3s' not in row or 'qeco_m3s' not in row:
                raise ValueError('水电出库或生态参数证据缺失')
            same(row['qout_max_m3s'],h.get('qout_max'),'水电出库上限')
            same(row['qeco_m3s'],h['seasons'][season]['qeco'],'水电生态下泄下限')
    verified=audit_result.get('hydro_outflow_check_passed')
    if 'hydro_outflow_check_passed' not in saved or saved['hydro_outflow_check_passed'] is not verified:
        raise ValueError('summary水电出库核验状态缺失或不一致')
    if enabled and verified is None:raise ValueError('已配置水电出库上限但没有激活出库核验')
    scope=(formulation if enabled else '未配置出库上限，不代表无限能力'
           if 'hydro' in mode.get('reserve',{}).get('providers',[]) and hydro else '未参与水电备用')
    return dict(水电备用出库已验证=verified,水电备用出库规则=scope)


def checks(d: Path) -> dict:
    """Read-only, fail-closed validation using only the run's frozen evidence."""
    S = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    C = {key: False for key in ("流程未中断", "功率平衡", "松弛为0", "无同时充放",
                                "SOC库容边界", "日末约束", "价格限值", "两部制恒等式<1元",
                                "价值曲线=−对偶(符号自洽)", "备用复核通过", "备用缺额证据完整", "零备用缺额", "非计划松弛为0", "证据完整", "验收通过")}
    C.update(水电备用水量已验证=None, 水电备用水量规则="未验证",
             水电备用出库已验证=None,水电备用出库规则="未验证")
    errors, notes = [], []
    try:
        from smm.result_retention import audit_archive
        id_cache = audit_archive(d, S)
        C['ID数值缓存已验证'] = id_cache['valid']
    except (OSError,ValueError,TypeError,KeyError,IndexError,AttributeError) as exc:
        errors.append(f'ID数值缓存未验证：{exc}')
        C['ID数值缓存已验证'] = False
    try:
        units, modes, base = _snapshot(d)
        mk = modes[base["rule_set"]]
        lo, hi = float(mk["price_limits"]["clear_min"]), float(mk["price_limits"]["clear_max"])
        T, dt = int(base["periods"]), float(base["dt_h"])
        if isinstance(base["periods"], bool) or T != base["periods"] or T <= 0 or not np.isfinite([dt,lo,hi]).all() or dt <= 0 or lo > hi:
            raise ValueError("冻结时域、步长或价格限值无效")
        groups = {kind: [u for u in units.get(kind, []) or [] if u.get("enabled", True)]
                  for kind in ("thermal", "renewable", "hydro", "storage", "pumped", "tie")}
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        C["说明"] = f"配置证据未验证：{exc}"
        return C
    policy = mk.get("realtime", {}).get("value_method", {}).get("terminal_policy", {})
    terminal_mode = policy.get("mode", "hard")
    terminal_coefficient = policy.get("penalty_yuan_per_discharge_mwh")
    if terminal_mode not in ("hard", "soft") or (terminal_mode == "soft" and (
            isinstance(terminal_coefficient, bool) or not isinstance(terminal_coefficient, (int,float))
            or not np.isfinite(terminal_coefficient) or terminal_coefficient <= 0)):
        C["说明"] = "冻结终态政策/罚价无效"
        return C
    C["终态政策"] = terminal_mode
    C["配置来源"] = "scenario_snapshot/config"
    C["价格下限"], C["价格上限"] = lo, hi
    periods = set(range(T))
    ch = S.get("checks", {})

    def table(name, columns, numeric=(), *, key=None, ids=None, grid=True):
        frame = _csv(d, name)
        if frame is None or frame.empty or not set(columns) <= set(frame):
            errors.append(f"{name} 缺失、空表或缺字段")
            return None
        for col in numeric:
            values = pd.to_numeric(frame[col], errors="coerce").to_numpy(dtype=float)
            if not np.isfinite(values).all():
                errors.append(f"{name}.{col} 含非有限值或非数值")
                return None
            frame[col] = values
        if grid:
            ps = pd.to_numeric(frame.period, errors="coerce").to_numpy(dtype=float)
            if not np.isfinite(ps).all() or not np.equal(ps, np.floor(ps)).all():
                errors.append(f"{name} 时段无效")
                return None
            frame["period"] = ps.astype(int)
            if key:
                if frame[key].isna().any():
                    errors.append(f"{name} 主体缺失")
                    return None
                expected_ids = set(ids) if ids is not None else set(frame[key])
                expected = {(uid,t) for uid in expected_ids for t in periods}
                actual = set(zip(frame[key], frame.period))
                valid = bool(expected) and actual == expected and not frame.duplicated([key,"period"]).any()
            else:
                valid = set(frame.period) == periods and not frame.period.duplicated().any()
            if not valid:
                errors.append(f"{name} 主体/时段覆盖不全、重复或含额外记录")
                return None
        return frame

    def finite(value):
        try:
            return not isinstance(value, bool) and np.isfinite(float(value))
        except (TypeError, ValueError):
            return False

    C["流程未中断"] = (S.get("completed_scope") == "full_day" and S.get("executed_periods") == T
                       and S.get("expected_periods") == T)
    slack_cols = ["shed_mw", "surplus_mw", "line_slack_max_win", "anchor_slack_max_mwh_win"]
    value_resources = {(kind,u["id"]) for kind,group in (("E","storage"),("Vu","pumped"),("V","hydro"))
                       if (mk.get("realtime",{}).get("hydro", "value") == "value" if kind == "V" else
                           mk.get("realtime",{}).get("storage",{}).get("mode", "value") == "value")
                       for u in groups[group]}
    rs = table("rt_system.csv", ["period", "R_up_short", *slack_cols], ["R_up_short", *slack_cols])
    balance = ch.get("rt_balance_err_mw")
    C["功率平衡误差MW"] = float(balance) if finite(balance) else None
    C["功率平衡"] = bool(rs is not None and finite(balance) and abs(float(balance)) < 1e-3)
    if not finite(balance):
        errors.append("summary.json 缺少有限功率平衡误差")
    C["上备用缺额最大MW"] = float(rs.R_up_short.abs().max()) if rs is not None else None
    slack = float(rs[slack_cols].abs().to_numpy().max()) if rs is not None else None
    band_ok = not value_resources
    nonplanned_band_ok = band_ok
    terminal_evidence_ok = True
    vw = None
    if value_resources:
        vw = table("rt_value_windows.csv", ["window_start","tau","kind","unit","state","lower","upper","band_slack"],
                   ["window_start","tau","state","lower","upper","band_slack"], grid=False)
        if vw is not None:
            keys = list(zip(vw.window_start, vw.kind, vw.unit))
            expected = {(t,kind,uid) for t in periods for kind,uid in value_resources}
            H = int(round(float(mk["realtime"]["lookahead_h"])/dt))
            valid = (len(keys) == len(set(keys)) and set(keys) == expected and H > 0
                     and np.array_equal(vw.tau.to_numpy(), np.minimum(T-1, vw.window_start.to_numpy()+H-1))
                     and (vw.lower <= vw.upper+1e-6).all() and (vw.band_slack >= -1e-6).all())
            if not valid:
                errors.append("rt_value_windows.csv 资源/窗口覆盖或端点、上下界无效")
            else:
                residual = np.maximum.reduce([vw.lower-vw.state, vw.state-vw.upper, np.zeros(len(vw))])
                band_ok = bool(np.max(residual) < 1e-3 and vw.band_slack.max() < 1e-6)
                planned = np.zeros(len(vw), dtype=bool)
                if terminal_mode == "soft":
                    metadata = ["terminal_policy", "planned_terminal_soft", "terminal_penalty_yuan_per_discharge_mwh",
                                "discharge_energy_factor", "deviation_discharge_mwh", "terminal_penalty_cost", "value_native"]
                    if not set(metadata) <= set(vw):
                        errors.append("软终态缺少政策、能量换算或罚金明细")
                        terminal_evidence_ok = False
                    else:
                        flags = vw.planned_terminal_soft.map(lambda x: x if isinstance(x, (bool,np.bool_)) else
                            ({"True":True,"False":False}.get(str(x))))
                        planned = (vw.tau.to_numpy() == T-1)
                        terminal_evidence_ok = bool(flags.notna().all() and np.array_equal(flags.to_numpy(),planned)
                                                   and (vw.terminal_policy == "soft").all())
                        for pos, w in enumerate(vw.itertuples()):
                            if not planned[pos]:
                                continue
                            try:
                                family = {"E":"storage", "Vu":"pumped", "V":"hydro"}[w.kind]
                                unit = next(u for u in groups[family] if u["id"] == w.unit)
                                factor = unit["eta_d"] if w.kind == "E" else (unit["e_gen"] if w.kind == "Vu" else 1./(.36*unit["water_rate"]))
                                values = [w.terminal_penalty_yuan_per_discharge_mwh,w.discharge_energy_factor,
                                          w.deviation_discharge_mwh,w.terminal_penalty_cost,w.value_native]
                                expected = [terminal_coefficient,factor,residual[pos]*factor,
                                            residual[pos]*factor*terminal_coefficient,0.]
                                # CSV exports native state to 4 decimals, so cost
                                # checks propagate only that rounding uncertainty.
                                tolerances = [1e-4,1e-4,1e-4*max(1.,factor),1e-4*max(1.,factor*terminal_coefficient),1e-6]
                                if any(not finite(a) or abs(float(a)-b)>tol for a,b,tol in zip(values,expected,tolerances)):
                                    terminal_evidence_ok = False
                            except (KeyError, ValueError, StopIteration, ZeroDivisionError):
                                terminal_evidence_ok = False
                        if not terminal_evidence_ok:
                            errors.append("软终态政策标记、换算、末值0或罚金与冻结配置不符")
                nonplanned_band_ok = bool(terminal_evidence_ok and np.all(residual[~planned] < 1e-3)
                    and (vw.loc[~planned,"band_slack"] < 1e-6).all())
                if not np.allclose(residual, vw.band_slack, atol=2e-4, rtol=0):
                    errors.append("状态带偏差与松弛明细不一致")
                    nonplanned_band_ok = False
                for kind, label, unit in (("E","储能","MWh"),("Vu","抽蓄","万m3"),("V","水电","万m3")):
                    a = vw[vw.kind == kind]
                    C[f"{label}状态带最大松弛{unit}"] = float(a.band_slack.max()) if len(a) else 0.
                if not band_ok:
                    notes.append("存在价值法状态带/日末目标松弛，不能声明全部目标满足")
        if rs is None or "state_band_slack_max_win" not in rs or not np.isfinite(pd.to_numeric(rs.state_band_slack_max_win, errors="coerce")).all():
            errors.append("rt_system.csv 缺少有限状态带松弛证据")
            band_ok = nonplanned_band_ok = False
        else:
            if rs.state_band_slack_max_win.abs().max() >= 1e-6:
                band_ok = False
            if vw is not None:
                declared = vw.groupby("window_start").band_slack.max().reindex(range(T)).to_numpy()
                observed = rs.sort_values("period").state_band_slack_max_win.to_numpy()
                if not np.allclose(declared, observed, atol=2e-4, rtol=0):
                    errors.append("窗口松弛汇总与状态带逐资源明细不符")
                    nonplanned_band_ok = False
    elif rs is not None and "state_band_slack_max_win" in rs:
        v = pd.to_numeric(rs.state_band_slack_max_win, errors="coerce")
        band_ok = bool(np.isfinite(v).all() and v.abs().max() < 1e-6)
    # This compatibility scalar mixes native units and is NOT a physical MW/MWh measure.
    C["执行时段最大松弛"] = slack
    C["最大松弛口径"] = "窗口松弛分类诊断最大值；不同单位不能相加或作为单一物理量"
    da_slack = (S.get("da_summary") or {}).get("slack") or {}
    da_keys = ("shed_mwh", "surplus_mwh", "line_slack_max", "anchor_slack_max_mwh")
    da_slack_valid = all(finite(da_slack.get(k)) for k in da_keys)
    if not da_slack_valid:
        errors.append("summary.json 缺少有限日前正式松弛证据")
    C["松弛为0"] = bool(slack is not None and slack < 1e-6 and band_ok and da_slack_valid
                           and all(abs(float(da_slack[k])) < 1e-6 for k in da_keys))

    C["非计划松弛为0"] = bool(slack is not None and slack < 1e-6 and nonplanned_band_ok and da_slack_valid
                                and all(abs(float(da_slack[k])) < 1e-6 for k in da_keys))
    bounds_ok = end_ok = simultaneous = True
    terminal_policy_ok = terminal_evidence_ok
    for group, filename, key, stock, powers in (
            ("storage", "storage", "unit", "E_mwh", ("P_dis","P_ch")),
            ("pumped", "pumped", "unit", "V_up", ("P_gen","P_pump")),
            ("hydro", "hydro", "station", "V_1e4m3", ())):
        if not groups[group]:
            continue
        ids = [u["id"] for u in groups[group]]
        rt = table(f"rt_{filename}.csv", ["period",key,stock,*powers], [stock,*powers], key=key, ids=ids)
        da = table(f"da_{filename}.csv", ["period",key,stock], [stock], key=key, ids=ids)
        if rt is None or da is None:
            bounds_ok = end_ok = terminal_policy_ok = False
            if powers: simultaneous = False
            continue
        if powers and ((rt[powers[0]] > 1e-6) & (rt[powers[1]] > 1e-6)).any():
            simultaneous = False
        for u in groups[group]:
            a = rt[rt[key] == u["id"]].sort_values("period")[stock]
            b = da[da[key] == u["id"]].sort_values("period")[stock]
            if group == "storage":
                lower, upper, target = u["soc_min"]*u["ecap"], u["soc_max"]*u["ecap"], b.iloc[-1]
            elif group == "pumped":
                lower, upper = max(u["vup_min"],u["v_total"]-u["vdn_max"]), min(u["vup_max"],u["v_total"]-u["vdn_min"])
                target = b.iloc[-1]
            else:
                ss = u["seasons"][base["season"]]
                lower, upper, target = ss["vmin"],ss["vmax"],ss["v_end"]
                if u.get("regulation") == "run_of_river":
                    lower, upper, target = ss["v0"]-float(u.get("pondage",0) or 0),ss["v0"],ss["v0"]
            if ((a < lower-1e-3) | (a > upper+1e-3)).any() or ((b < lower-1e-3) | (b > upper+1e-3)).any():
                bounds_ok = False; notes.append(f"{u['id']} SOC/库容越界")
            met = a.iloc[-1] >= target-1e-3 if group == "hydro" else abs(a.iloc[-1]-target) < 1e-3
            if not met:
                end_ok = False; notes.append(f"{u['id']} 实际日末存在目标偏差")
                if terminal_mode != "soft" or ({"storage":"E","pumped":"Vu","hydro":"V"}[group],u["id"]) not in value_resources:
                    terminal_policy_ok = False
            if terminal_mode == "soft" and vw is not None and ({"storage":"E","pumped":"Vu","hydro":"V"}[group],u["id"]) in value_resources:
                kind = {"storage":"E","pumped":"Vu","hydro":"V"}[group]
                endpoint = vw[(vw.kind == kind) & (vw.unit == u["id"]) & (vw.tau == T-1)]
                if (endpoint.empty or not np.allclose(endpoint.lower, target, atol=1e-3, rtol=0)
                        or not np.allclose(endpoint.upper, upper if group == "hydro" else target, atol=1e-3, rtol=0)):
                    terminal_policy_ok = False; errors.append(f"{u['id']} 日末软目标不是冻结DA/水电下限")
                actual = endpoint[endpoint.window_start == T-1]
                if len(actual) != 1 or abs(float(actual.state.iloc[0])-float(a.iloc[-1])) > 1e-3:
                    terminal_policy_ok = False; errors.append(f"{u['id']} 已执行日末与软目标明细不一致")
    C["SOC库容边界"], C["日末约束"], C["无同时充放"] = bounds_ok, end_ok, simultaneous
    C["日末目标零偏差"] = end_ok
    C["终态政策合规"] = bool(terminal_policy_ok and bounds_ok)
    if terminal_mode == "soft" and vw is not None and "terminal_penalty_cost" in vw:
        final = vw[(vw.window_start == T-1) & (vw.tau == T-1)]
        C["已执行终态优化罚金元"] = float(pd.to_numeric(final.terminal_penalty_cost,errors="coerce").sum())
        C["终态罚金口径"] = "仅实际日末；优化成本，不是结算付款；不累加前瞻窗口或后缀"

    bus_frame = table("scenario_snapshot/data/case39/buses.csv", ["bus"], ["bus"], grid=False)
    bus_ids = None
    if bus_frame is not None and not bus_frame.bus.duplicated().any():
        bus_ids = set(bus_frame.bus)
    else:
        errors.append("缺少完整冻结网络节点清单")
    prices_ok = bus_ids is not None
    price_frames = {}
    for stage,name in (("日前","da_nodal_prices.csv"),("实时","rt_prices.csv")):
        frame = table(name, ["bus","period","lmp"], ["bus","lmp"], key="bus", ids=bus_ids)
        price_frames[stage] = frame
        C[f"{stage}负价时段数"] = int((frame.groupby("period").lmp.min() < 0).sum()) if frame is not None else None
        if frame is None:
            prices_ok = False
        elif ((frame.lmp < lo-1e-6) | (frame.lmp > hi+1e-6)).any():
            prices_ok = False; notes.append(f"{name} 超出冻结限价 [{lo},{hi}]")
    C["价格限值"] = prices_ok

    rc = table("settlement_reconciliation.csv", ["stage","residual"], ["residual"], grid=False)
    if rc is not None and (set(rc.stage) != {"日前","实时偏差","合计"} or rc.stage.duplicated().any()):
        errors.append("结算对账阶段不全或重复"); rc = None
    be = table("settlement_by_entity.csv", ["entity","total","da_amount","rt_amount"], ["total","da_amount","rt_amount"], grid=False)
    sd = table("settlement_detail.csv", ["entity","period","q_da","q_rt","lmp_da","lmp_rt","total"],
               ["q_da","q_rt","lmp_da","lmp_rt","total"], key="entity")
    required_entities = {u["id"] for g in ("thermal","renewable","hydro") for u in groups[g]}
    required_entities |= {f"{u['id']}_{suffix}" for g,suffixes in (("storage",("放电","充电")),("pumped",("发电","抽水"))) for u in groups[g] for suffix in suffixes}
    required_entities.add("平衡松弛(过剩)")
    if base.get("tie_line_mode", "supply") == "supply":
        required_entities |= {u["id"] for u in groups["tie"]}
    if bus_frame is not None and "load_mw" in bus_frame:
        required_entities |= {f"L{int(bus)}" for bus in bus_frame.loc[
            pd.to_numeric(bus_frame.load_mw, errors="coerce") > 0., "bus"]}
    if be is not None and (be.entity.duplicated().any() or sd is None or set(be.entity) != set(sd.entity)
                           or not required_entities <= set(be.entity)):
        errors.append("结算主体缺失、重复或明细/合计不一致"); be = None
    res = float(rc.residual.abs().max()) if rc is not None else None
    ident = float((be.total-be.da_amount-be.rt_amount).abs().max()) if be is not None else None
    two = float((sd.q_da*dt*sd.lmp_da+(sd.q_rt-sd.q_da)*dt*sd.lmp_rt-sd.total).abs().max()) if sd is not None else None
    C["对账残差元"], C["主体合计恒等式误差元"] = res, max(ident,two) if ident is not None and two is not None else None
    C["两部制恒等式<1元"] = bool(res is not None and ident is not None and two is not None and max(res,ident,two) < 1)

    expected_values = {(kind,u["id"]) for kind,group in (("E","storage"),("Vu","pumped"),("V","hydro")) for u in groups[group]}
    vc = None
    if expected_values:
        vc = table("da_value_curves.csv", ["kind","unit","period","value_native","value_yuan_per_mwh","dual_raw"],
                   ["period","value_native","value_yuan_per_mwh","dual_raw"], grid=False)
        if vc is not None:
            keys = list(zip(vc.kind,vc.unit,vc.period))
            expected = {(k,u,t) for k,u in expected_values for t in periods}
            valid = len(keys) == len(set(keys)) and set(keys) == expected
            if not valid: errors.append("da_value_curves.csv 主体/时段覆盖不全或重复")
            C["价值曲线=−对偶(符号自洽)"] = bool(valid and all(np.allclose(g.sort_values("period").value_native.to_numpy()[:-1],
                -g.sort_values("period").dual_raw.to_numpy()[1:], atol=1e-3, rtol=1e-9) for _,g in vc.groupby(["kind","unit"])))
            neg = vc[vc.value_yuan_per_mwh < -1e-6]
            C["价值曲线负值点数"] = len(neg)
            C["价值曲线负值主体"] = ",".join(sorted(neg.unit.unique()))
    else:
        C["价值曲线=−对偶(符号自洽)"] = True
        C["价值曲线检查适用"] = False
    C["回退次数"] = S.get("realtime", {}).get("fallback_count")
    try:
        ev = json.loads((d/"reserve_window_evidence.json").read_text(encoding="utf-8"))
        if not isinstance(ev.get("da"), dict) or len(ev.get("rt_windows", [])) != T:
            raise ValueError("备用窗口证据不完整")
        reserve_ids = {u["id"] for group in ("thermal","hydro","storage","pumped") for u in groups[group]}
        H = int(round(float(mk["realtime"]["lookahead_h"])/dt))
        for index, window in enumerate([ev["da"], *ev["rt_windows"]]):
            start = 0 if index == 0 else index-1
            length = T if index == 0 else min(H,T-start)
            if (window.get("window_start") != start or window.get("periods") != length
                    or set(window.get("resource_catalog", [])) != reserve_ids):
                raise ValueError("备用窗口时域或主体清单与冻结输入不一致")
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
        from smm.reserve_checks import audit
        A = audit(ev)
        C["备用复核违例数"], C["备用复核最大违例"] = len(A["violations"]), float(A["max_violation"])
        C["备用复核通过"] = bool(A.get("passed", not A["violations"]))
        C.update(_hydro_reserve_water_policy_audit(ev, S, mk, groups['hydro'], base['season'], A))
        if C['水电备用水量已验证'] is None and C['水电备用水量规则'].startswith('历史'):
            notes.append('历史容量备用证据按原口径复核；未核验新增水电上备用水量限制')
        if C['水电备用出库已验证'] is None:
            notes.append(C['水电备用出库规则'])
        C.update(_reserve_shortage_audit(ev, rs, da_slack, mk, T, dt))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        errors.append(f"备用复核未验证：{exc}")
    # New runs disclose failed ID attempts separately; old hard archives without
    # this interface retain their historical, explicitly limited scope.
    intraday = S.get("intraday", {})
    new_id = terminal_mode == "soft" or "failure_count" in intraday
    if new_id and mk.get("intraday", {}).get("enabled", False):
        try:
            fail = _csv(d, "id_failed_rounds.csv")
            if fail is None or not {"tau","phase","solver_status","reason","fallback"} <= set(fail):
                raise ValueError("缺少规范失败ID轮次表")
            ids = _csv(d, "id_rounds_summary.csv")
            if ids is None or "tau" not in ids:
                raise ValueError("缺少成功ID轮次表")
            fts, sts = fail.tau.tolist(), ids.tau.tolist()
            step = int(round(float(mk["intraday"]["interval_h"])/dt))
            if (step <= 0 or len(set(fts+sts)) != len(fts+sts) or set(fts+sts) != set(range(0,T,step))
                    or intraday.get("rounds") != len(sts) or intraday.get("failure_count") != len(fts)
                    or intraday.get("attempted_rounds") != len(fts)+len(sts)
                    or len(intraday.get("failed_rounds", [])) != len(fts)):
                raise ValueError("ID成功/失败/尝试轮次重复、缺失或数量不符")
            status = {"infeasible","terminationcondition.infeasible","kinfeasible","highsmodelstatus.kinfeasible"}
            for record in fail.to_dict("records"):
                saved = next((x for x in intraday.get("failed_rounds", []) if x.get("tau") == record["tau"]), None)
                if saved is None or any(str(saved.get(k)) != str(record[k]) for k in ("phase","solver_status","reason","fallback")):
                    raise ValueError("ID失败表与summary内容不一致")
            if (not fail.phase.isin(["scuc","formal_sced"]).all()
                    or not fail.solver_status.astype(str).str.lower().isin(status).all()
                    or not (fail.fallback == "day_ahead").all() or fail.reason.isna().any()):
                raise ValueError("ID失败类型不允许回退")
            if len(fts):
                if ch.get("intraday_failure_records_valid") is not True or ch.get("intraday_failed_reference_evidence_complete") is not True:
                    raise ValueError("ID失败回退的正式DA来源审计未通过")
                evidence = json.loads((d/"hydro_window_evidence.json").read_text())
                if len(evidence.get("rt_windows", [])) != T:
                    raise ValueError("缺少逐窗ID失败来源证据")
                from types import SimpleNamespace
                from smm.full import intraday_failure_checks
                plan = dict(evidence["da"])
                plan["delta"] = {(x["station"],x["zone"]):x["values"] for x in plan["delta"]}
                plan["state_values"] = {}
                if vc is None:raise ValueError("失败回退缺少DA价值证据")
                for (kind,unit),g in vc.groupby(["kind","unit"]):
                    plan["state_values"].setdefault(kind,{})[unit] = {"value_native":g.sort_values("period").value_native.to_numpy()}
                rounds=[]
                for w in evidence["rt_windows"]:
                    rounds.append(dict(t=w["t"],handoff_config=w.get("handoff_config",{}),
                        reference_metadata=w.get("reference_metadata",{}),
                        thermal_reference_u=w.get("thermal_reference_u",{}),
                        hydro_reference_delta={(x["station"],x["zone"]):x["values"] for x in w.get("hydro_reference_delta",[])},
                        snap=dict(w["snapshot"],value_window=w.get("value_window",[]))))
                result = intraday_failure_checks(SimpleNamespace(T=T,mode=mk,units=groups),
                    SimpleNamespace(id_failures=fail.to_dict("records"),id_rounds=[dict(tau=t) for t in sts],rt=rounds),plan,tolerance=2e-4)
                if not all(result.values()):raise ValueError("失败后参考值、价值、台数或启停不匹配DA")
            C["日内失败回退合规"] = True
            C["日内失败轮数"] = len(fts)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            errors.append(f"日内轮次/回退未验证：{exc}")
            C["日内失败回退合规"] = False
    # New soft/value runs require independently checkable future reference
    # evidence. A positive forecast shortage is not a physical violation.
    lookahead_h = float(mk.get("realtime", {}).get("lookahead_h", 2.))
    interval_min = float(mk.get("realtime", {}).get("interval_min", 15))
    lookahead_required = (terminal_mode == "soft" and mk.get("realtime", {}).get("hydro", "value") == "value"
        and mk.get("realtime", {}).get("value_method", {}).get("hydro_lookahead") == "reference_continuation"
        and bool(groups["hydro"]) and T > int(round(lookahead_h*60/interval_min)))
    C["水电参考续程证据合规"] = not lookahead_required
    if lookahead_required or (d/"hydro_terminal_lookahead_evidence.json").exists():
        try:
            from types import SimpleNamespace
            result = _hydro_lookahead_audit(d,SimpleNamespace(T=T,dt=dt,mode=mk,units=groups,base=base))
            C["水电参考续程证据合规"] = True
            C["水电参考续程已核记录数"] = result["hydro_terminal_lookahead_checked_records"]
            C["水电单条预测短缺最大万m3"] = result["hydro_terminal_lookahead_max_forecast_shortfall_1e4m3"]
            notes.append("水电参考续程只核对预测成本，不累加为实际罚金或宣称全局可达")
        except (OSError,KeyError,ValueError,TypeError,AttributeError,IndexError) as exc:
            errors.append(f"水电参考续程未验证：{exc}")
            C["水电参考续程证据合规"] = False
    C["弃水去退化已验证"] = None
    if "hydro_water_selection" in S or (d/"hydro_water_selection_evidence.json").exists():
        try:
            from smm.hydro_spill_tiebreak import audit_selection_evidence
            selection=json.loads((d/"hydro_water_selection_evidence.json").read_text())
            water=json.loads((d/"hydro_window_evidence.json").read_text())
            stage_evidence=json.loads((d/"stage_diagnostics.json").read_text())
            result=audit_selection_evidence(selection,water,stage_evidence,groups["hydro"],T,S.get("intraday",{}).get("rounds",0))
            if S.get("hydro_water_selection")!=result:
                raise ValueError("弃水去退化summary缺失或与逐窗证据不符")
            C["弃水去退化已验证"] = True
        except (OSError,KeyError,ValueError,TypeError,IndexError) as exc:
            errors.append(f"弃水去退化未验证：{exc}")
            C["弃水去退化已验证"] = False
    C["水电日电量下限合规"] = True
    configured_minima = [u for u in groups["hydro"] if u.get("regulation", "reservoir") == "reservoir" and u.get("e_day_min") is not None]
    if configured_minima:
        try:
            from types import SimpleNamespace
            from smm.hydro_daily_energy import audit_exported
            cfg = SimpleNamespace(T=T, dt=dt, units=groups, base=base, mode=mk)
            hydro_evidence = json.loads((d/"hydro_window_evidence.json").read_text())
            actual_states = json.loads((d/"rolling_actual_states.json").read_text())
            archived_ids = hydro_evidence.get("id_rounds")
            if not isinstance(archived_ids,list) or any(not isinstance(r,dict) or type(r.get("tau")) is not int for r in archived_ids):
                raise ValueError("日电量完整精度ID轮次证据无效")
            expected_ids = (pd.read_csv(d/"id_rounds_summary.csv").tau.tolist()
                            if mk.get("intraday",{}).get("enabled",False) else [])
            if sorted(r["tau"] for r in archived_ids) != sorted(expected_ids):
                raise ValueError("日电量完整精度ID源与成功轮次表不一致")
            frozen_bids = _frozen_resolved_bids(d)
            failed_ids = (pd.read_csv(d/"id_failed_rounds.csv").to_dict("records")
                          if mk.get("intraday",{}).get("enabled",False) else [])
            result = audit_exported(cfg, hydro_evidence, actual_states, bids=frozen_bids, id_failures=failed_ids)
            if (result.get("hydro_daily_minimum_evidence_valid") is not True
                    or result.get("hydro_daily_minimum_final_satisfied") is not True):
                raise ValueError("完整精度日电量/累计/续程证据未通过：" + str(result.get("hydro_daily_minimum_errors", [])))
            saved = S.get("hydro_daily_minimum")
            if (not isinstance(saved,dict) or saved.get("active") is not True
                    or saved.get("evidence_valid") is not True or saved.get("full_day") is not True
                    or saved.get("final_satisfied") is not True):
                raise ValueError("摘要未如实记录已完成的全天日电量下限合规")
            csv_result = _csv_daily_minimum_audit(d, cfg)
            C["水电日电量下限逐主体"] = csv_result["records"]
        except (OSError,KeyError,ValueError,TypeError,AttributeError,IndexError,ImportError) as exc:
            errors.append(f"水电日电量下限未验证：{exc}")
            C["水电日电量下限合规"] = False
    C["松弛为0"] = bool(C["松弛为0"] and C["备用缺额证据完整"] and C["零备用缺额"])
    C["非计划松弛为0"] = bool(C["非计划松弛为0"] and C["备用缺额证据完整"] and C["零备用缺额"])
    if C["备用缺额证据完整"] and not C["零备用缺额"]:
        notes.append("存在备用缺额；备用公式复核通过不等于零缺额，严格无松弛验收不通过")
    C["证据完整"] = not errors
    C["验收通过"] = bool(not errors and all(C[k] for k in ("流程未中断","功率平衡","非计划松弛为0","无同时充放",
                         "SOC库容边界","终态政策合规","价格限值","两部制恒等式<1元","价值曲线=−对偶(符号自洽)","备用复核通过")))
    C["说明"] = "；".join(errors+notes)
    return C


def render_check(d: Path) -> dict:
    """用 Streamlit AppTest 渲染结果页，检查三大块（日前 / 日内·实时滚动 / 最终结果）均出现且无异常。"""
    import sys
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "app"))
    import common
    from streamlit.testing.v1 import AppTest
    original_results = common.all_results
    try:
        common.all_results = lambda: [dict(dir=str(d.resolve()), scenario=d.name, mode="full", status="done")]
        at = AppTest.from_file(str(root / "app/pages/3_结果.py"), default_timeout=180).run()
    finally:
        common.all_results = original_results
    labels = [t.label for t in at.tabs]
    from result_navigation import MAIN_SECTIONS
    need = list(MAIN_SECTIONS)
    C = dict(渲染异常数=len(at.exception), 页签数=len(labels), 图表数=len(at.get("plotly_chart")))
    C["三大块渲染"] = not at.exception and all(x in labels for x in need)
    if at.exception:
        C["渲染异常"] = str(at.exception[0].value)[:200]
    return C


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--out", default=None)
    ap.add_argument("--render", action="store_true", help="额外用 AppTest 渲染结果页检查三大块")
    a = ap.parse_args()
    rows = []
    for item in a.dirs:
        path, _, name = item.partition("=")
        d = Path(path)
        row = dict(场景=name or d.name, 目录=str(d))
        try:
            row.update(metrics(d)); row.update(checks(d))
            if a.render:
                rendered = render_check(d)
                row.update(rendered)
                count = rendered.get("渲染异常数")
                render_ok = (isinstance(count, (int, np.integer)) and not isinstance(count, bool)
                             and count == 0 and rendered.get("三大块渲染") is True)
                row["渲染通过"] = render_ok
                row["验收通过"] = row.get("验收通过") is True and render_ok
                if not render_ok:
                    row["说明"] = "；".join(filter(None, (row.get("说明"), "请求的页面渲染未通过或证据不完整")))
        except Exception as exc:  # noqa: BLE001 记录失败项，不中断批量
            row["错误"] = f"{type(exc).__name__}: {exc}"
            row["验收通过"] = False
        rows.append(row)
    df = pd.DataFrame(rows)
    if a.out:
        df.to_csv(a.out, index=False, encoding="utf-8-sig")
    with pd.option_context("display.max_columns", None, "display.width", 250):
        print(df.T.to_string())
    return 0 if all(row.get("验收通过") is True for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
