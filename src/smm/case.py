"""把配置、网络、预测曲线和报价组装成一个出清算例（日前）。"""
from __future__ import annotations

from dataclasses import dataclass, field
import copy

import numpy as np
import pandas as pd

from .config import Config
from .network import Network


@dataclass
class DACase:
    cfg: Config
    net: Network
    profiles: pd.DataFrame
    bids: dict
    load_bus: dict          # bus -> np.array(T) 日前短期预测负荷（MW）
    ren_fc: dict            # id -> np.array(T) 日前短期预测（出力上限）
    tie: dict               # id -> (bus, np.array(T))
    R_up: np.ndarray
    R_dn: np.ndarray
    reserve_info: dict
    # ---- 以下用于日内/实时滚动窗口（日前出清全部取默认值）
    T: int | None = None                 # 窗口时段数；None = cfg.T（全天）
    t0: int = 0                          # 窗口首时段在全天中的序号
    # init may be a declared research, forecast, or executed state; source is explicit.
    # hydro_commitment carries per-turbine ages and absolute required_until.
    init: dict | None = None             # 初始状态：thermal{g:{online,p0,hours}}, storage{s:E0}, hydro{h:V0}, hydro_p{j:实际P0}, hydro_online{j:实际在线台数}, hydro_hist{h:出库流量历史}, pumped{p:{V,ng,np,xg,xp}}
    fixed: dict = field(default_factory=dict)   # 固定整数变量：{"u": {(g,t): 0/1}, "delta": {...}, "u_ch": ..., "u_dis": ...}
    hydro_e_budget: dict | None = None   # 水电剩余日电量上限 MWh；None = 季节 e_day
    terminal: dict | None = None         # None = 日前（申报期末 SOC 硬约束、V≥v_end）；实时：{storage_anchor:{s:[(t,lo,hi)]}, anchor_penalty, fix_power:{Ph/Pd/Pc:{idx:val}}, hydro_vend, hydro_window_end:{h:同钟点DA库容硬等值}}
    stage: str = "DA"
    input_provider: object | None = None  # causal source service; never used inside solver expressions
    selected_inputs: object | None = None  # immutable issue-bound full remaining-day input
    day_start_utc: str | None = None
    fixed_load: dict = field(default_factory=dict)  # source@bus -> (bus, fixed demand MW); not a bid/DR resource
    actual_state_required: bool = False  # admitted continuous-day cases may never reset from cfg


def reserve_requirement(cfg: Config, load_total: np.ndarray, ren_total: np.ndarray, extra_n1: float | None = None):
    """工程备用规则 (12)：R⁺ = ρ_L·D̂ + ρ_RE·P̂_RE + R_ctg；R⁻ = ρ⁻_L·D̂ + ρ⁻_RE·P̂_RE。"""
    from .input_validation import validate_reserve_configuration, _nonnegative
    validate_reserve_configuration(cfg)
    if extra_n1 is not None:
        _nonnegative(extra_n1, 'extra_n1')
    r = cfg.mode["reserve"]
    units = [u["pmax"] for u in cfg.units["thermal"]] + [h["unit_pmax"] for h in cfg.units["hydro"]] \
        + [max(u["unit_gen_max"], u["unit_pump"]) for u in cfg.units.get("pumped", [])]   # 抽蓄单机发电/抽水失去同样构成功率缺额
    max_unit = float(max(units, default=0.0))
    n1 = float(cfg.base.get("n1_loss_mw") or 0.0) if extra_n1 is None else extra_n1
    r_ctg = max(n1, max_unit) if r["include_ctg"] else 0.0
    up = r["rho_L"] * load_total + r["rho_RE"] * ren_total + r_ctg
    # An explicitly upward-only research scenario procures no downward reserve.
    dn = (r["rho_L_dn"] * load_total + r["rho_RE_dn"] * ren_total
          if bool(r.get("down_enabled", True)) else np.zeros_like(load_total, dtype=float))
    return up, dn, dict(max_unit=max_unit, n1_loss=n1, R_ctg=r_ctg, **r)


