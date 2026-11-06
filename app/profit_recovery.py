"""Explicit policy-labelled supplements; no original report or market is changed."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import streamlit as st
from input_workflow import business_error
from smm import spot_costs


def _complete(summary):
    import math
    return summary.get('all_unit_costs_available') is True and all(isinstance(summary.get(k),(int,float)) and math.isfinite(summary[k]) for k in ('cash_operating_cost_yuan','simulated_operating_profit_yuan'))


def historical_profit_recovery(result):
    if not result.get('full'):return None
    from smm.config import ROOT
    from smm import scenario
    from smm.profit_recalculation import prepare_recalculation,apply_recalculation,read_recalculation
    run=Path(result['dir']);report=None
    try:
        saved=sorted((p for p in (run/'cost_recalculations').glob('*/manifest.json') if not p.parent.name.startswith('.')),key=lambda p:p.stat().st_mtime,reverse=True)
        if saved:report=read_recalculation(saved[0].parent,run_dir=run)
    except (OSError,ValueError,KeyError,TypeError,RuntimeError) as exc:
        business_error('历史派生报告未通过核对',exc,'原始结果保持不变，请先核对保存的报告。')
        return None
    with st.expander('按现货口径另算成本与利润',expanded=not _complete(result.get('summary',{}).get('operating_profit',{}))):
        st.caption('采用明确现货成本（研究值非实测），不含运维/折旧/寿命折损；保留原始结果，另外保存报告，不重新求解市场。')
        sources={}
        frozen=run/'scenario_snapshot'
        if (frozen/'config'/spot_costs.FILENAME).is_file():sources['本次冻结现货成本']=frozen
        if (ROOT/'config'/spot_costs.FILENAME).is_file():sources['项目当前现货成本']=ROOT
        for p in sorted(scenario.SCEN_DIR.glob('*/config/'+spot_costs.FILENAME)):
            sources['场景：'+p.parent.parent.name]=p.parent.parent
        if not sources:
            st.info('请先在运行页明确保存现货核算成本。旧混合成本不会自动当作燃料。')
        else:
            choice=st.selectbox('重算成本来源',list(sources),key=f'spot_recalc_source:{run}')
            try:
                plan=prepare_recalculation(run,sources[choice],policy=spot_costs.BASIS)
                st.dataframe(pd.DataFrame([{'机组':uid,'成本来源':x['source'],'成本指纹':x['profile_sha256']} for uid,x in plan['unit_sources'].items()]),hide_index=True,width='stretch')
                st.caption('将采用 '+choice+'；核算范围仅本次实际执行时段。充电/抽水已在净结算内，不重复扣减。')
                if st.button('按现货口径重算本次利润',key=f'spot_recalc:{run}:{plan["fingerprint"]}'):
                    report=read_recalculation(apply_recalculation(plan),run_dir=run)
            except (OSError,ValueError,KeyError,TypeError,RuntimeError) as exc:
                business_error('当前证据不足以安全重算',exc,'请补齐所选成本来源或核对冻结证据，原结果未改变。')
    if report is None:return None
    summary=report['summary'];new=summary.get('profit_basis',spot_costs.LEGACY)==spot_costs.BASIS
    label='现货口径派生报告' if new else '历史口径补算报告'
    st.success(f'{label}：本次实际执行 {summary["executed_periods"]}/{summary["expected_periods"]} 时段；原始电价、出力、结算未改。')
    with st.expander('派生报告来源与下载'):
        st.caption('策略：'+summary.get('profit_basis',spot_costs.LEGACY)+'；按已导出精度核算，库存差单列、不折现金。')
        st.download_button('下载派生利润明细',report['by_unit'].to_csv(index=False).encode('utf-8-sig'),file_name='现货口径利润明细.csv',mime='text/csv',key=f'spot_report_csv:{run}')
        st.download_button('下载派生来源记录',json.dumps(report['manifest'],ensure_ascii=False,indent=2),file_name='现货口径来源记录.json',mime='application/json',key=f'spot_report_manifest:{run}')
    return report
