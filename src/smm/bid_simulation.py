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

from .bids import _unit_meta, bid_rules_for_type, default_bids, legacy_default_bids, raise_on_errors, validate_bids
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
    # A random translation of an already cost-derived baseline is not a fresh
    # cost preview. Preserve the prior derivation as history, not active basis.
    previous_basis = draft.pop('_cost_basis', None)
    if previous_basis is not None:
        history = draft.setdefault('_cost_basis_history', [])
        if not isinstance(history, list):
            raise ValueError('历史成本报价依据须为列表')
        if previous_basis not in history:
            history.append(previous_basis)
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


def simulate_cost_based_bids(
    cfg: Config, *, worksheet: dict, selected_unit_ids: Iterable[str],
    base_bids: dict | None = None, protected_unit_ids: Iterable[str] = (),
    strategy_seed: int | None = None,
) -> dict:
    """Explicit cost-anchor preview; never enabled by default or saved implicitly.

    Translate each curve preserving its quantities/incremental shape. Thermal
    anchor is its first market segment (not the internal 0..Pmin block); the
    internal block receives the SAME shift to preserve whole-curve ordering.
    Anchors round HALF_UP to the declared bid tick (0.01 if none), explicitly
    recorded. Out-of-range results fail validation; prices are NEVER clipped.
    Optional legacy seeded strategy translation is a separate recorded step.
    """
    from decimal import ROUND_HALF_UP
    from .bid_cost_basis import cost_basis_anchors

    selected = _ids(selected_unit_ids, 'selected_unit_ids')
    protected = _ids(protected_unit_ids, 'protected_unit_ids')
    if not selected:
        raise ValueError('请明确选择至少一个资源')
    if selected & protected:
        raise ValueError('受保护的手工编辑禁止覆盖: ' + ', '.join(sorted(selected & protected)))
    if not isinstance(worksheet, dict) or not selected <= set(worksheet):
        raise ValueError('每个所选资源必须有显式cost basis worksheet')
    anchors = cost_basis_anchors(cfg, {uid: worksheet[uid] for uid in sorted(selected)})
    draft = copy.deepcopy(default_bids(cfg) if base_bids is None else base_bids)
    if base_bids is None:
        draft.pop('created', None)
    raise_on_errors(validate_bids(cfg, draft, require_complete=True))
    meta = _unit_meta(cfg)
    changes = []
    for uid in sorted(selected):
        unit = draft['units'][uid]
        if unit.get('mode', 'price_quantity') != 'price_quantity':
            raise ValueError(f'{uid}: cost basis不能擅自切换非报量报价模式')
        typ = anchors[uid]['type']
        tick = Decimal(str(bid_rules_for_type(cfg, typ)['price_step'] or 0.01))
        for curve, anchor in anchors[uid]['curve_anchors_yuan_per_mwh'].items():
            segments = unit[curve]
            index = 0
            if typ in ('coal', 'gas_ccgt', 'gas_gt') and meta[uid]['pmin'] > 0:
                index = next((i for i, seg in enumerate(segments)
                              if seg['p_start'] >= meta[uid]['pmin'] - 1e-8), -1)
                if index < 0:
                    raise ValueError(f'{uid}: 缺少独立Pmin以上市场段，无法应用成本锚点')
            rounded = (Decimal(str(anchor)) / tick).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * tick
            shift = rounded - Decimal(str(segments[index]['price']))
            for seg in segments:
                seg['price'] = float(Decimal(str(seg['price'])) + shift)
            changes.append({'unit_id': uid, 'curve': curve, 'anchor_segment_index': index,
                            'unrounded_anchor_yuan_per_mwh': anchor,
                            'rounded_anchor_yuan_per_mwh': float(rounded),
                            'price_shift_yuan_per_mwh': float(shift)})
    raise_on_errors(validate_bids(cfg, draft, require_complete=True))
    # Prior preview metadata must not masquerade as a newly applied strategy.
    draft.pop('_simulation', None)
    if strategy_seed is not None:
        draft = simulate_bids(cfg, selected_unit_ids=selected, seed=strategy_seed,
                              base_bids=draft, protected_unit_ids=protected)
    draft['_cost_basis'] = {
        'algorithm': 'explicit-per-unit-cost-anchor-v1',
        'worksheet': copy.deepcopy({uid: worksheet[uid] for uid in sorted(selected)}),
        'anchors': anchors, 'changes': changes,
        'rounding': 'nearest bid tick, ROUND_HALF_UP; never clipping',
        'strategy_seed': strategy_seed,
        'scope': 'offer rationale only; not extra objective/accounting costs; no startup/no-load inference',
        'submission_policy': 'freeze once and reuse all RT windows; not saved',
    }
    return draft


