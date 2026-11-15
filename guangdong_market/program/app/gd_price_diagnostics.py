"""Read-only explanations of a PRICE domain; never changes dispatch or prices."""
import math
import re
from collections import defaultdict


def thermal_reference_rows(context):
    """Current-quarter reference limits from the result's own bound context."""
    specs = (context.get('thermal_reference_extension') or {}).get('thermal_specs', {})
    rows = []
    for name, spec in specs.items():
        match = re.fullmatch(r'P\[([^,]+),0\]', name)
        if not match or spec.get('status') != 'pricing_stable':
            continue
        keys = ('da_sced', 'current_sced', 'da_max', 'delta')
        if not all(isinstance(spec.get(k), (int, float)) and not isinstance(spec[k], bool)
                   and math.isfinite(spec[k]) for k in keys):
            raise ValueError('invalid sourced thermal reference')
        rt = context.get('boundary_mode') == 'same_period_RT_SCED_reference_user_selected' or context.get('reference_selection') == 'user_selected_RT_SCED_not_literal_DA_wording_in_rule_3_4_4_5'
        reference = spec['current_sced'] if rt else spec['da_sced']
        maximum = spec.get('current_max') if rt else spec['da_max']
        if not isinstance(maximum,(int,float)) or not math.isfinite(maximum):
            raise ValueError('invalid current capability')
        cap = min(maximum, (1 + spec['delta']) * reference)
        rows.append({'机组': match[1], '参考来源': '同一时段实时SCED' if rt else '日前SCED（历史结果）', '定价参考（MW）': reference,
                     '调度出力（MW）': spec['current_sced'], '定价允许上限（MW）': cap,
                     '调度超出定价上限（MW）': max(0., spec['current_sced'] - cap)})
    return sorted(rows, key=lambda r: r['调度超出定价上限（MW）'], reverse=True)


def aggregate_balance_bound(model, names, primal, quarter=0):
    """Prove a lower bound on PRICE shortage by summing all nodal equalities.

    Network flows cancel. Other constraints can tighten but never enlarge this
    box bound. Do not interpret it as a physical adequacy assessment.
    """
    if len(model['variables']) != len(names['variables']) or len(primal) != len(model['variables']):
        raise ValueError('PRICE variable dimensions differ')
    if len(model['rows']) != len(names['constraints']):
        raise ValueError('PRICE constraint dimensions differ')
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in primal):
        raise ValueError('nonfinite primal')
    coefficients = defaultdict(float)
    rhs = []
    for name, row in zip(names['constraints'], model['rows']):
        if not name.startswith('balance[') or not name.endswith(f',{quarter}]'):
            continue
        if row['lb'] is None or row['lb'] != row['ub']:
            raise ValueError('balance is not equality')
        rhs.append(row['lb'])
        for j, coefficient in row['terms']:
            coefficients[j] += coefficient
    if not rhs:
        raise ValueError('no nodal balance rows for quarter')
    maxima, values, adjustments = [], [], []
    for j, coefficient in coefficients.items():
        if abs(coefficient) <= 1e-10:
            continue
        name = names['variables'][j]
        if name.startswith('PRICE_'):
            adjustments.append(coefficient * primal[j])
            continue
        if name.startswith(('flow[', 'angle[')):
            raise ValueError('network terms did not cancel; unsupported topology representation')
        bound = model['variables'][j]['ub' if coefficient > 0 else 'lb']
        if bound is None or not math.isfinite(bound):
            raise ValueError('unbounded aggregate injection domain')
        maxima.append(coefficient * bound)
        values.append(coefficient * primal[j])
    requirement, maximum = math.fsum(rhs), math.fsum(maxima)
    return dict(quarter=quarter, net_requirement_mw=requirement,
                domain_max_net_injection_mw=maximum,
                unavoidable_domain_gap_mw=max(0., requirement - maximum),
                priced_net_injection_mw=math.fsum(values),
                balance_adjustment_mw=math.fsum(adjustments),
                balance_residual_mw=math.fsum(values) + math.fsum(adjustments) - requirement,
                physical_load_shedding_inferred=False)
