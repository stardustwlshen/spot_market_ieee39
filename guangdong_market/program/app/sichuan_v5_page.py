"""Sichuan branch default UI; Guangdong historical source stays unmodified."""
from pathlib import Path

import streamlit as st

from market_rule_profile import SICHUAN_V5
from sichuan_v5_intraday import Update
from sichuan_v5_rules import Participant, participation_rule
from sichuan_v5_workflow import archive_names, json_bytes, load, run


def _quarter_label(t):
    return f'{t//4:02d}:{(t%4)*15:02d}–{(t+1)//4:02d}:{((t+1)%4)*15:02d}'


def _segment_label(start,stop):
    return f'{start//4:02d}:{(start%4)*15:02d}–{stop//4:02d}:{(stop%4)*15:02d}'


def _render_results(name, results):
    receipt=results['receipt']
    n=receipt['quarters']
    st.subheader('③ 查看运行结果')
    st.caption(f'{name} · 四川 V5.0 征求意见稿省内研究 · {n}/96 个实时段')
    if n < 96:
        st.info('这是首段试算，不代表全天；未计算的时段不填零。')
    st.caption('日前预出清仅是技术参考，没有日前应收应付，也没有日前—实时偏差金额。')
    st.line_chart({'全网日前预出清负荷 MW':[sum(q['demand'].values()) for q in results['da']]})
    if results.get('intraday'):
        plan=results['intraday']
        start=plan['activation']['effective_quarter']
        st.caption(f'日内物理计划从 {_quarter_label(start)} 起，覆盖剩余 {len(plan["rows"])} 段；仅供实时参考，不定价、不结算。')
        with st.expander('查看日内物理参考计划'):
            st.dataframe([{'时段':_quarter_label(row['quarter']),
                           '全网负荷计划（MW）':sum(row['demand'].values()),
                           '运行煤机数':sum(row['online'].values())}
                          for row in plan['rows']],hide_index=True)
    elif receipt.get('intraday_status')=='not_reached':
        st.caption('本次时段尚未到达日内更新声明的生效时间，没有生成日内计划。')
    if results.get('agc'):
        with st.expander('查看分段调频、备用和占位研究'):
            st.caption('正式组合研究代理 → 调频初步出清 → 一份容量抵扣备用 → 保留上、下方向容量后出清能量。各段价格是不同产品的研究参考，不是收入。')
            st.dataframe([{'时段':_segment_label(a['start'],a['stop']),
                           '调频需求（MW）':a['result']['demand_mw'],
                           '调频抵扣（MW，只记一次）':a['result']['system_regulation_credit_mw'],
                           '调频研究首轮里程价（元/MW）':a['result']['first_price_cny_per_mw'],
                           '抵扣后备用需求（MW）':b['result']['net_demand_mw'],
                           '普通备用研究价（元/MW·h）':b['result']['ordinary_marginal_price_cny_per_mw_h']}
                          for a,b in zip(results['agc'],results['reserve'])],hide_index=True)
            st.dataframe([{'时段':_segment_label(seg['start'],seg['stop']),
                           '机组':resource,'调频上调占位（MW）':a['hold_up_mw'],
                           '调频下调占位（MW）':a['hold_down_mw'],'备用抵扣（MW）':a['reserve_credit_mw']}
                          for seg in results['agc'] for resource,a in seg['result']['awarded'].items()],hide_index=True)
    elif results.get('reserve'):
        reserve_demo=results['reserve']
        with st.expander('查看顺序辅助服务研究'):
            st.caption('合成日前正式组合代理 → 已声明调频及抵扣 → 单区单段备用预出清 → 保留容量后 SCED。非正式成交、补偿或实际调用。')
            st.metric('扣除调频后研究备用需求（MW）',reserve_demo['net_demand_mw'])
            st.dataframe([{'机组':r,'普通研究中标（MW）':a['regular_mw'],
                           '普通研究边际价（元/MW·h）':a['regular_price_cny_per_mw_h']}
                          for r,a in reserve_demo['awards'].items()],hide_index=True)
    t=st.selectbox('选择已执行实时段',list(range(n)),format_func=_quarter_label,
                   key='scv5_period_'+name)
    rt=results['rt'][t]; da=results['da'][t]; price=results['values'][t]
    st.metric('实时统一结算点研究价（元/MWh）',price['uniform_price'] or '未形成')
    st.caption('按符合 V5.0 §4.4 的实时发电出清电量加权，储能只计放电。表中按研究规则显示每段电能量价值，不是正式账单。')
    if price['valuation_status']!='provisional_rt_only':
        st.warning('该时段有可靠性松弛：暂停计算模拟电费。原始模型对偶只用于诊断。')
    with st.expander('查看节点价格、线路潮流及松弛'):
        st.dataframe([{'节点':b,'日前参考模型对偶（不结算）':da['raw_nodal_prices'][b],
                       '实时原始对偶（研究诊断）':rt['raw_nodal_prices'][b],
                       '实时研究节点价（按限价处理）':price['cleared_nodal_price'][b],
                       '负荷削减 MW':rt['shed'][b], '过剩松弛 MW':rt['spill'][b]} for b in rt['demand']],hide_index=True)
        st.dataframe([{'线路':i,'实时潮流 MW':v} for i,v in rt['flows'].items()],hide_index=True)
    with st.expander('查看风光弃电和辅助服务容量占位'):
        st.caption('备用可靠性缺额不代表辅助服务结算。本场景未声明可靠性备用需求时，不显示经济性授予。')
        st.metric('可靠性备用缺额（MW）',rt['reliability_reserve_short_mw'])
        st.dataframe([{'资源':r,'新能源未利用 MW':v} for r,v in rt['curtailed_mw'].items()],hide_index=True)
        st.dataframe([{'机组':r,'调频＋备用上调占位 MW':v[0],'调频下调占位 MW':v[1]}
                       for r,v in rt['ancillary_holds_mw'].items()],hide_index=True)
    if rt.get('hydro'):
        with st.expander('查看梯级水库与水量'):
            st.caption('合成 H1→H2 延迟30分钟，H3独立；展示执行后库容及本段入流/弃水，不是实时实测。')
            st.dataframe([{'水库':r,'执行后库容 m³':v['storage_m3'],
                           '上游到达量 m³':v['upstream_arrival_m3'],'区间来水 m³/s':v['natural_inflow_m3s'],
                           '发电流量 m³/s':v['turbine_m3s'],'弃水 m³/s':v['water_spill_m3s']}
                          for r,v in rt['hydro'].items()],hide_index=True)
    st.dataframe([{'主体':r['resource_id'],'实时上网 MWh':r['grid_delivered_mwh'],
                   '实时用电 MWh':r['grid_consumed_mwh'],'研究上网金额（元）':r['grid_delivered_amount'],
                   '研究用电金额（元，负号是支出）':r['grid_consumed_amount'],
                   '实时电能量研究净额（元）':r['net_amount']} for r in price['rows']],hide_index=True)
    st.download_button('下载已执行时段研究结果 JSON', data=json_bytes({
        'rules':receipt['rules'],'formal_settlement':False,'status':receipt['status'],'quarters':results['values']}),
        file_name=name+'_实时研究结果.json',mime='application/json')


