"""Sichuan branch default UI; Guangdong historical source stays unmodified."""
from pathlib import Path

import streamlit as st

from market_rule_profile import SICHUAN_V5
from sichuan_v5_rules import Participant, participation_rule
from sichuan_v5_workflow import archive_names, json_bytes, load, run


def _quarter_label(t):
    return f'{t//4:02d}:{(t%4)*15:02d}–{(t+1)//4:02d}:{((t+1)%4)*15:02d}'


def _render_results(name, results):
    receipt=results['receipt']
    n=receipt['quarters']
    st.subheader('③ 查看运行结果')
    st.caption(f'{name} · 四川 V5.0 征求意见稿省内研究 · {n}/96 个实时段')
    if n < 96:
        st.info('这是首段试算，不代表全天；未计算的时段不填零。')
    st.caption('日前预出清仅是技术参考，没有日前应收应付，也没有日前—实时偏差金额。')
    st.line_chart({'全网日前预出清负荷 MW':[sum(q['demand'].values()) for q in results['da']]})
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
        reg=st.number_input('煤机 C1 已声明调频上调占位（MW）',0.,100.,0.,step=5.)
        reserve=st.number_input('煤机 C1 已声明备用上调占位（MW）',0.,100.,0.,step=5.)
        err=st.slider('合成实时负荷扰动幅度',0.,.08,.025,.005)
        st.caption('仅校核输入占位，不出清辅助服务或计算收入。不存在从广东继承的辅助服务授予与补偿。')
    with st.expander('口径与未覆盖规则'):
        st.markdown('直流有功、线路硬限、无交流/无功/N−1。热机开停最短时长、初期状态、非市场电源计划、水电日电量与日内保留、储能曲线均为明示合成输入。'
                    '实时滚动 2 小时，仅执行首个 15 分钟；研究首段已知该段人工实际均值，并非提前可得生产数据。'
                    '日前预出清的启停结果仅作为研究正式组合替代；未模拟独立日前正式组合、日内正式滚动、梯级水库、市场力替代、'
                    '各类特殊计划、异常时段重算后才可使用的日前代理价及完整中长期、辅助服务、结算分摊。')
    st.subheader('② 运行出清')
    n=st.radio('运行范围', (4,96),format_func=lambda x:'首小时试算（4 段）' if x==4 else '完整单日（96 段）',horizontal=True)
    if st.button('计算日前并运行实时',type='primary',key='scv5_run',disabled=old is not None):
        st.session_state.pop('scv5_recent',None)
        try:
            with st.status('启动四川研究任务…',expanded=True) as status:
                name,_=run(root,month=month,load_factor=load_factor,solar_factor=solar_factor,
                           coal_offer=float(coal_offer),rt_error=err,periods=n,progress=st.write,
                           storage_product=storage_product,regulation_up=reg,reserve_up=reserve)
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
