"""One explicit action for missing historical costs; original results stay intact."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import streamlit as st

from input_workflow import business_error


def _complete(summary):
    return summary.get('all_unit_costs_available') is True and all(
        isinstance(summary.get(k), (int, float)) and math.isfinite(summary[k])
        for k in ('cash_operating_cost_yuan', 'simulated_operating_profit_yuan'))


def historical_profit_recovery(result):
    """Return a verified supplemental report, never modify the loaded originals."""
    if not result.get('full') or _complete(result.get('summary', {}).get('operating_profit', {})):
        return None
    from smm.config import ROOT
    from smm.profit_recalculation import (apply_recalculation, prepare_recalculation,
                                         read_recalculation)

    run_dir = Path(result['dir'])
    try:
        saved = sorted((p for p in (run_dir / 'cost_recalculations').glob('*/manifest.json')
                        if not p.parent.name.startswith('.')), key=lambda p: p.stat().st_mtime, reverse=True)
        if saved:
            report = read_recalculation(saved[0].parent, run_dir=run_dir)
            plan = report['manifest']['plan']
        else:
            st.info('本次结果缺少完整成本，所以成本和利润显示为“—”。可直接补算，无需重新求解市场。')
            st.caption('点击补算将保留本次已冻结的有效成本，仅为缺项采用本项目的研究默认成本（非实测）。'
                       '沿用本次实际执行量和结算，另存补算报告，不修改原始结果或场景。')
            plan = prepare_recalculation(run_dir, ROOT)
            if any(source['kind'] != 'original_frozen_cost' and
                   source.get('source_kind') != 'research_assumption_not_observed_plant_data'
                   for source in plan['unit_sources'].values()):
                raise ValueError('来源未明确标注为研究假设，请先核对成本来源。')
            if not st.button('补算本次成本与利润', key=f'profit_supplement:{run_dir}:{plan["fingerprint"]}'):
                return None
            output = apply_recalculation(plan)
            report = read_recalculation(output, run_dir=run_dir)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        business_error('本次历史记录不足以安全补算', exc,
                       '未生成估算值，也未修改原结果。可回到运行页使用研究默认成本后新建一次运行。')
        st.page_link('pages/2_运行.py', label='前往运行页')
        return None

    summary = report['summary']
    executed, expected = summary['executed_periods'], summary['expected_periods']
    st.success(f'成本与利润已补算，覆盖本次实际执行的 {executed}/{expected} 个时段。'
               '下方“补算”指标使用后来补充的成本假设，原始电价、出力和结算不变。')
    with st.expander('补算来源与下载'):
        sources = plan['unit_sources']
        st.dataframe(pd.DataFrame([
            {'机组': uid,
             '成本来源': ('本次冻结档案' if source['kind'] == 'original_frozen_cost' else
                        '后来补充的研究假设' if source.get('source_kind') == 'research_assumption_not_observed_plant_data' else
                        '后来补充的成本参数'),
             '说明': source['source'], '成本指纹': source['profile_sha256']}
            for uid, source in sources.items()]), hide_index=True, width='stretch')
        st.caption('补算按已导出数据的精度计算；金额不含资本、融资、税费，也不重复扣减充电或抽水结算。')
        st.download_button('下载补算利润明细', report['by_unit'].to_csv(index=False).encode('utf-8-sig'),
                           file_name='补算运营利润.csv', mime='text/csv', key=f'profit_supplement_csv:{run_dir}')
        st.download_button('下载补算来源记录', json.dumps(report['manifest'], ensure_ascii=False, indent=2),
                           file_name='补算来源记录.json', mime='application/json', key=f'profit_supplement_manifest:{run_dir}')
    return report
