"""Check saved financial evidence without recalculating or repairing archives."""
from __future__ import annotations

import numpy as np
import pandas as pd

class SettlementIntegrityError(ValueError):
    """Present records contradict each other, rather than merely being unknown."""


MONEY = ('da_amount', 'rt_amount', 'total', 'dev_pnl')
QUANTITIES = ('q_da', 'q_rt', 'dev_mwh', 'dev_abs_mwh')
TABLES = {'settlement_detail': ('逐时段结算', ['entity', 'period']),
          'settlement_by_entity': ('逐主体结算', ['entity']),
          'settlement_by_type': ('按类型结算', ['type'])}


def normalized_settlement_tables(result):
    """Validate present fields. Historical absent fields stay absent/unknown."""
    tables = {}
    for key, (label, identifiers) in TABLES.items():
        source = result.get(key)
        if not isinstance(source, pd.DataFrame) or source.empty:
            continue
        frame = source.copy()
        for field in ('entity', 'type'):
            if field in frame and (frame[field].isna().any() or frame[field].astype(str).str.strip().eq('').any()):
                raise ValueError(f'{label}的{field}标识缺失')
        if set(identifiers) <= set(frame) and frame.duplicated(identifiers).any():
            raise SettlementIntegrityError(f'{label}记录重复，不能重复累计金额')
        numeric = set(MONEY + QUANTITIES) | {'lmp_da', 'lmp_rt', 'dq', 'clamp_da', 'clamp_rt'}
        if key == 'settlement_detail':
            numeric.add('period')
        for field in numeric & set(frame):
            values = pd.to_numeric(frame[field], errors='coerce')
            if not np.isfinite(values.to_numpy(dtype=float)).all():
                raise ValueError(f'{label}字段 {field} 含非数值、缺失或无穷值，不能汇总或视为零')
            frame[field] = values
        if 'period' in frame:
            if not ((frame.period >= 0) & (frame.period % 1 == 0)).all():
                raise ValueError(f'{label}时段编号无效')
            frame['period'] = frame.period.astype(int)
            if 'entity' in frame:
                expected = result.get('rt_prices')
                periods = set(expected.period) if isinstance(expected, pd.DataFrame) and 'period' in expected else set(frame.period)
                if set(frame.period) != periods or frame.groupby('entity').period.nunique().ne(len(periods)).any():
                    raise ValueError(f'{label}主体时段不完整，不能将缺行作为完整结算')
        if {'entity', 'type'} <= set(frame) and frame.groupby('entity').type.nunique().ne(1).any():
            raise SettlementIntegrityError(f'{label}同一主体的类型冲突')
        if {'total', 'da_amount', 'rt_amount'} <= set(frame):
            if ((frame.total - frame.da_amount - frame.rt_amount).abs() > 1.).any():
                raise SettlementIntegrityError(f'{label}合计与日前电费、实时偏差电费之和不一致')
        if key == 'settlement_detail' and {'q_da', 'q_rt', 'lmp_da', 'lmp_rt', 'total'} <= set(frame):
            from smm.time_grid import result_time_grid
            dt = result_time_grid(result)[1]
            expected_da = frame.q_da * frame.lmp_da * dt
            expected_rt = (frame.q_rt - frame.q_da) * frame.lmp_rt * dt
            expected_total = expected_da + expected_rt
            if ((expected_total - frame.total).abs() > 1.).any():
                raise SettlementIntegrityError('逐时段结算与保存量价计算的两部制电费不一致')
            # Total alone cannot detect equal-and-opposite DA/RT corruption.
            for field, expected in (
                    ('da_amount', expected_da), ('rt_amount', expected_rt),
                    ('dev_pnl', (frame.q_rt-frame.q_da) * (frame.lmp_rt-frame.lmp_da) * dt)):
                if field in frame and ((frame[field] - expected).abs() > 1.).any():
                    raise SettlementIntegrityError(f'逐时段结算 {field} 与保存量价计算不一致')
            if 'dq' in frame and ((frame.dq - frame.q_rt + frame.q_da).abs() > .000151).any():
                raise SettlementIntegrityError('逐时段结算 dq 与实时减日前出力不一致')
        tables[key] = frame
    return tables


