"""Reproducible, explicitly selected bid-price previews (research assumptions).

This module never saves files or mutates the supplied draft. Only energy prices
of selected price_quantity resources change; quantities, modes, technical limits,
SOC/volume declarations and startup/no-load costs remain as supplied. Applying a
preview over unsaved manual edits is a separate, explicit UI action. The caller
freezes the resulting submission once for a scenario/run and reuses it for every
RT window; this is cross-resource scenario heterogeneity, not time-varying rebids.
"""
from __future__ import annotations

import copy
import hashlib
import math
from decimal import Decimal
from numbers import Integral
from typing import Iterable

from .bids import _unit_meta, bid_rules_for_type, default_bids, raise_on_errors, validate_bids
from .config import Config


# Maximum additive price shifts (yuan/MWh): scenario assumptions, not estimated
# cost distributions, observed offers, plant identities, or trading recommendations.
PRICE_SHIFT_LIMITS = {
    "coal": 40.0,
    "gas_ccgt": 60.0,
    "gas_gt": 70.0,
    "hydro": 30.0,
    "wind": 20.0,
    "pv": 20.0,
    "storage": 40.0,
    "pumped": 40.0,
}
ALGORITHM_VERSION = "selected-type-price-translation-v1"


def _ids(values: Iterable[str], name: str) -> set[str]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{name} 须为资源ID列表，不是单个字符串")
    try:
        result = set(values)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 须为资源ID列表") from exc
    if any(not isinstance(v, str) for v in result):
        raise ValueError(f"{name} 只能包含字符串资源ID")
    return result


def simulate_bids(
    cfg: Config,
    *,
    selected_unit_ids: Iterable[str],
    seed: int,
    base_bids: dict | None = None,
    protected_unit_ids: Iterable[str] = (),
) -> dict:
    """Return a validated, detached preview for explicitly selected resources.

    Same cfg/base/seed/selection gives identical output; each resource's price
    shift is independent of selection order or the presence of other selections.
    ``protected_unit_ids`` provides a guard for manual drafts: overlap raises an
    error rather than overwriting or silently skipping those resources.

    Price translations are type-specific and bounded by applicable bid rules.
    The same shift is applied to both sides of storage/pumped bids, preserving
    their buy/sell spread and ordering. No price is clipped. A fully pinned curve
    that admits no nonzero translation is rejected explicitly. Non-price modes
    are not silently converted to price_quantity.
    """
    if not isinstance(seed, Integral) or isinstance(seed, bool) or seed < 0:
        raise ValueError("模拟seed须为非负整数")
    seed = int(seed)
    selected = _ids(selected_unit_ids, "selected_unit_ids")
    protected = _ids(protected_unit_ids, "protected_unit_ids")
    if not selected:
        raise ValueError("请明确选择至少一个资源后再生成模拟报价")
    overlap = selected & protected
    if overlap:
        raise ValueError(f"以下资源含受保护的手工编辑，禁止覆盖：{', '.join(sorted(overlap))}")
    meta = _unit_meta(cfg)
    unknown = selected - set(meta)
    if unknown:
        raise ValueError(f"未知或停用资源：{', '.join(sorted(unknown))}")

    draft = copy.deepcopy(default_bids(cfg) if base_bids is None else base_bids)
    if base_bids is None:
        # Preview generation has no wall-clock side effect; save_bids supplies a
        # real creation timestamp only when the user explicitly saves a version.
        draft.pop("created", None)
    raise_on_errors(validate_bids(cfg, draft, require_complete=True))
    changes = []
    for uid in sorted(selected):
        unit = draft["units"][uid]
        typ = meta[uid]["type"]
        if unit.get("mode", "price_quantity") != "price_quantity":
            raise ValueError(f"{uid} 当前为 {unit.get('mode')} 模式；模拟价格不会擅自改为报量报价，请先明确切换模式")
        curves = ("dis_segments", "ch_segments") if typ == "storage" else (
            ("gen_segments", "pump_segments") if typ == "pumped" else ("segments",)
        )
        values = [float(seg["price"]) for curve in curves for seg in unit[curve]]
        rules = bid_rules_for_type(cfg, typ)
        amplitude = PRICE_SHIFT_LIMITS[typ]
        low = max(-amplitude, float(rules["price_min"]) - min(values))
        high = min(amplitude, float(rules["price_max"]) - max(values))
        quantum = float(rules["price_step"] or 0.01)
        lower = math.ceil((low - 1e-9) / quantum)
        upper = math.floor((high + 1e-9) / quantum)
        zero_present = lower <= 0 <= upper
        count = upper - lower + 1 - int(zero_present)
        if count <= 0:
            raise ValueError(f"{uid} 当前曲线已被报价边界/步长完全限制，无法生成非零平移；请显式编辑或换其他资源")
        digest = hashlib.sha256(f"{ALGORITHM_VERSION}|{seed}|{uid}|{typ}".encode()).digest()
        k = lower + int.from_bytes(digest, "big") % count
        if zero_present and k >= 0:
            k += 1
        shift_decimal = Decimal(str(quantum)) * k
        shift = float(shift_decimal)
        for curve in curves:
            for segment in unit[curve]:
                segment["price"] = float(Decimal(str(segment["price"])) + shift_decimal)
        changes.append({"unit_id": uid, "type": typ, "price_shift_yuan_per_mwh": shift,
                        "assumed_max_abs_shift_yuan_per_mwh": amplitude,
                        "curves": list(curves)})
    raise_on_errors(validate_bids(cfg, draft, require_complete=True))
    draft["_simulation"] = {
        "algorithm": ALGORITHM_VERSION,
        "seed": seed,
        "selected_unit_ids": sorted(selected),
        "scope": "energy_price_preview_only; quantities_and_technical_fields_unchanged; not_saved",
        "submission_policy": "freeze_once_per_scenario_and_reuse_all_RT_windows;not_random_rebidding",
        "assumption": "type-specific bounded additive price shifts; not observed market offers",
        "changes": changes,
    }
    return draft
