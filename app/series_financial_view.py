"""Read recorded series accounting policies; never recompute historical profit."""
import math
import pandas as pd
import streamlit as st

LABELS={'spot_energy_cash_v2':('现货运行成本','现货利润（模拟）'),
        'legacy_cash_operating_v1':('历史口径运营成本','历史口径现金运营利润')}
REASONS={'mixed_profit_basis_or_cost_version':'各日成本口径不同，成本与利润不合计',
         'missing_explicit_unit_costs':'部分主体缺少明确成本参数',
         'no_executed_financial_periods':'暂无已执行财务记录'}


def financial_display(record):
    basis=record.get('profit_basis','legacy_cash_operating_v1')
    labels=LABELS.get(basis,('成本（无法统一口径）','利润（无法统一口径）'))
    available=(record.get('aggregate_cost_profit_available',record.get('all_costs_available',False)) is True
        and record.get('cost_basis_consistent',True) is True and basis in LABELS)
    def value(key):
        x=record.get(key)
        return x if isinstance(x,(float,int)) and not isinstance(x,bool) and math.isfinite(x) else None
    reasons=[REASONS.get(r.strip(),r.strip()) for r in str(record.get('cost_aggregation_unavailable_reason','')).split(';') if r.strip()]
    if not available and not reasons:reasons=['成本信息缺失或口径尚不能核实，不按零成本计算']
    return dict(basis=basis,labels=('净电能结算',)+labels,values=(value('cash_revenue_yuan'),
        value('cash_operating_cost_yuan') if available else None,value('simulated_operating_profit_yuan') if available else None),
        reasons=reasons,profiles=record.get('cost_profiles_by_date',[]),available=available)


def render_series_financial(record):
    shown=financial_display(record)
    for col,label,value in zip(st.columns(3),shown['labels'],shown['values']):
        col.metric(label,'未提供 / 不汇总' if value is None else f'{value:,.2f}元')
    if shown['reasons']:st.warning('；'.join(shown['reasons'])+'。')
    if shown['basis']=='spot_energy_cash_v2':
        st.caption('仅计现货运行相关成本，不计运维。抽水和充电购电已含在净电能结算中，不重复扣除。')
    elif shown['basis']=='legacy_cash_operating_v1':
        st.caption('历史口径原样展示，未自动改成现货新口径。')
    st.caption('只累计已完成实际执行日；日内参考不结算。优化罚项不算机组实际费用。')
    if shown['profiles']:
        with st.expander('逐日成本口径与冻结来源'):
            st.dataframe(pd.DataFrame(shown['profiles']).rename(columns={'date':'日期','profit_basis':'利润口径',
                'cost_basis_version':'成本版本','cost_profile_sha256':'冻结成本指纹','legacy_basis_inferred':'旧档口径识别'}),hide_index=True,width='stretch')
    return shown