def research_cost_bid_preview(
    cfg: Config, *, selected_unit_ids: Iterable[str], base_bids: dict | None = None,
    protected_unit_ids: Iterable[str] = (), strategy_seed: int | None = None,
) -> dict:
    """Apply the explicit synthetic worksheet to selected units only, without saving.

    Unlike the shape-preserving cost-anchor API this proposal explicitly replaces
    price increments with cost/strategy decompositions. Segment quantities remain
    unchanged. Actual cost anchors come only from the shared fixed-cost profile;
    worksheet ladders/opportunity values and seeded translations are offer strategies,
    not actual operating costs. Every thermal internal 0..Pmin block is priced at its base energy
    cost; the fixed no-load intercept remains unchanged (no double-counting).
    Configured ticks use HALF_UP rounding; duplicates never get artificial bumps.
    Renewable strategy increments explicitly follow the existing 10-yuan grid;
    underlying VOM may quantize equally across units. No rules are modified here.
    """
    from decimal import ROUND_HALF_UP
    from .bid_cost_basis import research_cost_worksheet
    from .input_validation import validate_physical_inputs
    from .profit import load_fixed_cost_profile

    validate_physical_inputs(cfg)
    selected = _ids(selected_unit_ids, 'selected_unit_ids')
    protected = _ids(protected_unit_ids, 'protected_unit_ids')
    if not selected:
        raise ValueError('请明确选择至少一个资源')
    if selected & protected:
        raise ValueError('受保护的手工编辑禁止覆盖: ' + ', '.join(sorted(selected & protected)))
    meta = _unit_meta(cfg)
    if not selected <= set(meta):
        raise ValueError('所选资源未知或停用')
    sheet = research_cost_worksheet(cfg)
    fixed = load_fixed_cost_profile(cfg)
    if not fixed['available']:
        raise ValueError('缺少显式fixed_costs.yaml；成本报价不会回退到报价或旧研究档案')
    for uid in selected:
        if uid not in fixed['units'] or fixed['units'][uid]['type'] != meta[uid]['type']:
            raise ValueError(f'{uid}: 缺少匹配类型的独立核算成本记录')
    draft = copy.deepcopy(legacy_default_bids(cfg) if base_bids is None else base_bids)
    if base_bids is None:
        draft.pop('created', None)
    raise_on_errors(validate_bids(cfg, draft, require_complete=True))
    specs = {u['id']: u for family in ('storage', 'pumped') for u in cfg.units.get(family, [])}
    changes = []
    for uid in sorted(selected):
        typ = meta[uid]['type']; unit = draft['units'][uid]
        if unit.get('mode', 'price_quantity') != 'price_quantity':
            raise ValueError(f'{uid}: 成本方案不能擅自切换非报量报价模式')
        actual = fixed['units'][uid]
        details = {}
        if typ in ('coal', 'gas_ccgt', 'gas_gt'):
            if uid not in sheet['thermal']: raise ValueError(f'{uid}: 缺少显式研究成本记录')
            d = sheet['thermal'][uid]
            original_derivation = d['fuel_price'] * d['heat_rate'] + d['variable_om']
            marginal = actual['scalar_marginal_cost_yuan_per_mwh']
            # Scalar physical cost remains constant; these factors are solely
            # OFFER STRATEGY and must never be integrated as actual costs.
            markups = [marginal * (k - 1) for k in sheet['thermal_offer_markup_factors']]
            market = [marginal + markup for markup in markups]
            curves = {'segments': ([market[0]] + market)}
            if not (len(unit['segments']) == 6 and unit['segments'][0]['p_start'] == 0
                    and abs(unit['segments'][0]['p_end'] - meta[uid]['pmin']) < 1e-8):
                raise ValueError(f'{uid}: 研究方案需要内部0..Pmin段和5个市场段，不自动重划容量')
            details = {'fixed_marginal_cost_yuan_per_mwh': marginal,
                       'original_hhv_derived_marginal_cost_yuan_per_mwh': original_derivation,
                       'fixed_scalar_overrides_original_derivation': marginal != original_derivation,
                       'offer_strategy_markups_yuan_per_mwh': markups,
                       'cost_scope': 'constant fixed profile marginal cost; segment markups are offers only',
                       'fuel_hhv_yuan_per_gj': d['fuel_price'], 'heat_rate_hhv_gj_per_mwh': d['heat_rate'],
                       'heat_rate_basis': sheet.get('thermal_heat_rate_basis', 'legacy_unspecified_not_claimed_average'),
                       'incremental_energy_ratio_not_average_efficiency': 3.6 / d['heat_rate'],
                       'variable_om_yuan_per_mwh': d['variable_om'],
                       'base_convention': 'priced_minimum_output_energy_plus_fixed_intercept',
                       'fixed_intercept_yuan_per_h': unit['noload_cost'],
                       'pmin_energy_yuan_per_h': meta[uid]['pmin'] * market[0]}
        elif typ in ('wind', 'pv'):
            if uid not in sheet['renewable_vom']: raise ValueError(f'{uid}: 缺少显式研究成本记录')
            vom = actual['scalar_marginal_cost_yuan_per_mwh']
            curves = {'segments': [vom + k for k in sheet['renewable_strategy_increments']]}
            details = {'variable_om_yuan_per_mwh': vom, 'strategy_increments_yuan_per_mwh': sheet['renewable_strategy_increments']}
        elif typ == 'hydro':
            if uid not in sheet['hydro']: raise ValueError(f'{uid}: 缺少显式研究成本记录')
            d = sheet['hydro'][uid]
            if sheet['season'] not in d['water_opportunity']: raise ValueError('成本方案season须为wet或dry')
            water = d['water_opportunity'][sheet['season']]
            curves = {'segments': [actual['scalar_marginal_cost_yuan_per_mwh'] + water + k for k in sheet['hydro_opportunity_increments']]}
            details = {'variable_om_yuan_per_mwh': actual['scalar_marginal_cost_yuan_per_mwh'], 'water_opportunity_yuan_per_mwh': water,
                       'opportunity_increments_yuan_per_mwh': sheet['hydro_opportunity_increments']}
        elif typ in ('storage', 'pumped'):
            family = 'storage' if typ == 'storage' else 'pumped'
            if uid not in sheet[family]: raise ValueError(f'{uid}: 缺少显式研究成本记录')
            d = sheet[family][uid]; spec = specs[uid]
            eta = spec['eta_c'] * spec['eta_d'] if typ == 'storage' else spec['e_gen'] / spec['e_pump']
            expense = (actual['discharge_degradation_yuan_per_mwh'] if typ == 'storage'
                       else actual['generation_vom_yuan_per_mwh'])
            replacement = d['replacement_energy'] / eta + expense
            sell = max(replacement, d['reservation'])
            buy = eta * (d['reservation'] - expense)
            curves = ({'dis_segments': [sell + k for k in sheet['storage_sell_increments']],
                       'ch_segments': [buy - k for k in sheet['storage_buy_decrements']]}
                      if typ == 'storage' else {'gen_segments': [sell + k for k in sheet['pumped_sell_increments']],
                                               'pump_segments': [buy]})
            details = {'fixed_discharge_expense_yuan_per_mwh': expense,
                       'technical_roundtrip_efficiency': eta, 'replacement_cost_including_losses': replacement,
                       'reservation_yuan_per_mwh': d['reservation'], 'discharge_anchor': sell,
                       'charge_willingness_anchor': buy,
                       'formula': 'sell=max(replacement/eta+expense,reservation); buy=eta*(reservation-expense)'}
        else:
            raise ValueError(f'{uid}: unsupported type')
        tick = Decimal(str(bid_rules_for_type(cfg, typ)['price_step'] or .01))
        for curve, raw in curves.items():
            if len(unit[curve]) != len(raw):
                raise ValueError(f'{uid}.{curve}: 研究方案段数不匹配，不自动重划容量')
            rounded = [float((Decimal(str(v)) / tick).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * tick) for v in raw]
            for segment, value in zip(unit[curve], rounded): segment['price'] = value
            changes.append({'unit_id': uid, 'type': typ, 'curve': curve,
                            'unrounded_prices_yuan_per_mwh': raw, 'rounded_prices_yuan_per_mwh': rounded,
                            'tick_yuan_per_mwh': float(tick), 'basis': details})
    raise_on_errors(validate_bids(cfg, draft, require_complete=True))
    draft.pop('_simulation', None)
    if strategy_seed is not None:
        draft = simulate_bids(cfg, selected_unit_ids=selected, seed=strategy_seed,
                              base_bids=draft, protected_unit_ids=protected)
    draft['_cost_basis'] = {'algorithm': 'fixed-cost-plus-offer-strategy-v2', 'worksheet': sheet,
                            'fixed_cost_profile_sha256': fixed['sha256'],
                            'fixed_cost_parameters': copy.deepcopy({uid: fixed['units'][uid] for uid in sorted(selected)}),
                            'fixed_cost_source': fixed['source'],
                            'selected_unit_ids': sorted(selected), 'changes': changes,
                            'strategy_seed': strategy_seed, 'rounding': 'ROUND_HALF_UP; no clipping/no artificial tie breaking',
                            'submission_policy': 'freeze once and reuse all RT windows; not saved'}
    return draft
