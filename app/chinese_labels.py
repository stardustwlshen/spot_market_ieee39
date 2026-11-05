"""中文展示层：保留计算字段、数值、原始导出，不推断缺失数据。"""
from __future__ import annotations
import re
import pandas as pd

COLUMNS = {
 'A':'方案甲','B':'方案乙','B−A':'方案乙−方案甲',
 'constraint_kind':'跟踪约束类型','state_end_hour':'北京时间状态时刻（小时）',
 'unit':'主体','entity':'结算主体','type':'资源类型','bus':'节点','line':'线路','station':'电站','period':'时段序号','time':'时刻',
 'stage':'求解阶段','status':'状态','mode':'工况','on':'在线状态或台数','startup':'启动数','startup_count':'发电启动次数','pump_startup_count':'抽水启动次数',
 'P_mw':'净出力 MW','P_ch_mw':'充电功率 MW','P_dis_mw':'放电功率 MW','P_gen_mw':'发电功率 MW','P_pump_mw':'抽水功率 MW',
 'P_ch':'充电功率 MW','P_dis':'放电功率 MW','P_gen':'发电功率 MW','P_pump':'抽水功率 MW','avail_mw':'可用出力 MW','curtail_mw':'弃风弃光功率 MW',
 'energy_mwh':'净电量 MWh','rt_price_valuation_yuan':'按实时价估值（非两级结算）元','served_load_mw':'扣缺额后供给负荷 MW',
 'Q_gen':'发电流量 m³/s','Q_spill':'弃水流量 m³/s','Q_in':'来水流量（含上游）m³/s',
 'E_mwh':'储能电量 MWh','E_start':'时段初电量 MWh','soc':'荷电状态（比例）','u_ch':'充电状态','u_dis':'放电状态',
 'V_up':'时段末上库库容 万m³','V_start':'时段初库容 万m³','V_1e4m3':'时段末库容 万m³','V_up_1e4m3':'上库库容 万m³','V_dn':'下库库容 万m³',
 'n_gen':'发电台数','n_pump':'抽水台数','Q_out':'总下泄流量 m³/s','qout_m3s':'总下泄流量 m³/s','qout_max_m3s':'声明下泄上限 m³/s',
 'qout_max_source':'下泄上限来源','qout_limit_active':'是否声明下泄上限','qout_margin_m3s':'下泄上限余量 m³/s',
 'lmp':'限价后结算电价 元/MWh','lmp_raw':'选定定价原始电价 元/MWh','lmp_x':'中标表结算电价 元/MWh','lmp_y':'节点表结算电价 元/MWh',
 'lmp_da':'日前结算电价 元/MWh','lmp_rt':'实时结算电价 元/MWh','dispatch_raw_lmp':'正式调度原始电价 元/MWh',
 'dispatch_energy':'正式调度能量分量 元/MWh','dispatch_congestion':'正式调度阻塞分量 元/MWh',
 'energy':'能量分量 元/MWh','congestion':'阻塞分量 元/MWh','lmp_decomp':'原始电价分解合计 元/MWh',
 'flow_mw':'正式调度潮流 MW','rate_mw':'保存的线路限额 MW','rate_eff':'有效线路限额 MW','loading':'线路负载率（比例）','mu':'线路影子价格 元/MWh',
 'mu_plus':'正向影子价格 元/MWh','mu_minus':'反向影子价格 元/MWh','slack_mw':'线路松弛 MW','congested':'是否阻塞',
 'pricing_source':'定价来源','raw_price_source':'原始价格来源','settlement_price_source':'结算价格来源','physical_quantity_source':'物理量来源',
 'flow_source':'潮流来源','dual_source':'对偶价格来源','repriced':'是否辅助定价','anchor_relaxed':'是否放宽跟踪目标',
 'r_up':'上备用容量 MW','r_dn':'下备用容量 MW','reserve_eligible':'设计与工况允许备用','reserve_qualified':'记录的备用参与资格',
 'reserve_formulation':'备用筛选口径','reserve_down_enabled':'是否启用下备用','down_enabled':'是否启用下备用','T_dur_h':'备用持续时长 小时',
 'R_up_req':'上备用需求 MW','R_dn_req':'下备用需求 MW','R_up_supply':'上备用分配 MW','R_dn_supply':'下备用分配 MW',
 'R_up_short':'上备用短缺 MW','R_dn_short':'下备用短缺 MW','R_up_re_credit_mw':'新能源上备用折算 MW','R_up_balance_mw':'上备用供需残差 MW',
 'reserve_power_up_margin_mw':'上备用功率余量 MW','reserve_power_down_margin_mw':'下备用功率余量 MW',
 'q_da':'日前结算功率 MW','q_rt':'实时结算功率 MW','dq':'实时偏差功率 MW','da_amount':'日前结算金额 元','rt_amount':'实时偏差结算金额 元',
 'total':'结算合计 元','dev_pnl':'偏差损益 元','clamp_da':'日前限价修正 元','clamp_rt':'实时限价修正 元',
 'merchandising_surplus':'市场结算盈余 元','congestion_rent':'阻塞项 元','clamp_adjustment':'限价修正 元','residual':'核对残差 元',
 'max_power_imbalance_mw':'最大功率不平衡 MW','rent_da':'日前线路阻塞项 元','rent_rt':'实时偏差线路阻塞项 元',
 'curve':'报价曲线','segment':'报价段','p_start':'分段起点 MW','p_end':'分段终点 MW','price':'报价 元/MWh',
 'objective':'求解目标（含罚）元','best_bound':'最优目标下界 元','gap':'混合整数相对间隙（比例）','time_s':'求解用时 秒','termination':'求解终止原因',
 'warm_start':'热启动记录','storage_simultaneous':'同时充放电计数','objective_kind':'目标口径','prices_are_market_prices':'价格是否用于市场结算',
 'quantities_execute':'数量是否用于执行','variable':'整数变量','index':'变量索引','value':'数值','fixed':'是否固定',
 'window_start':'窗口起始时段','window_end_exclusive':'窗口结束边界（不含）','current_P_mw':'窗口前已执行功率 MW','current_V_1e4m3':'窗口初实际库容 万m³',
 'actual_terminal_V_1e4m3':'前瞻计划末库容 万m³','target_V_1e4m3':'日前同钟点目标 万m³',
 'signed_terminal_error_1e4m3':'计划末值减目标 万m³','terminal_error_1e4m3':'末值与目标绝对偏差 万m³','terminal_kind':'终端约束类型',
 'tracking_equation_error_1e4m3':'跟踪松弛等式误差 万m³','water_balance_max_err_1e4m3':'最大水量平衡误差 万m³',
 'ramp_violation_max_mw':'最大爬坡超限 MW','remaining_energy_budget_mwh':'剩余发电量预算 MWh','window_generation_mwh':'窗口计划发电量 MWh',
 'energy_generated_or_discharged_mwh':'实际发电或放电量 MWh','completed_scope':'完成范围','settlement_scope':'结算范围',
 'executed_periods':'已执行时段数','expected_periods':'预期时段数','cost_profile_sha256':'固定成本文件校验指纹','cost_source_kind':'成本数据来源类型',
 'cost_available':'成本参数完整','cost_missing_reason':'成本缺项原因','inventory_unit':'库存单位','inventory_start':'期初库存','inventory_end':'期末库存','inventory_change':'库存变化',
 'report_kind':'核算报告类型',
 'cash_revenue_yuan':'净能量现金结算 元','cash_operating_cost_yuan':'现金运营成本 元','simulated_operating_profit_yuan':'模拟运营利润 元',
 'da_cash_revenue_yuan':'日前现金结算 元','rt_deviation_cash_revenue_yuan':'实时偏差现金结算 元','charging_settlement_yuan':'其中充电或抽水结算 元（已含）',
 'variable_cost_yuan':'可变运营成本 元','fixed_online_cost_yuan':'在线固定成本 元','startup_cost_yuan':'启动成本 元',
 'load_mw':'负荷 MW','shed_mw':'切负荷 MW','surplus_mw':'系统过剩功率 MW','inj_mw':'节点净注入 MW',
 'A':'方案甲','B':'方案乙','B−A':'方案乙减方案甲',
}
for side,word in [('up','向上'),('down','向下'),('lower','下界'),('upper','上界')]:
 for suffix,unit in [('mwh','MWh'),('1e4m3','万m³')]:
  COLUMNS[f'reserve_duration_{side}_margin_{suffix}']=f'持续激活{word}余量 {unit}'
