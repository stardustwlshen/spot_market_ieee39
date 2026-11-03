"""Read-only all-resource operating parameters from one saved execution stage."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from common import display_frame, csv_bytes, column_label


def saved_da_tables(directory):
    files = {'units': 'da_unit_awards.csv', 'prices': 'da_nodal_prices.csv',
             'storage': 'da_storage.csv', 'hydro': 'da_hydro.csv', 'pumped': 'da_pumped.csv'}
    base = directory / 'da' if (directory / 'da').exists() else directory
    tables = {}
    for kind, filename in files.items():
        path = base / filename
        if path.exists():
            try:
                tables[kind] = pd.read_csv(path)
            except (OSError, ValueError, pd.errors.ParserError):
                continue
    return tables


def all_unit_parameters(tables):
    """Keep every recorded field, joining storage/water state by unit and period."""
    def frame(key):
        value = tables.get(key)
        return value.copy() if isinstance(value, pd.DataFrame) else pd.DataFrame(value or [])
    units = frame('units')
    if units.empty:
        return units
    for category in ('storage', 'hydro', 'pumped'):
        extra = frame(category)
        if extra.empty:
            continue
        if 'station' in extra and 'unit' not in extra:
            extra = extra.rename(columns={'station': 'unit'})
        if not {'unit', 'period'} <= set(extra):
            continue
        # Existing common columns retain their formal unit-table values. An
        # identically named resource-specific field remains separately visible.
        extra = extra.rename(columns={c: f'{category}_{c}' for c in extra
                                      if c in units and c not in ('unit', 'period', 'type', 'bus')})
        extra = extra.drop(columns=[c for c in ('type', 'bus') if c in units], errors='ignore')
        units = units.merge(extra, on=['unit', 'period'], how='left', validate='one_to_one')
    prices = frame('prices')
    if not prices.empty and {'period', 'bus'} <= set(units) and {'period', 'bus'} <= set(prices):
        columns = [c for c in prices if c in ('period', 'bus') or c not in units]
        units = units.merge(prices[columns], on=['period', 'bus'], how='left', validate='many_to_one')
    return units.sort_values(['period', 'unit']).reset_index(drop=True)


def render_all_unit_parameters(tables, *, prefix, stage='实时已执行'):
    units = all_unit_parameters(tables)
    st.subheader('所有机组运行参数')
    if units.empty:
        st.info('尚无已保存的机组运行参数。')
        return
    periods = sorted(units.period.unique())
    period = st.selectbox('查看时段', periods, index=len(periods) - 1, key=prefix + ':period',
                          format_func=lambda value: f'第 {int(value) + 1} 时段')
    st.caption(f'{stage} · 此表保留各机组全部已记录参数，空白表示未记录或不适用。')
    labels = {}
    for column in units:
        for resource, label in [('storage', '储能'), ('hydro', '水电'), ('pumped', '抽蓄')]:
            if column.startswith(resource + '_'):
                labels[column] = label + ' · ' + column_label(column[len(resource) + 1:])
                break
    shown = units.rename(columns=labels)
    st.dataframe(display_frame(shown[units.period == period]), hide_index=True, width='stretch')
    st.download_button('下载所有机组逐时参数', csv_bytes(shown), file_name='all_units_parameters.csv',
                       mime='text/csv', key=prefix + ':download')
