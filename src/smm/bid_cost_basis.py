"""Explicit per-unit offer anchors, not defaults or inferred real-plant costs.

All monetary parameters are CNY; thermal fuel uses GJ and energy uses MWh.
Every supplied parameter needs provenance stating source and assumption status.
Storage replacement energy is an opportunity-cost rationale for offers, NOT an
extra cost to add again to dispatch or settlement accounting. No technical data,
startup/no-load declarations, bids, or files are changed by this module.
"""
from __future__ import annotations

from .bids import _unit_meta
from .input_validation import number, validate_physical_inputs


def cost_basis_anchors(cfg, worksheet: dict) -> dict:
    """Return deterministic anchors/decomposition for explicitly supplied units.

    Record schema: ``{parameters: {name: number}, provenance: {name:
    {source: nonempty string, status: assumed|reference|observed}}}``.
    No missing cost is silently treated as zero. Charge willingness is an
    independently declared input, not inferred from discharge replacement cost.
    """
    validate_physical_inputs(cfg)
    if not isinstance(worksheet, dict) or not worksheet:
        raise ValueError('cost basis worksheet须为非空资源对象')
    meta = _unit_meta(cfg)
    specs = {u['id']: u for family in ('storage', 'pumped') for u in cfg.units.get(family, [])}
    result = {}
    for uid, record in worksheet.items():
        if uid not in meta:
            raise ValueError(f'{uid}: cost basis未知或停用资源')
        if not isinstance(record, dict):
            raise ValueError(f'{uid}: cost basis记录须为对象')
        typ = meta[uid]['type']
        if typ in ('coal', 'gas_ccgt', 'gas_gt'):
            required = {'fuel_price_yuan_per_gj', 'heat_rate_gj_per_mwh', 'variable_om_yuan_per_mwh'}
        elif typ == 'hydro':
            required = {'water_opportunity_yuan_per_mwh', 'variable_om_yuan_per_mwh'}
        elif typ in ('wind', 'pv'):
            required = {'variable_om_yuan_per_mwh'}
        elif typ in ('storage', 'pumped'):
            required = {'replacement_energy_yuan_per_mwh', 'variable_om_yuan_per_mwh',
                        'degradation_yuan_per_mwh', 'charge_willingness_yuan_per_mwh'}
        else:
            raise ValueError(f'{uid}: unsupported cost basis type {typ}')
        params = record.get('parameters')
        provenance = record.get('provenance')
        if not isinstance(params, dict) or set(params) != required:
            raise ValueError(f'{uid}: parameters须完整且仅包含 {sorted(required)}')
        if not isinstance(provenance, dict) or set(provenance) != required:
            raise ValueError(f'{uid}: 每个参数须有provenance来源与假设状态')
        values = {}
        for key in sorted(required):
            value = number(params[key], f'{uid}.{key}')
            signed = key in ('replacement_energy_yuan_per_mwh', 'charge_willingness_yuan_per_mwh',
                             'water_opportunity_yuan_per_mwh')
            if (not signed and value < 0) or (key == 'heat_rate_gj_per_mwh' and value <= 0):
                raise ValueError(f'{uid}.{key}: 成本须非负、热耗须正数')
            source = provenance[key]
            if (not isinstance(source, dict) or not isinstance(source.get('source'), str)
                    or not source['source'].strip() or source.get('status') not in ('assumed', 'reference', 'observed')):
                raise ValueError(f'{uid}.{key}: provenance须含source与status(assumed/reference/observed)')
            values[key] = value
        components = {'variable_om': values['variable_om_yuan_per_mwh']}
        if typ in ('coal', 'gas_ccgt', 'gas_gt'):
            components['fuel'] = values['fuel_price_yuan_per_gj'] * values['heat_rate_gj_per_mwh']
        elif typ == 'hydro':
            components['water_opportunity'] = values['water_opportunity_yuan_per_mwh']
        elif typ in ('storage', 'pumped'):
            spec = specs[uid]
            efficiency = (spec['eta_c'] * spec['eta_d'] if typ == 'storage'
                          else spec['e_gen'] / spec['e_pump'])
            components['replacement_energy_after_losses'] = values['replacement_energy_yuan_per_mwh'] / efficiency
            components['degradation'] = values['degradation_yuan_per_mwh']
        anchor = number(sum(components.values()), f'{uid}.calculated_anchor')
        curves = ({'dis_segments': anchor, 'ch_segments': values['charge_willingness_yuan_per_mwh']}
                  if typ == 'storage' else {'gen_segments': anchor, 'pump_segments': values['charge_willingness_yuan_per_mwh']}
                  if typ == 'pumped' else {'segments': anchor})
        result[uid] = {'type': typ, 'curve_anchors_yuan_per_mwh': curves,
                       'components_yuan_per_mwh': components}
        if typ in ('storage', 'pumped'):
            result[uid]['technical_roundtrip_efficiency'] = efficiency
    return result