def build_da_case(cfg: Config, net: Network, profiles: pd.DataFrame, bids: dict, *, init: dict | None = None,
                  require_complete_bids: bool = False, require_actual_state: bool = False,
                  input_provider=None, selected_inputs=None, day_start_utc=None) -> DACase:
    # Public Python case-building entrypoint must enforce the same rules as file/UI input.
    from .input_validation import validate_physical_inputs, validate_network_inputs
    from .forecast import validate_profiles
    if require_actual_state:
        if init is None:
            raise ValueError('Continuous-day DA requires complete actual initial state')
        from .state_bridge import state_from_case_init
        state_from_case_init(cfg, init)
    validate_physical_inputs(cfg, init=init)
    from .bids import validate_bids, raise_on_errors, fill_missing_bids
    raise_on_errors(validate_bids(cfg, bids, require_complete=require_complete_bids))
    validate_network_inputs(cfg, net)
    from .natural_inflow import is_causal, validate_bid_availability
    if selected_inputs is None and input_provider is not None:
        if day_start_utc is None: raise ValueError('causal DA requires explicit absolute day start')
        selected_inputs=input_provider.select_day(day_start_utc,0,0,stage='DA',executed_through_period=0)
    if is_causal(cfg) != (selected_inputs is not None):
        raise ValueError('causal model and selected source must be explicitly enabled together')
    if selected_inputs is None:
        validate_profiles(cfg, profiles)
    else:
        from .causal_types import utc_seconds, utc_iso
        if cfg.dt!=.25 or cfg.T!=96 or day_start_utc is None:
            raise ValueError('causal DA supports 96 x 15min with an explicit clock')
        if selected_inputs.start_utc!=utc_iso(day_start_utc) or selected_inputs.end_utc!=utc_iso(utc_seconds(day_start_utc)+86400):
            raise ValueError('DA source must cover the complete operating day')
        if any(x!='issued_forecast' for x in selected_inputs.sources):
            raise ValueError('DA cannot receive the current-interval RT observation exception')
        expected={'load'}|{r['id'] for r in cfg.units['renewable']}|{h['id'] for h in cfg.units['hydro']}
        if {r.resource_id for r in selected_inputs.series}!=expected:
            raise ValueError('causal input resource identities differ from the case')
        if input_provider is None or input_provider.selection(selected_inputs.content_hash)!=selected_inputs:
            raise ValueError('causal DA selected inputs must match their frozen source registry')
        from .fixed_load import tie_mode as _tie_mode
        if _tie_mode(cfg)=='fixed_load' and any(f"{u['id']}_{stage}" in profiles for u in cfg.units.get('tie',[]) for stage in ('actual','da','id','rt')):
            raise ValueError('v3 fixed tie demand requires an explicit public schedule, not legacy four-stage curves')
        # A solver-facing case contains no future actual/id/rt truth-conditioned columns.
        profiles=profiles[[c for c in profiles if not c.endswith(('_actual','_da','_id','_rt'))]].copy()
        profiles['load_da']=selected_inputs.values('load',kind='load')
        for r in cfg.units['renewable']:
            values=selected_inputs.values(r['id'],kind=r['type'])
            if (values>float(r['cap'])+1e-8).any(): raise ValueError('forecast exceeds technical renewable capacity')
            profiles[f"{r['id']}_da"]=values
        profiles.attrs['forecast_model']='causal_physical_v3'
    bids = copy.deepcopy(bids) if require_complete_bids else fill_missing_bids(cfg, copy.deepcopy(bids))
    raise_on_errors(validate_bids(cfg, bids))
    validate_bid_availability(cfg,bids,selected_inputs)
    load_da = profiles["load_da"].to_numpy()
    from .fixed_load import nodal_demand, tie_mode
    load_bus, fixed_load = nodal_demand(cfg, net, profiles, load_da, selected_inputs=selected_inputs)
    ren_fc = {}
    for r in cfg.units["renewable"]:
        fc = profiles[f"{r['id']}_da"].to_numpy().copy()
        av = bids["units"].get(r["id"], {}).get("avail_pmax")
        if av:  # 报价中给出的 96 点申报预测优先
            fc = np.asarray(av, float).copy()
        ren_fc[r["id"]] = fc
    tie = {t["id"]: (t["bus"], profiles[f"{t['id']}_sched"].to_numpy()) for t in cfg.units.get("tie", [])} if tie_mode(cfg) == "supply" else {}
    ren_total = sum(ren_fc.values())
    up, dn, info = reserve_requirement(cfg, sum(load_bus.values()) if tie_mode(cfg) == "fixed_load" else load_da, ren_total)
    return DACase(cfg, net, profiles, bids, load_bus, ren_fc, tie, up, dn, info,
                  init=copy.deepcopy(init), fixed_load=fixed_load, actual_state_required=bool(require_actual_state),
                  input_provider=input_provider, selected_inputs=selected_inputs, day_start_utc=day_start_utc)
