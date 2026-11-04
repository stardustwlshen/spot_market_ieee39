"""Compact, explicit creation of combinable research boundary scenarios."""
from __future__ import annotations

import pandas as pd
import streamlit as st


def render_typical_scenarios(cfg, name, *, bids=None, disabled=False):
    from smm.typical_scenarios import (RENEWABLE_LEVELS, LOAD_LEVELS, WIND_EVENTS, MANIFEST,
                                       create_typical, prepare_typical, fingerprint)
    from input_workflow import business_error
    key = f'typical:{name}'
    with st.expander('典型运行场景：丰枯水、风光与负荷组合', expanded=False):
        st.caption('合成研究情景。负荷仅是外生需求，不参与报价或需求响应；组合只保存为新场景，原场景、上传文件与报价不覆盖。')
        source_model = cfg.base.get('forecast', {}).get('model', 'legacy_v1')
        st.caption(f'当前组合源模型：{source_model}。新仓库默认使用multiscale_v2；旧场景与上传时序不自动替换。旧场景需要新版时，先在下方风光曲线控制中预览并采用，再建立组合。')
        existing = (cfg.root/MANIFEST).exists()
        use_current = st.checkbox('当前输入作为新基准', value=False, key=key+':new_baseline',
                                  disabled=disabled) if existing else True
        if existing and not use_current:
            st.caption('沿用本场景保存的最初基准重新组合，倍率不逐次叠乘；源场景后来改价仍会复制。')
        cols = st.columns(3)
        season = cols[0].selectbox('水文类型', ['保持基准', '丰水期', '枯水期'], key=key+':season', disabled=disabled)
        renewable = cols[1].selectbox('新能源出力水平', list(RENEWABLE_LEVELS),
                                      format_func=lambda x: f'{x} ×{RENEWABLE_LEVELS[x]:g}', key=key+':renewable', disabled=disabled)
        load = cols[2].selectbox('全网区内负荷水平', list(LOAD_LEVELS),
                                 format_func=lambda x: f'{x} ×{LOAD_LEVELS[x]:g}', key=key+':load', disabled=disabled)
        with st.expander('可选：独立风光与时序压力', expanded=False):
            split = st.checkbox('分别设置风电与光伏（补充风大光小、风小光大组合）', key=key+':split_renewables', disabled=disabled)
            wind_multiplier = pv_multiplier = RENEWABLE_LEVELS[renewable]
            if split:
                separate = st.columns(2)
                wind = separate[0].selectbox('风电水平', list(RENEWABLE_LEVELS), key=key+':wind', disabled=disabled)
                pv = separate[1].selectbox('光伏水平', list(RENEWABLE_LEVELS), key=key+':pv', disabled=disabled)
                wind_multiplier, pv_multiplier = RENEWABLE_LEVELS[wind], RENEWABLE_LEVELS[pv]
            event = st.selectbox('附加时序压力', list(WIND_EVENTS), key=key+':wind_event', disabled=disabled)
        st.caption('基础18组合是幅值敏感性矩阵；风光分别设置后可覆盖54组合，另有傍晚持续低风压力。均不代表真实典型日或发生概率；负荷日形状、季节气象关联及独立预测误差尾部仍需额外数据检验。')
        st.caption('先选曲线模型，再建典型场景；新副本保存完整时序，后续切换自动生成模型不会改写这些已保存曲线。')
        st.caption('actual / 日前 DA / 日内 ID / 超短期 RT 四阶段一起调整。风光不改装机并按装机限幅；区外来电固定计划保持不变，只缩放区内基础负荷。' if cfg.base.get('tie_line_mode', 'supply') == 'supply' else '旧转换场景四阶段的基础负荷与独立TIE需求各缩放一次；建议先恢复区外来电，再创建新组合。')
        request = dict(season={'保持基准': None, '丰水期': 'wet', '枯水期': 'dry'}[season],
                       renewable_multiplier=RENEWABLE_LEVELS[renewable], load_multiplier=LOAD_LEVELS[load],
                       use_current_as_baseline=use_current, restore=False, wind_multiplier=wind_multiplier,
                       pv_multiplier=pv_multiplier, wind_event=WIND_EVENTS[event])
        preview_clicked = st.button('预览典型场景', key=key+':preview_button', disabled=disabled or bids is None)
        restore_clicked = st.button('预览恢复最初基准（另建副本）', key=key+':restore_button',
                                     disabled=disabled or bids is None or not existing or use_current) if existing else False
        if preview_clicked or restore_clicked:
            st.session_state.pop(key+':preview', None)
            st.session_state[key+':confirm_availability'] = False
            try:
                actual_request = {**request, 'restore': restore_clicked}
                st.session_state[key+':preview'] = prepare_typical(cfg.root, bids=bids, **actual_request)
                st.session_state[key+':selection'] = request
            except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
                business_error('未生成场景预览，原场景未改变', exc)
        preview = st.session_state.get(key+':preview')
        if preview is None:
            return
        stale = fingerprint(cfg.root) != preview['fingerprint'] or request != st.session_state.get(key+':selection')
        st.markdown('**基准恢复预览**' if preview['request']['restore'] else '**组合预览**')
        st.caption(f"基准：{preview['baseline_name']} · {'丰水期' if preview['season'] == 'wet' else '枯水期'} · "
                   f"风电 ×{preview['wind_multiplier']:g} · 光伏 ×{preview['pv_multiplier']:g} · 全网区内负荷 ×{preview['load_multiplier']:g}。保留全部当前机组价格与分段量。")
        st.dataframe(pd.DataFrame(preview['summary']), hide_index=True, width='stretch')
        assessment = preview['assessment']
        caps = assessment['capacities_mw']
        share = assessment.get('renewable_local_generation_nameplate_share')
        if share is not None:
            st.caption(f"风电 {caps['wind']:g} MW、光伏 {caps['pv']:g} MW；风光占本地发电装机 {share:.2%}（不含储能/区外来电），不是实际供能份额。")
        if assessment['tier'] == 'pressure_required':
            st.warning('该组合至少一套边界未通过乐观供需必要条件，不能作为已验证的常规运行日。增加抽蓄功率不能弥补日末库存不变时的持续电量缺口。')
        elif assessment['tier'] == 'designed_stress':
            st.warning('已选择持续低风压力事件；供需必要条件通过不代表压力测试已通过，仍需全天出清。')
        else:
            st.info('供需必要条件通过，仅为常规候选。仍需全天日前和96段实时出清验证，不能据此声称无切负荷或无线路越限。')
        with st.expander('供需必要条件与场景层级'):
            st.caption(assessment['label'] + '。上界已乐观计入全部可调装机；不把储能充电当作新增电源。')
            st.dataframe(pd.DataFrame(assessment['stages']).rename(columns={
                'stage': '边界阶段', 'demand_peak_mw': '峰荷MW', 'demand_mwh': '需求MWh',
                'renewable_mwh': '风光可用MWh', 'renewable_available_to_demand_ratio': '风光可用电量/负荷（非实发）', 'net_load_peak_mw': '净负荷峰MW',
                'net_load_min_mw': '净负荷谷MW', 'net_ramp_max_abs_mw': '最大相邻净负荷变化MW',
                'optimistic_supply_energy_upper_mwh': '乐观供能上界MWh',
                'unavoidable_energy_shortfall_lower_mwh': '电量缺口下界MWh',
                'instantaneous_shortfall_lower_mw': '功率缺口下界MW',
                'energy_or_up_reserve_shortfall_lower_mw': '电能量或上备用缺口下界MW',
                'energy_or_up_reserve_deficient_periods': '能量或上备用不足点数'}), hide_index=True, width='stretch')
            for caveat in assessment['caveats']:
                st.caption(caveat)
        clipped = sum(row['触及装机上限点数'] for row in preview['clipping'])
        if clipped:
            st.warning(f'风光共有 {clipped} 个“机组×阶段×时点”超过装机，已在预览中限幅；上限截断会使实际倍率低于设定倍率。')
        with st.expander('水文参数与时序核对'):
            st.dataframe(pd.DataFrame(preview['hydro']).rename(columns={'qin': '来水（m³/s）', 'v0': '初始库容（万m³）',
                'vmin': '最小库容（万m³）', 'vmax': '最大库容（万m³）', 'v_end': '日末库容下限（万m³）',
                'qeco': '生态流量（m³/s）', 'e_day': '日电量上限（MWh）'}), hide_index=True, width='stretch')
            st.dataframe(preview['profiles'], hide_index=True, width='stretch')
            st.caption('这些是出清输入，不是求解结果；丰枯水使用该季真实配置的来水、库容与日电量边界，不保证任意组合均可行。')
        changed = preview['availability_changes']
        confirmed = False
        if changed:
            st.warning('以下机组的日前申报可用量会优先进入出清，必须同步才能使场景倍率生效：' + '、'.join(changed))
            confirmed = st.checkbox('同意仅在新场景同步这些新能源申报可用出力；价格与分段量不变',
                                    key=key+':confirm_availability', disabled=disabled or stale)
        if stale:
            st.warning('选项或源场景已变化，请重新预览。')
        destination = st.text_input('新场景名称', value=name[:28]+'_'+('基准恢复' if preview['request']['restore'] else season+'_'+renewable+'_'+load), key=key+':destination')
        if st.button('创建并切换到新场景', key=key+':create', type='primary',
                     disabled=disabled or stale or not destination or bool(changed and not confirmed)):
            try:
                path = create_typical(destination, preview, bids=bids, confirm_availability=confirmed)
            except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
                business_error('未创建新场景，原场景保留', exc)
            else:
                st.session_state['typical_switch_to'] = path.name
                st.session_state['typical_created_notice'] = f'已创建“{path.name}”；原场景及原报价保留。当前报价已复制到新场景，运行前请核对。'
                st.session_state.pop(key+':preview', None)
                st.rerun()
