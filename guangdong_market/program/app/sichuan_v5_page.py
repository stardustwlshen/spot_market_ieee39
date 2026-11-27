"""Sichuan branch default UI; Guangdong historical source stays unmodified."""
from decimal import Decimal, InvalidOperation
import json
from zipfile import BadZipFile
from pathlib import Path

import streamlit as st

from market_rule_profile import SICHUAN_V5
from sichuan_v5_intraday import Update
from sichuan_v5_price_review import BoundRerun, Incident, ReviewRequest, Vector
from sichuan_v5_reconciliation import reconcile_energy
from sichuan_v5_archive_ui import render_archive_picker, render_failed_archive
from sichuan_v5_workflow import evidence_zip, json_bytes, load, review, run


def _quarter_label(t):
    return f'{t//4:02d}:{(t%4)*15:02d}–{(t+1)//4:02d}:{((t+1)%4)*15:02d}'


def _segment_label(start,stop):
    return f'{start//4:02d}:{(start%4)*15:02d}–{stop//4:02d}:{(stop%4)*15:02d}'


_INCIDENTS={
    'missing_boundary':'边界缺失', 'incorrect_boundary':'边界错误',
    'program_fault':'程序故障', 'other_price_anomaly':'其他已记录价格异常',
}
_PRICE_SOURCES={'rt_initial':'原始实时价','rt_recomputed':'同段实时重算',
                'da_proxy_rt':'同段日前参考作为实时代理','withheld':'暂不计价',
                'rt_nodal':'节点价','rt_uniform':'统一结算点价'}


def _unique_json(pairs):
    result={}
    for key,value in pairs:
        if key in result:
            raise ValueError('JSON 中有重复字段')
        result[key]=value
    return result


def _uploaded_rerun(payload, run_id, sha, quarter):
    if len(payload)>128*1024:
        raise ValueError('重算凭证不应超过 128 KB')
    record=json.loads(payload,parse_float=Decimal,object_pairs_hook=_unique_json)
    if not isinstance(record,dict) or set(record)!={'run_id','rt_sha256','quarter','record_id','nodal_yuan_mwh'}:
        raise ValueError('重算凭证字段必须与下载模板一致')
    if (record['run_id']!=run_id or record['rt_sha256']!=sha
            or type(record['quarter']) is not int or record['quarter']!=quarter):
        raise ValueError('重算凭证未绑定到本次运行和本时段')
    raw=record['nodal_yuan_mwh']
    if not isinstance(raw,dict) or any(not k.isascii() or not k.isdigit() or str(int(k))!=k for k in raw):
        raise ValueError('节点编号必须是规范整数键')
    if any(type(v) not in (str,int,Decimal) for v in raw.values()):
        raise ValueError('重算节点价必须为有限十进制数')
    try:
        values={int(k):Decimal(v) for k,v in raw.items()}
    except InvalidOperation as exc:
        raise ValueError('重算节点价必须为有限十进制数') from exc
    if not isinstance(record['record_id'],str) or not record['record_id'].strip() or len(record['record_id'])>200:
        raise ValueError('请填写有效重算记录编号（最多 200 字）')
    return BoundRerun(run_id,sha,Vector('rt_recomputed',quarter,values,record['record_id']))