VALUES={
 'hard_equality':'硬等式','soft_target':'带罚软目标',
 'incremental_current_stock':'当前时段初库存增量筛选（旧结果）',
 'period_end_stock':'时段末库存增量筛选',
 'coal':'燃煤','gas_ccgt':'燃气联合循环','gas_gt':'燃气轮机','hydro':'水电','wind':'风电','pv':'光伏','storage':'储能','pumped':'抽水蓄能','fixed_load':'原联络线固定负荷','tie':'区外联络线','load':'负荷','slack':'平衡松弛',
 'storage_dis':'储能放电','storage_ch':'储能充电','pumped_gen':'抽蓄发电','pumped_pump':'抽蓄抽水',
 'standard_sced':'正式调度边际定价','dispatch_sced':'正式调度边际定价','bounded_auxiliary_pricing':'辅助定价（数量不执行）',
 'scuc_preplan':'日前或窗口启停预计划','energy_sced':'正式能量调度','auxiliary_pricing':'辅助定价（数量不执行）','agc_auxiliary':'历史调频辅助阶段',
 'soft_da_window_end':'日前窗口末库容软跟踪','hard_da_window_end':'日前窗口末库容硬目标','declared_day_end':'申报日末硬目标','hard_declared_day_end':'申报日末硬目标',
 'full_day':'完整日','debug_prefix':'调试前缀','executed_prefix':'已执行前缀','prefix_only':'已执行前缀',
 'optimal':'达到求解终止标准','maxTimeLimit':'达到时间上限','maxIterations':'达到迭代上限','infeasible':'不可行','unbounded':'无界',
 'research_assumption_not_observed_plant_data':'模拟研究假设（非实测电厂数据）','1e4m3':'万m³','MWh':'兆瓦时（MWh）',
 'energy_objective_including_penalties':'能量报价目标（含调度罚项）','auxiliary_pricing_not_dispatch_cost':'辅助定价目标（不是调度成本）',
 'segments':'发电报价','dis_segments':'放电报价','ch_segments':'充电报价','gen_segments':'抽蓄发电报价','pump_segments':'抽水报价',
 'price_quantity':'报量报价','fixed_da':'执行日前计划','generating':'发电','pumping':'抽水','idle':'空闲','charging':'充电','discharging':'放电',
 'explicit unit cost profile missing':'未提供独立主体固定成本参数','cost profile resource type mismatch':'成本参数与资源类型不一致',
 'original_frozen_cost':'本次运行冻结成本','retrospective_supplemental_assumption':'后来补充的成本假设',
 'retrospective_supplemental_assumptions':'历史成本补算（原始结果保留）',
 'mixed_explicit_and_synthetic_costs':'包含原有成本与生成研究假设（逐主体列明）',
}
VARIABLES={'u':'在线状态','v':'启动状态','w':'停机状态','u_ch':'充电状态','u_dis':'放电状态','delta':'水电稳定区台数','ngen':'抽蓄发电台数','npump':'抽蓄抽水台数','xg':'抽蓄发电工况','xp':'抽蓄抽水工况','sg':'抽蓄发电启动台数','sp':'抽蓄抽水启动台数','z_s':'历史储能备用资格整数','z_p':'历史抽蓄备用资格整数','y':'热启动状态','ycold':'冷启动状态'}