def research_cost_worksheet(cfg) -> dict:
    """Load only this scenario's explicit worksheet; never fall back to repo data.

    All numeric inputs are synthetic research assumptions. Fuel and heat rates
    use the same HHV basis. References support formulas, not unit observations.
    """
    from pathlib import Path
    import yaml

    path = Path(cfg.root) / 'config' / 'bid_cost_basis.yaml'
    from .research_cost_setup import merge_cost_worksheet
    sheet = merge_cost_worksheet(cfg.root, yaml.safe_load(path.read_text(encoding='utf-8')) if path.is_file() else None)
    if sheet is None:
        raise ValueError(f'缺少显式成本研究档案: {path}；不会回退到其他场景配置')
    if not isinstance(sheet, dict) or sheet.get('version') != 'synthetic-cost-study-v1':
        raise ValueError('成本研究档案须为synthetic-cost-study-v1对象')
    groups = {
        'thermal': ('fuel_price', 'heat_rate', 'variable_om'),
        'hydro': ('variable_om',),
        'storage': ('replacement_energy', 'degradation', 'reservation'),
        'pumped': ('replacement_energy', 'variable_om', 'reservation'),
    }
    for group, fields in groups.items():
        entries = sheet.get(group)
        if not isinstance(entries, dict):
            raise ValueError(f'成本研究档案{group}须为资源对象')
        for uid, entry in entries.items():
            if not isinstance(entry, dict):
                raise ValueError(f'{group}.{uid}须为对象')
            for key in fields:
                value = number(entry.get(key), f'{group}.{uid}.{key}')
                if value < 0 or (key == 'heat_rate' and value <= 0):
                    raise ValueError(f'{group}.{uid}.{key}须非负，热耗须正数')
            if group == 'hydro':
                water = entry.get('water_opportunity')
                if not isinstance(water, dict) or not {'wet', 'dry'} <= set(water):
                    raise ValueError(f'{uid}.water_opportunity须含wet/dry')
                for season in ('wet', 'dry'):
                    number(water[season], f'{uid}.water_opportunity.{season}')
    re_vom = sheet.get('renewable_vom')
    if not isinstance(re_vom, dict):
        raise ValueError('renewable_vom须为资源对象')
    for uid, value in re_vom.items():
        if number(value, f'{uid}.renewable_vom') < 0:
            raise ValueError(f'{uid}.renewable_vom须非负')
    lengths = {'thermal_offer_markup_factors': 5, 'renewable_strategy_increments': 5,
               'hydro_opportunity_increments': 5, 'storage_sell_increments': 3,
               'storage_buy_decrements': 3, 'pumped_sell_increments': 3}
    for key, length in lengths.items():
        values = sheet.get(key)
        if not isinstance(values, list) or len(values) != length:
            raise ValueError(f'{key}须为长度{length}的列表')
        for value in values:
            if number(value, key) < 0:
                raise ValueError(f'{key}须非负')
    if not isinstance(sheet.get('source'), str) or not sheet['source'].strip():
        raise ValueError('成本研究档案必须说明source')
    sheet['season'] = cfg.base['season']
    return sheet