def _render_price_review(root, name, results, quarter):
    existing=next((r for r in results.get('price_reviews',[]) if r['quarter']==quarter),None)
    with st.expander('复核本时段实时电价（仅研究）'):
        st.caption('仅在执行量已核验时使用。已记录异常应优先采用同段实时重算；只有明确记载无法重算的原因时，才可使用同段日前参考作为实时代理。原始执行、价格与凭证不覆盖。这里只核验研究凭证，不认证外部签发，也不会自动重算。')
        if existing:
            decision=existing.get('decision',existing)
            st.info('已保存复核：'+_PRICE_SOURCES.get(decision.get('source'),str(decision.get('source'))))
            st.json(existing,expanded=False)
            return
        rt=results['rt'][quarter]
        if max((*rt['shed'].values(),*rt['spill'].values(),rt['reliability_reserve_short_mw']))>1.e-6:
            st.warning('本时段存在可靠性松弛，不能使用价格复核。本段原始结果仍可查看。')
            return
        sha=results['receipt']['data_files']['RT_EXECUTED.json']
        template={'run_id':name,'rt_sha256':sha,'quarter':quarter,'record_id':'',
                  'nodal_yuan_mwh':dict.fromkeys(rt['demand'],None)}
        st.download_button('下载重算凭证模板（不是重算结果）',data=json_bytes(template),
                           file_name=f'{name}_q{quarter+1}_重算模板.json',mime='application/json')
        with st.form(f'scv5_review_{name}_{quarter}',clear_on_submit=False):
            cause=st.selectbox('已记录异常类型',_INCIDENTS,format_func=_INCIDENTS.__getitem__)
            incident_id=st.text_input('异常记录编号（必填）',max_chars=200)
            rerun=st.file_uploader('独立重算凭证（可选，按模板完整填写）',type='json')
            blocker=st.text_input('无法重算的具体原因（仅在无法重算并申请同段日前代理时填写）',max_chars=500)
            submitted=st.form_submit_button('保存本时段价格复核')
        if submitted:
            try:
                if not incident_id.strip():
                    raise ValueError('必须有异常记录编号')
                if (rerun is None)==(not blocker.strip()):
                    raise ValueError('请提供重算凭证，或明确填写无法重算的原因；不能同时填两项')
                rr=None if rerun is None else _uploaded_rerun(rerun.getvalue(),name,sha,quarter)
                review(root,name,ReviewRequest(name,sha,quarter,Incident(cause,incident_id.strip(),blocker.strip() or None),rr))
            except (ValueError,RuntimeError,OSError,TypeError) as exc:
                st.error('未保存价格复核：'+str(exc))
            else:
                st.rerun()


def _display_number(value):
    """Human-scale display; structured reports/downloads retain source precision."""
    return '未形成' if value is None else format(Decimal(str(value)), ',.2f')


_STORAGE_RUN_MODE={'spot':'仅参与现货','none':'不参与（零充放电）'}
_PV_RUN_MODE={'quantity_only':'报量不报价','quantity_price':'报量报价'}


def _render_unit_detail(name, resources, rt, da, transactions, *, storage_mode=None):
    by_unit={u['id']:u for u in resources}
    resolved={r['resource_id']:r for r in transactions}
    selected=st.selectbox('查看机组或储能',list(by_unit),key='scv5_unit_'+name,
                         format_func=lambda u:f"{u}（节点 {by_unit[u]['bus']}）")
    resource=by_unit[selected]
    row=resolved.get(selected)
    if row is None:
        st.info(f'{selected} 不属于本算例现货金额测算范围；仍纳入物理平衡。')
        st.dataframe([{'量（上网）':'日前参考上网量（MWh，不结算）','值':_display_number(Decimal(str(da['power'][selected]))/4)},
                      {'量（上网）':'实时上网量（MWh）','值':_display_number(Decimal(str(rt['power'][selected]))/4)}],
                     hide_index=True)
        return
    directions=[('上网','delivered','delivery','effective_rt_nodal_price','rt_nodal')]
    if resource['kind']=='storage':
        if storage_mode == 'none':
            st.info(f'{selected} 在本任务中选择不参与，计划充放电为零；不代表参与现货但未出清。')
        elif storage_mode == 'spot':
            st.caption(f'{selected} 在本任务中仅参与现货；本时段没有活动不代表整日不参与。')
        directions.append(('充电','consumed','consumption','effective_rt_uniform_price','rt_uniform'))
    lines=[]
    for label,energy,basis,price,kind in directions:
        basis_value=row.get(basis+'_price_source')
        active=Decimal(str(row['grid_'+energy+'_mwh']))!=0
        lines.append({'方向':label,'日前参考量（MWh，不结算）':_display_number(row['da_'+energy+'_mwh']),
                      '实时电量（MWh）':_display_number(row['grid_'+energy+'_mwh']),
                      '实际计价口径':_PRICE_SOURCES.get(basis_value,'本方向无电量' if not active else '暂不计价'),
                      '采用价格（元/MWh）':_display_number(row[price]) if basis_value==kind else '—',
                      '研究金额（元）':_display_number(row['grid_'+energy+'_amount'])})
    st.dataframe(lines,hide_index=True)
    if row.get('rt_price_source') not in (None,'rt_initial'):
        st.caption('价格复核来源：'+_PRICE_SOURCES.get(row['rt_price_source'],str(row['rt_price_source']))+'。')