def column_label(value):
    value=str(value)
    return COLUMNS.get(value,value)

def value_label(value):
    if isinstance(value,bool):return '是' if value else '否'
    if not isinstance(value,str):return value
    if value in VALUES:return VALUES[value]
    for ending in ('_clipped_to_market_limits','_clipped'):
        if value.endswith(ending):return value_label(value[:-len(ending)])+'（经市场限价）'
    return value

def display_frame(frame):
    """Return a presentation-only copy, preserving all numeric amounts and missing cells."""
    frame=frame.copy()
    aggregate_energy = 'period' not in frame and ('q_da' in frame or 'q_rt' in frame)
    for name in frame:
        if frame[name].dtype==object or pd.api.types.is_bool_dtype(frame[name]):
            frame[name]=frame[name].map(value_label)
        if name=='variable':frame[name]=frame[name].map(lambda v:VARIABLES.get(v,v))
    # Name collisions are disambiguated rather than silently dropping a source column.
    used={};labels=[]
    for name in frame:
        label=({'q_da':'日前结算电量 MWh','q_rt':'实时结算电量 MWh'}.get(name,column_label(name)) if aggregate_energy else column_label(name));used[label]=used.get(label,0)+1
        labels.append(label if used[label]==1 else f'{label}（来源{used[label]}）')
    frame.columns=labels
    return frame

def csv_bytes(frame, chinese=True):
    return (display_frame(frame) if chinese else frame).to_csv(index=False).encode('utf-8-sig')

def localize_figure(fig):
    """Translate labels only: do not change traces, axes bindings, numbers, or limits."""
    for key in fig.layout:
        if key.startswith(('xaxis','yaxis')):
            axis=getattr(fig.layout,key)
            if axis.title and axis.title.text:
                axis.title.text=('功率 MW' if axis.title.text=='value' and 'MW' in (fig.layout.title.text or '') else column_label(axis.title.text))
    if fig.layout.legend.title.text:fig.layout.legend.title.text=('数据系列' if fig.layout.legend.title.text=='variable' else column_label(fig.layout.legend.title.text))
    for trace in fig.data:
        if trace.name:trace.name=column_label(value_label(trace.name))
        if trace.hovertemplate:
            text=trace.hovertemplate
            for key in sorted(COLUMNS,key=len,reverse=True):text=text.replace(key+'=',COLUMNS[key]+'=')
            trace.hovertemplate=text
    return fig