def cross_table_settlement_check(result):
    """Return known errors and missing checks; never report missing evidence passed."""
    errors, unavailable = [], []
    try:
        tables = normalized_settlement_tables(result)
    except SettlementIntegrityError as exc:
        return [str(exc)], []
    except ValueError as exc:
        return [], [str(exc)]
    detail, entity, by_type = (tables.get(k) for k in TABLES)
    for left, right, group, label in ((detail, entity, 'entity', '逐时段→逐主体结算'),
                                      (entity, by_type, 'type', '逐主体→按类型结算')):
        if left is None or right is None or group not in left or group not in right:
            unavailable.append(label + '缺少表或分组标识')
            continue
        if set(left[group]) != set(right[group]):
            errors.append(label + '主体/类型集合不一致')
            continue
        if group == 'entity' and 'type' in left and 'type' in right:
            kinds = left.groupby(group).type.first()
            right_kinds = right.set_index(group).type.reindex(kinds.index)
            if not kinds.eq(right_kinds).all():
                errors.append(label + '主体类型不一致')
        columns = list(MONEY) + (list(QUANTITIES) if group == 'type' else [])
        for field in columns:
            if field not in left or field not in right:
                if field in MONEY:
                    unavailable.append(label + f'缺少 {field}')
                continue
            expected = left.groupby(group)[field].sum(min_count=1)
            saved = right.set_index(group)[field].reindex(expected.index)
            difference = (expected - saved).abs()
            tolerance = 1.0 if field in MONEY else .01
            if (difference > tolerance).any():
                errors.append(f'{label} {field} 不一致，最大差 {difference.max():.6g}' +
                              (' 元' if field in MONEY else ' MWh'))
        if group == 'entity' and {'q_da', 'q_rt'} <= set(left):
            from smm.time_grid import result_time_grid
            dt = result_time_grid(result)[1]
            quantities = left[['entity', 'q_da', 'q_rt']].copy()
            quantities['dev_mwh'] = quantities.q_rt - quantities.q_da
            quantities['dev_abs_mwh'] = quantities.dev_mwh.abs()
            for field in QUANTITIES:
                if field not in right:
                    continue  # Older archives may not contain aggregate energy.
                expected = quantities.groupby('entity')[field].sum(min_count=1) * dt
                saved = right.set_index('entity')[field].reindex(expected.index)
                difference = (expected - saved).abs()
                if (difference > .01).any():
                    errors.append(f'{label} {field} 不一致，最大差 {difference.max():.6g} MWh')
    return errors, unavailable


def validate_saved_settlement(result):
    """Normalize safe table copies; known corrupt money fails before rendering."""
    for key, (label, _) in TABLES.items():
        if isinstance(result.get(key), pd.DataFrame) and result[key].empty:
            raise ValueError(label + '文件为空，不能核实结算')
    tables = normalized_settlement_tables(result)
    errors, _ = cross_table_settlement_check({**result, **tables})
    if errors:
        raise ValueError('；'.join(errors))
    result.update(tables)


def normalized_profit_table(source, *, per_period=False):
    """Optional profit evidence may fail locally; never coerce damage into profit."""
    rows = source.copy()
    if rows.empty:
        return rows
    keys = ['unit', 'period'] if per_period else ['unit']
    if not set(keys) <= set(rows) or rows[keys].isna().any().any() or rows.duplicated(keys).any():
        raise ValueError('利润主体/时段缺失或重复')
    if 'profit_basis' in rows:
        allowed={'legacy_cash_operating_v1','spot_energy_cash_v2'}
        if rows.profit_basis.isna().any() or not set(rows.profit_basis)<=allowed or rows.profit_basis.nunique()!=1:raise ValueError('混合或未知利润核算口径')
    complete = rows.get('cost_available', pd.Series(False, index=rows.index)).eq(True)
    costs = {'variable_cost_yuan', 'fixed_online_cost_yuan', 'startup_cost_yuan', 'shutdown_cost_yuan',
             'cash_operating_cost_yuan', 'simulated_operating_profit_yuan'}
    for column in [c for c in rows if c.endswith('_yuan')]:
        values = pd.to_numeric(rows[column], errors='coerce')
        required = complete if column in costs else pd.Series(True, index=rows.index)
        if not np.isfinite(values[required].to_numpy(dtype=float)).all():
            raise ValueError(f'利润字段 {column} 含非数值、缺失或无穷值')
        if column in costs:
            values = values.where(complete)
        rows[column] = values
    required = {'cash_revenue_yuan', 'cash_operating_cost_yuan', 'simulated_operating_profit_yuan'}
    if required <= set(rows):
        delta = (rows.cash_revenue_yuan - rows.cash_operating_cost_yuan - rows.simulated_operating_profit_yuan).abs()
        if (delta[complete] > 1.).any():
            raise ValueError('利润与净现金结算减运营成本不一致')
    return rows