def _result_provenance(name, results):
    receipt=results['receipt']  # From the hash-verified archive loader.
    return {'schema':'ieee39.sichuan_v5.result_provenance.v1',
            'run_id':name, 'rules':receipt['rules'],
            'executed_quarters':receipt['quarters'], 'expected_day_quarters':96,
            'full_day_coverage':receipt['quarters']==96,
            'original_rt_executed_sha256':receipt['data_files']['RT_EXECUTED.json'],
            'price_review_quarters':[r['quarter'] for r in results.get('price_reviews',[])],
            'complete_archive':False}


def _render_reconciliation(name, report, quarter, provenance):
    with st.expander('实时量价对账与导出'):
        st.caption('此处总量覆盖本次所有已执行实时段；下表只列所选时段。现货上网量不含固定非市场注入，用电量含负荷和储能充电，两者不用于核验全网平衡。净额不是全网盈亏或正式账单。日前仅对照电量。')
        summary=report['summary']
        cols=st.columns(3)
        cols[0].metric('现货纳入主体上网量（MWh）',_display_number(summary['grid_delivered_mwh']))
        cols[1].metric('用电合计（MWh，含储能充电）',_display_number(summary['grid_consumed_mwh']))
        net=summary['net_amount']
        cols[2].metric('现货纳入主体净额（元，已执行段累计）',
                       '未完整' if net is None else _display_number(net))
        fields={'resource_id':'主体','role':'角色','bus':'节点','grid_delivered_mwh':'实时上网 MWh',
                'grid_consumed_mwh':'实时用电 MWh','da_delivered_mwh':'日前参考上网 MWh',
                'da_consumed_mwh':'日前参考用电 MWh','raw_rt_nodal_price':'原始节点价',
                'effective_rt_nodal_price':'有效实时节点价','effective_rt_uniform_price':'有效实时统一价',
                'rt_price_source':'实时价格来源','delivery_price_source':'上网计价口径','consumption_price_source':'用电计价口径',
                'grid_delivered_amount':'上网金额（元）','grid_consumed_amount':'用电金额（元）',
                'net_amount':'净额（元）'}
        rows=[{label:(_PRICE_SOURCES.get(r.get(k),r.get(k)) if k.endswith('_source') else
                      r.get(k) if k in ('resource_id','role','bus') else _display_number(r.get(k)))
               for k,label in fields.items()} for r in report['transactions'] if r['quarter']==quarter]
        st.dataframe(rows,hide_index=True)
        st.download_button('下载实时量价对账 JSON',data=json_bytes({**report, 'provenance':provenance}),
                           file_name=name+'_实时量价对账.json',mime='application/json')