def render():
    st.set_page_config(page_title=SICHUAN_V5.display_name,layout='wide')
    st.title(SICHUAN_V5.display_name)
    st.caption('选择场景 → 设置报价与开关 → 日前预出清 → 15分钟实时滚动 → 查看量价和模拟电费')
    st.info('使用 IEEE39 合成数据，验证四川征求稿规则的研究实现。不是已生效规则、真实四川系统、正式账单或生产调度工具。')
    root=SICHUAN_V5.default_results(Path(__file__).resolve().parents[1])
    with st.expander('找回四川已保存研究任务（只读）'):
        old=st.selectbox('选择四川研究记录',[None,*archive_names(root)],
                         format_func=lambda x:x or '不查看旧记录',key='scv5_archive')
        st.caption('与广东历史档案分目录。浏览旧任务不会启动或恢复求解。')
    st.subheader('① 场景、报价与辅助服务')
    month=st.selectbox('运行月份',range(1,13),index=6,format_func=lambda m:f'{m} 月',key='scv5_month')
    pv=participation_rule(Participant.COMPETITIVE_PV,month)
    st.caption('竞价光伏：'+('报量报价' if pv.bid_mode.value=='quantity_price' else '报量不报价')+'。此处是 V5.0 竞价光伏的现货月份定义。')
    left,middle,right=st.columns(3)
    load_factor=left.slider('负荷倍率',.65,1.25,1.,.01,key='scv5_load')
    solar_factor=middle.slider('光伏可用出力倍率',.5,1.3,1.,.05,key='scv5_solar')
    coal_offer=right.slider('煤机 C1 首段报价（元/MWh）',220,500,320,key='scv5_bid')
    st.caption('报价曲线依 V5.0 分段边界生成；运行物理参数和最小出力成本是合成研究设定。')
    with st.expander('辅助服务占位和实时预测'):
        storage_product=st.radio('独立储能选择',('spot','none'),format_func=lambda a:'仅现货' if a=='spot' else '不参与（零充放电）')

        reserve_demo=st.checkbox('运行顺序备用预出清研究',key='scv5_reserve_demo')
        if reserve_demo:
            reserve_gross=st.number_input('研究总备用需求（MW）',1.,300.,120.,step=5.,key='scv5_reserve_gross')
            agc_demo=st.checkbox('先做分段调频研究出清',key='scv5_agc_demo')
            if agc_demo:
                st.caption('00–12 时40 MW、12–24 时60 MW；使用合成准入及历史 Kh，分别结转至备用。没有实测 AGC 里程和费用。')
            else:
                st.caption('采用全天单段、仅 H1/H2 合成水电报价。C1 已声明的上调容量作为研究抵扣，不出清调频或结算。')
            reserve=0.
        else:
            reserve_gross=0.
            agc_demo=False
            reserve=st.number_input('煤机 C1 已声明备用上调占位（MW）',0.,100.,0.,step=5.)
        reg=0. if agc_demo else st.number_input('煤机 C1 已声明调频上调占位（MW）',0.,100.,0.,step=5.)
        err=st.slider('合成实时负荷扰动幅度',0.,.08,.025,.005)
        st.caption('研究备用结果可进入能量模型容量占位，不计算实际调用或收入；没有从广东继承辅助服务授予。')
    with st.expander('梯级水库研究（可选）'):
        hydro_cascade=st.checkbox('使用合成水库约束和上游传播时滞',key='scv5_cascade')
        st.caption('在日电量上限之外独立启用库容、区间来水、发电用水、弃水和生态流量；无变水头或禁振区。')
    with st.expander('口径与未覆盖规则'):
        st.markdown('直流有功、线路硬限、无交流/无功/N−1。热机开停最短时长、初期状态、非市场电源计划、水电日电量与日内保留、储能曲线均为明示合成输入。'
                    '实时滚动 2 小时，仅执行首个 15 分钟；研究首段已知该段人工实际均值，并非提前可得生产数据。'
                    '日前预出清的启停结果仅作为研究正式组合替代；未模拟日内完整边界类型、生产级日前正式组合、变水头与水电禁振区、市场力替代、'
                    '各类特殊计划、异常时段重算后才可使用的日前代理价及完整中长期、辅助服务、结算分摊。')
    with st.expander('日内边界变化（可选）'):
        id_enabled=st.checkbox('声明日内负荷预测显著变化',key='scv5_id_enabled')
        st.caption('仅在指定时点收到更新，从已执行状态重新优化剩余日内计划；此前负荷预测和平衡不读取该更新。日内不出价、不结算。')
        if id_enabled:
            id_start=st.selectbox('更新开始时段',range(96),index=48,format_func=_quarter_label,key='scv5_id_start')
            id_pct=st.slider('更新后剩余时段负荷变化（%）',-20,20,5,key='scv5_id_pct')
    st.subheader('② 运行出清')
    n=st.radio('运行范围', (4,96),format_func=lambda x:'首小时试算（4 段）' if x==4 else '完整单日（96 段）',horizontal=True)
    if st.button('计算日前并运行实时',type='primary',key='scv5_run',disabled=old is not None):
        st.session_state.pop('scv5_recent',None)
        try:
            with st.status('启动四川研究任务…',expanded=True) as status:
                update=Update(id_start,1+id_pct/100.) if id_enabled else None
                name,_=run(root,month=month,load_factor=load_factor,solar_factor=solar_factor,
                           coal_offer=float(coal_offer),rt_error=err,periods=n,progress=st.write,
                           storage_product=storage_product,regulation_up=reg,reserve_up=reserve,intraday_update=update,
                           hydro_cascade=hydro_cascade,reserve_gross=reserve_gross,clear_agc=agc_demo)
                st.session_state['scv5_recent']=name
                status.update(label='四川研究运行已保存',state='complete')
        except (ValueError,OSError,RuntimeError) as exc:
            st.error('四川研究运行未完成，未使用广东结果：'+str(exc))
    name=old or st.session_state.get('scv5_recent')
    if name:
        try:
            _render_results(name,load(root,name))
        except (OSError,ValueError,TypeError,KeyError) as exc:
            st.error('无法核验四川研究档案：'+str(exc))