def _render_results(root, name, results):
    receipt=results['receipt']
    n=receipt['quarters']
    st.subheader('③ 查看运行结果',anchor='sichuan-results')
    end=f'{n//4:02d}:{n%4*15:02d}'
    st.caption(f'本次覆盖 00:00–{end}（{n}/96 个实时段） · {name}')
    loaded=results['inputs']
    if all(type(loaded.get(k)) in (float,int) for k in ('month','load_factor','solar_factor','coal_offer')):
        st.caption(f'本任务参数：{loaded["month"]} 月 · 负荷 ×{loaded["load_factor"]:.2f} · '
                   f'光伏 ×{loaded["solar_factor"]:.2f} · C1 首段报价 {loaded["coal_offer"]:g} 元/MWh')
    storage_mode=loaded.get('storage_product')
    participation=loaded.get('declared_participation')
    pv_mode=participation.get('PV1') if isinstance(participation,dict) else None
    choices=[]
    if isinstance(storage_mode, str) and storage_mode in _STORAGE_RUN_MODE:
        choices.append('独立储能：'+_STORAGE_RUN_MODE[storage_mode])
    if isinstance(pv_mode, str) and pv_mode in _PV_RUN_MODE:
        choices.append('竞价光伏 PV1：'+_PV_RUN_MODE[pv_mode])
    if choices:
        st.caption('本任务参与方式：'+' · '.join(choices))
    if n < 96:
        st.info('本次未完成全天模拟；未执行的时段不填零。')
    t=st.selectbox('选择已执行实时段',list(range(n)),format_func=_quarter_label,
                   key='scv5_period_'+name)
    rt=results['rt'][t]; da=results['da'][t]
    price=results.get('reviewed_values',results['values'])[t]
    uniform=price['uniform_price']
    report=reconcile_energy(results['inputs']['case'],results['rt'],
                            results.get('reviewed_values',results['values']),
                            da_reference=results['da'],price_reviews=results.get('price_reviews'))
    st.markdown(f'**当前时段：{_quarter_label(t)}**')
    st.metric('实时统一结算点研究价（元/MWh）',_display_number(uniform))
    if uniform is None:
        st.warning('该时段未形成统一研究价。可能因可靠性松弛、缺少符合条件的正权重电量或价格证据不足；不把缺项当作零价。')
    st.caption('日前仅对照电量，不计费用。实时发电一般按节点价，负荷和储能充电用统一结算点研究价；统一价为合格发电量加权，储能仅计放电。下列金额不是正式账单。')
    _render_unit_detail(name,results['inputs']['case']['resources'],rt,da,
                        [r for r in report['transactions'] if r['quarter']==t],storage_mode=storage_mode)
    provenance=_result_provenance(name,results)
    _render_reconciliation(name,report,t,provenance)
    st.caption('两个 JSON 分别提供实时量价对账和实时估值，均覆盖全部已执行段，'
               '不包含完整的出清输入、支路潮流、辅助服务占位或梯级水库记录。')
    st.caption(f'如需保留完整原始复核资料，请备份程序目录下的 '
               f'`{SICHUAN_V5.results_folder}/{name}/`，不要只保存下载的 JSON。')
    st.download_button('下载实时估值 JSON', data=json_bytes({
        'rules':receipt['rules'],'formal_settlement':False,'status':receipt['status'],'quarters':results.get('reviewed_values',results['values']),'original_quarters':results['values'],
        'price_reviews':results.get('price_reviews',[]),'provenance':provenance}),
        file_name=name+'_实时研究结果.json',mime='application/json')
    with st.expander('专家复核：原始档案 ZIP'):
        st.caption('包含本次已保存的原始输入、出清结果和核验凭证；只打包这一次运行。'
                   '不会上传，也不是正式结算或续算文件。发给他人之前请确认可分享。')
        if st.button('准备原始档案 ZIP',key='scv5_evidence_prepare_'+name):
            try:
                bundle=evidence_zip(root,name)
            except (OSError,ValueError,KeyError,TypeError,BadZipFile) as exc:
                st.error(f'原始档案无法打包：{exc}')
            else:
                st.download_button('下载原始档案 ZIP',data=bundle,mime='application/zip',
                                   file_name=name+'_原始复核档案.zip',on_click='ignore')
    with st.expander('查看全天日前负荷参考'):
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
    final_da=results.get('da_post_ancillary')
    rt_hydro=rt.get('hydro',{})
    if final_da is not None or rt.get('ancillary_holds_mw') or rt_hydro:
        with st.expander('追踪本时段的辅助占位与梯级水库'):
            if final_da is not None:
                st.caption('日前预出清用于参考；占位后日前 SCED 是辅助服务容量保留后的能量计划。两者都不计费用，不替代实时执行值。')
            final_row=final_da[t] if final_da is not None else None
            holds=rt.get('ancillary_holds_mw',{})
            da_holds=final_row['ancillary_holds_mw'] if final_row else {}
            units=sorted(set(holds)|set(da_holds))
            if units:
                st.caption('上调占位含调频和备用，下调为调频；不是实际调用量。')
                st.dataframe([{'机组':r, '日前预出清出力（MW）':round(da['power'][r],2),
                               **({'占位后日前 SCED 出力（MW）':round(final_row['power'][r],2)}
                                  if final_row else {}),
                               '实时执行出力（MW）':round(rt['power'][r],2),
                               '实时上调占位（MW）':round(holds.get(r,(0.,0.))[0],2),
                               '实时下调占位（MW）':round(holds.get(r,(0.,0.))[1],2)}
                              for r in units],hide_index=True)
            elif final_row is not None:
                st.caption('本时段没有正的辅助服务容量占位。')
            if rt_hydro:
                st.caption('梯级水库均为合成数据；列出已执行实时段的期末库容和水量。')
                st.dataframe([{'水库':r,'实时期末库容（m³）':round(z['storage_m3'],2),
                               '上游到达水量（m³）':round(z['upstream_arrival_m3'],2),
                               '天然来水流量（m³/s）':round(z['natural_inflow_m3s'],2),
                               '发电用水流量（m³/s）':round(z['turbine_m3s'],2),
                               '弃水流量（m³/s）':round(z['water_spill_m3s'],2)}
                              for r,z in sorted(rt_hydro.items())],hide_index=True)
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
    if da.get('equal_offer_dispatch'):
        with st.expander('查看本时段日前同价出清记录'):
            st.caption('在固定组合与原始经济最优约束下分配；参考节点价仍取原始定价模型。实时不使用这一研究规则。')
            st.json(da['equal_offer_dispatch'],expanded=False)
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
        if rt['ancillary_holds_mw']:
            st.dataframe([{'机组':r,'调频＋备用上调占位 MW':v[0],'调频下调占位 MW':v[1]}
                          for r,v in rt['ancillary_holds_mw'].items()],hide_index=True)
        else:
            st.caption('此时段没有已记录的调频或备用容量占位。')
    if rt.get('hydro'):
        with st.expander('查看梯级水库与水量'):
            st.caption('合成 H1→H2 延迟30分钟，H3独立；展示执行后库容及本段入流/弃水，不是实时实测。')
            st.dataframe([{'水库':r,'执行后库容 m³':v['storage_m3'],
                           '上游到达量 m³':v['upstream_arrival_m3'],'区间来水 m³/s':v['natural_inflow_m3s'],
                           '发电流量 m³/s':v['turbine_m3s'],'弃水 m³/s':v['water_spill_m3s']}
                          for r,v in rt['hydro'].items()],hide_index=True)
    _render_price_review(root,name,results,t)


def render():
    st.set_page_config(page_title=SICHUAN_V5.display_name,layout='wide')
    from sichuan_v5_dadu_view import render_dadu
    dadu, historical = st.tabs(('大渡河 19 站', '原四川示例与历史档案'))
    with dadu:
        render_dadu(SICHUAN_V5.default_results(Path(__file__).resolve().parents[1]))
    with historical:
        _render_original()


def _render_original():
    st.title(SICHUAN_V5.display_name)
    st.caption('选择场景 → 设置报价与开关 → 日前预出清 → 15分钟实时滚动 → 查看量价和模拟电费')
    st.info('使用 IEEE39 合成数据，验证四川征求稿规则的研究实现。不是已生效规则、真实四川系统、正式账单或生产调度工具。')
    root=SICHUAN_V5.default_results(Path(__file__).resolve().parents[1])
    old=render_archive_picker(root,latest_failure=st.session_state.get('scv5_latest_failure'))
    if old is not None:
        st.info('正在查看保存记录。下面的设置不会改变该记录；要计算新场景，先点“返回新任务”。')
    run_panel=st.container()
    with run_panel:
        # Commit draft widgets independently of the archive search form; only the Run button solves.
        st.subheader('① 选场景、设报价')
        month=st.selectbox('运行月份',range(1,13),index=6,format_func=lambda m:f'{m} 月',key='scv5_month')
        st.caption('竞价光伏：6–10 月报量报价，其他月份报量不报价。')
        left,middle,right=st.columns(3)
        load_factor=left.slider('负荷倍率',.65,1.25,1.,.01,key='scv5_load')
        solar_factor=middle.slider('光伏可用出力倍率',.5,1.3,1.,.05,key='scv5_solar')
        coal_offer=right.number_input('煤机 C1 首段报价（元/MWh）',220,500,320,step=1,key='scv5_bid')
        st.caption('可直接输入 C1 首段报价；其他机组使用场景默认报价。')
        st.subheader('② 运行出清')
        n=st.radio('运行范围', (4,96),format_func=lambda x:'首小时试算（4 段）' if x==4 else '完整单日（96 段）',horizontal=True)
        st.caption('首小时也先计算全天日前参考，实时只执行 4 段；用完整单日查看全天结果。')
        clicked=st.button('计算日前并运行实时',type='primary',key='scv5_run',disabled=old is not None)
        feedback=st.empty()
        if old is None and st.session_state.get('scv5_recent'):
            st.markdown('[跳到本次显示的运行结果 ↓](#sichuan-results)')
    results_panel=st.container()
    st.markdown('#### 可选研究设置')
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
    with st.expander('日前同价出清研究'):
        equal_offer_priority=st.checkbox('启用日前同价清洁能源优先与分段分配',value=True,key='scv5_equal_offer')
        st.caption('只用于日前预出清。受限网络不能强制配比；保留原报价和原始定价模型，不改变实时规则或煤机组合平局规则。')
    with st.expander('口径与未覆盖规则'):
        st.markdown('直流有功、线路硬限、无交流/无功/N−1。热机开停最短时长、初期状态、非市场电源计划、水电日电量与日内保留、储能曲线均为明示合成输入。'
                    '实时滚动 2 小时，仅执行首个 15 分钟；研究首段已知该段人工实际均值，并非提前可得生产数据。'
                    '日前预出清的启停结果仅作为研究正式组合替代；未模拟日内完整边界类型、生产级日前正式组合、变水头与水电禁振区、市场力替代、'
                    '各类特殊计划及完整中长期、辅助服务、结算分摊。')
    with st.expander('日内边界变化（可选）'):
        id_enabled=st.checkbox('声明日内负荷预测显著变化',key='scv5_id_enabled')
        st.caption('仅在指定时点收到更新，从已执行状态重新优化剩余日内计划；此前负荷预测和平衡不读取该更新。日内不出价、不结算。')
        if id_enabled:
            id_start=st.selectbox('更新开始时段',range(96),index=48,format_func=_quarter_label,key='scv5_id_start')
            id_pct=st.slider('更新后剩余时段负荷变化（%）',-20,20,5,key='scv5_id_pct')
    if clicked:
        try:
            with run_panel.status('启动四川研究任务…',expanded=True) as status:
                update=Update(id_start,1+id_pct/100.) if id_enabled else None
                name,_=run(root,month=month,load_factor=load_factor,solar_factor=solar_factor,
                           coal_offer=float(coal_offer),rt_error=err,periods=n,progress=st.write,
                           storage_product=storage_product,regulation_up=reg,reserve_up=reserve,intraday_update=update,
                           hydro_cascade=hydro_cascade,reserve_gross=reserve_gross,clear_agc=agc_demo,
                           equal_offer_priority=equal_offer_priority)
                st.session_state['scv5_recent']=name
                st.session_state.pop('scv5_latest_failure',None)
                st.session_state.pop('scv5_run_error',None)
                status.update(label='四川研究运行已保存',state='complete')
        except (ValueError,OSError,RuntimeError) as exc:
            failed=getattr(exc,'archive_name',None)
            if failed:
                st.session_state['scv5_latest_failure']=failed
            st.session_state['scv5_run_error']=str(exc)
        st.rerun()
    if old is None:
        error=st.session_state.get('scv5_run_error')
        recent=st.session_state.get('scv5_recent')
        if error:
            feedback.error('本次未完成：'+error+('。下方仍显示上一次成功结果。' if recent else '。可修改当前参数后重试。'))
        elif recent:
            feedback.success('运行已保存。向下查看结果，或从顶部找回研究记录。')
    name=old or st.session_state.get('scv5_recent')
    if name:
        with results_panel:
            try:
                if not render_failed_archive(root,name):
                    _render_results(root,name,load(root,name))
            except (OSError,ValueError,TypeError,KeyError) as exc:
                st.error('无法核验四川研究档案：'+str(exc))
