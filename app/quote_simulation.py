"""Quote one resource and run the market from one explicit, retained draft."""
from __future__ import annotations

import copy
from contextlib import nullcontext
import hashlib
import json
import math
from numbers import Real

import pandas as pd
import streamlit as st

from bid_workspace import (get_scene_working_draft, persist_scene_working_draft,
                           quotation_backup, quotation_changed, uninitialized_legacy_backup)
from common import TYPE_CN
from input_workflow import CURVE_CN, business_error, friendly_diagnostic


def _reset_widgets(key, uid):
    for field in list(st.session_state):
        if field.startswith(f'quote_edit:{key}:{uid}:'):
            del st.session_state[field]


def quote_rule_notes(cfg, uid, typ, curve):
    """Explain the same effective per-type rules the validator will enforce."""
    from smm.bids import bid_rules_for_type
    rules = bid_rules_for_type(cfg, typ)
    decreasing = curve in ('ch_segments', 'pump_segments')
    direction = ('严格递减' if rules['strict_monotonic'] else '非递增') if decreasing else (
        '严格递增' if rules['strict_monotonic'] else '非递减')
    notes = [f"本场景报价范围 {rules['price_min']:g}～{rules['price_max']:g} 元/MWh"
             f"（不是结算电价范围）；申报 {rules['segments_min']:g}～{rules['segments_max']:g} 段，"
             f"价格{direction}，功率区间须连续。"]
    if rules['price_step']:
        notes.append(f"价格须为 {rules['price_step']:g} 元/MWh 的整数倍。")
    if rules['max_adjacent_step']:
        exception = '（不含内部最小出力块到首个市场段）' if typ in ('coal', 'gas_ccgt', 'gas_gt') and not rules['thermal_market_above_pmin'] else ''
        notes.append(f"相邻报价段价差上限 {rules['max_adjacent_step']:g} 元/MWh{exception}。")
    if rules['thermal_market_above_pmin']:
        unit = next(u for u in cfg.units['thermal'] if u['id'] == uid)
        width = unit['pmax'] - unit['pmin']
        basis = width if rules['min_segment_basis'] == 'dispatchable' else unit['pmax']
        minimum = min(basis, max(rules['min_segment_fraction'] * basis, rules['min_segment_mw']))
        notes.append(f"第一行 0～{unit['pmin']:g} MW 是内部最小出力块，不计入上述市场段数；"
                     "其报价仍进入优化，不能高于后续市场段。")
        notes.append(f"市场申报从 {unit['pmin']:g} MW 起，每段至少 {minimum:g} MW"
                     + (f"，须覆盖到 {unit['pmax']:g} MW。" if rules['require_capacity_coverage'] else "。"))
    elif rules['require_capacity_coverage']:
        notes.append('申报曲线须完整覆盖装机容量。' + ('新能源实际出力仍受可用预测量限制。' if typ in ('wind', 'pv') else ''))
    if typ in ('storage', 'pumped') and cfg.mode.get('storage_dis_gt_ch', 'error') in ('error', 'warn'):
        requirement = '须' if cfg.mode.get('storage_dis_gt_ch', 'error') == 'error' else '建议'
        notes.append(f'放电/发电最低报价{requirement}高于充电/抽水最高报价；买入报价表示购电意愿。')
    return notes


def cost_archive_recovery(cfg, name, *, disabled=False):
    """One explicit action generates type/capacity defaults without editing originals."""
    from smm.research_cost_setup import (FILENAME, apply_generated_cost_setup,
                                         prepare_generated_cost_setup)
    try:
        plan = prepare_generated_cost_setup(cfg)
        if not plan['changed']:
            st.caption('模拟成本已按机组参数生成；自定义成本仍保留。' if plan['generated_units']
                       else '正在使用已有成本档案；自定义值不会自动替换。')
            return
        st.info('按机组参数生成合成研究成本（非实测），永久另存到当前场景；仅替换原研究默认值，保留自定义成本、报价和原始档案。')
        label = ('使用研究默认成本' if not (cfg.root / 'config/fixed_costs.yaml').exists()
                 else '按机组参数生成模拟成本')
        if st.button(label, disabled=disabled,
                     key=f'cost_import:{name}:{plan["data"]["fingerprint"]}:{plan["snapshot"]}'):
            apply_generated_cost_setup(plan)
            st.rerun()
        with st.expander('查看成本来源'):
            st.caption('支持煤电、联合循环、燃机、风电、光伏、水电、电池、抽蓄。火电按类型及容量温和修正增量热耗和运维，系数为研究假设；容量也用于分段与固定费用缩放，'
                       '不是把单位电量成本乘装机容量。水价值和替代电能仅用于报价，不重复计入运营成本。')
            st.text('生成成本：' + '、'.join(plan['generated_units']) + '\n保留自定义成本：' + ('、'.join(plan['preserved_units']) or '无'))
            st.download_button('下载生成依据', json.dumps(plan['data'], ensure_ascii=False, indent=2),
                               file_name=FILENAME, mime='application/json', key=f'cost_source:{name}')
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        business_error('当前场景不能自动补齐成本', exc, '缺少匹配的研究参数时不会估造；已有档案保持不变。')


def quotation_workspace(cfg, name, *, disabled=False, compact=False):
    """Return exact current draft; persist workspace without rewriting bid archives."""
    from smm.bids import default_bids, resolve_startup_bid, validate_bids
    from unit_input_view import render_unit_information

    try:
        key, draft, baseline, info = get_scene_working_draft(st.session_state, name, cfg)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        business_error('当前报价暂不能读取', exc, '原文件保留，不会用缺省报价替换。请修复存档后继续。')
        backup = uninitialized_legacy_backup(st.session_state, name)
        if backup is not None:
            st.warning('本会话还保留旧编辑，请先下载备份，不要刷新或关闭页面。')
            st.download_button('下载保留的旧编辑', backup, file_name=f'{name}_旧报价备份.zip', mime='application/zip')
        return None, ['无法读取报价']
    if not compact and draft.get('default_bid_model') == 'cost_basis_v2':
        st.caption('报价来源：研究成本（非实测）；切换丰枯水不自动重报价，需重新预览并采用。')
    unit_types = {u['id']: u.get('type', family) for family in ('thermal', 'renewable', 'hydro', 'storage', 'pumped', 'tie')
                  for u in cfg.units.get(family, [])}
    from unit_selection import render_unit_selector
    uid = render_unit_selector(cfg, name, quote=True, disabled=disabled or 'unit_editor:' + name in st.session_state)
    st.caption('当前选择只决定查看和编辑哪台机组；其他参与模拟的机组仍按各自报价参与全网出清。')
    if uid is None or uid not in unit_types:
        if uid is not None:
            st.info(f'{uid} 当前不参与模拟。可返回机组页编辑“参与模拟”并保存；其余启用机组仍参与出清。')
        errors = [issue for issue in validate_bids(cfg, draft, require_complete=True) if issue.level == 'error']
        if errors:
            st.error('报价存在违规，修正后才能运行。请选择相关机组核对。')
        st.caption('当前报价：' + info['basis_label'])
        return draft, errors
    if uid not in draft['units']:
        st.error(f'{uid} 缺少当前报价，请修复报价后继续。')
        return draft, ['当前机组缺少报价']
    if compact:
        st.caption('当前报价：' + info['basis_label'] +
                   (' · 模拟成本报价，非真实市场申报。' if draft.get('default_bid_model') == 'cost_basis_v2' else ''))
    if info.get('latest_error'):
        st.warning('场景的外部最新报价文件暂不能读取；本页已保存的当前报价仍可使用和下载，没有被替换。')
        with st.expander('最新报价文件读取问题'):
            st.text(info['latest_error'])
    if info.get('newer_basis'):
        st.warning(f'场景最新已保存报价与本页基础不同。本页继续使用{info["basis_label"]}及当前编辑，不会自动替换任何机组。')
    if info.get('origin'):
        st.info(info['origin'])
    if info.get('history'):
        with st.expander('保留的旧编辑'):
            st.caption('历史编辑已完整备份，不作为隐藏运行输入。需要找回时下载并解压，再在下方“从文件恢复报价”中上传其中的报价 JSON。')
            st.download_button('下载保留的旧编辑', quotation_backup(st.session_state, name),
                               file_name=f'{name}_报价备份.zip', mime='application/zip')
    with st.expander('恢复与重置') if compact else nullcontext():
        with st.expander('从文件恢复报价（仅所选机组）', expanded=False):
            st.caption('可上传“下载当前报价”保存的 JSON，或旧编辑备份中的报价 JSON。先预览，确认后只替换当前所选机组；其他机组、成本和技术参数不变，恢复前的完整当前报价会另存备份。')
            uploaded = st.file_uploader('恢复报价 JSON（仅所选机组）', type=['json'],
                                       key=f'quote_recovery_file:{name}', disabled=disabled)
            if uploaded is not None:
                try:
                    recovered = json.loads(uploaded.getvalue())['units'][uid]
                    if not isinstance(recovered, dict):
                        raise ValueError('该机组报价不是有效对象')
                    if 'mode' in recovered and not isinstance(recovered['mode'], str):
                        raise ValueError('申报方式须为文本，当前报价未改动')
                    for curve in CURVE_CN:
                        rows = recovered.get(curve)
                        if rows is not None and (not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows)):
                            raise ValueError(f'{CURVE_CN[curve]}须为分段对象列表')
                    if not isinstance(recovered.get('startup_cost', {}), dict):
                        raise ValueError('启动报价须为分状态对象')
                    from unit_input_view import field_rows
                    st.dataframe(pd.DataFrame(field_rows(recovered))[["参数", "数值"]], hide_index=True, width='stretch')
                    if st.button('恢复文件中本机组报价', disabled=disabled, key=f'quote_recover:{key}:{uid}'):
                        previous = copy.deepcopy(draft)
                        stamp = hashlib.sha256(json.dumps(previous, sort_keys=True).encode()).hexdigest()[:12]
                        info.setdefault('history', {}).setdefault(f'恢复前当前报价:{stamp}', previous)
                        draft['units'][uid] = copy.deepcopy(recovered)
                        st.session_state[key + ':manual'] = sorted(set(st.session_state.get(key + ':manual', [])) | {uid})
                        st.session_state[key + ':reset_unit'] = uid
                        st.session_state.pop(key + ':preview', None)
                        st.rerun()
                except (ValueError, TypeError, KeyError) as exc:
                    business_error('恢复文件不能读取，当前报价未改动', exc, '请选择包含当前机组的报价备份 JSON。')
        reset_uid = st.session_state.pop(key + ':reset_unit', None)
        if reset_uid is not None:
            # Reset is requested before rerender so editors don't replay old deltas.
            _reset_widgets(key, reset_uid)
        if st.button('恢复该机组原报价', disabled=disabled, key=f'quote_reset:{key}:{uid}'):
            draft['units'][uid] = copy.deepcopy(baseline['units'][uid])
            st.session_state[key + ':manual'] = [u for u in st.session_state.get(key + ':manual', []) if u != uid]
            st.session_state[key + ':reset_unit'] = uid
            st.session_state.pop(key + ':preview', None)
            st.rerun()

    field_errors = []
    table_changed = False
    def number_input(label, raw, suffix, *, disabled, help=None):
        valid = isinstance(raw, Real) and not isinstance(raw, bool) and math.isfinite(raw)
        value = st.number_input(label, value=float(raw) if valid else None,
                                key=f'quote_edit:{key}:{uid}:{suffix}', disabled=disabled, help=help)
        if value is None:
            field_errors.append(label)
            st.error(f'{label} 原值为 {raw!r}，请输入有限数值；原草稿未被替换。')
            return raw
        return value

    quote_column, information_column = st.columns([2, 1])
    with information_column:
        with st.expander('机组参数与成本（只读）', expanded=False):
            render_unit_information(cfg, uid, compact=True)
        with st.expander('成本来源与研究假设（可选）', expanded=False):
            cost_archive_recovery(cfg, name, disabled=disabled)
    from spot_cost_editor import render_spot_cost_editor
    with st.container():
        render_spot_cost_editor(cfg,uid,disabled=disabled)
    with quote_column:
        st.subheader(f'{uid} 报价')
        st.caption('功率区间与价格可编辑，修改自动保存。')
        ub = draft['units'][uid]
        before = copy.deepcopy(ub)
        typ = unit_types.get(uid)
        bid_mode = ub.get('mode', 'price_quantity')
        price_enabled = bid_mode == 'price_quantity'
        quote_disabled = disabled or not price_enabled
        if not price_enabled:
            reasons = {'planned': '计划/价格接受：发电段价不进入出清',
                       'quantity_only': '报量不报价：充放电或抽发价格不进入出清',
                       'self_schedule': '自调度：执行申报功率曲线，价格不进入出清',
                       'dispatch': '历史调度模式：按报量不报价处理，价格不进入出清'}
            reason = reasons.get(bid_mode) if isinstance(bid_mode, str) else None
            st.warning((reason or '当前申报模式无效，不能模拟改价') + '。未自动改变运行模式。')
            if st.button('切换该机组为报量报价', disabled=disabled, key=f'quote_mode_switch:{key}:{uid}',
                         help='仅改变本机组申报方式；缺少的量价曲线由本机组缺省报价补齐，采用前请核对。'):
                template = default_bids(cfg)['units'][uid]
                ub['mode'] = 'price_quantity'
                for curve in CURVE_CN:
                    if curve in template and not ub.get(curve):
                        ub[curve] = copy.deepcopy(template[curve])
                st.session_state[key + ':reset_unit'] = uid
                st.session_state.pop(key + ':preview', None)
                st.rerun()
        curves = [curve for curve in CURVE_CN if curve in ub]
        for curve in curves:
            frame = pd.DataFrame(ub.get(curve) or [], columns=['p_start', 'p_end', 'price'])
            frame = frame.rename(columns={'p_start': '起点（MW）', 'p_end': '终点（MW）', 'price': '报价（元/MWh）'})
            st.markdown(f'**{CURVE_CN[curve]}**')
            if price_enabled:
                with st.expander('报价规则：' + CURVE_CN[curve]) if compact else nullcontext():
                    st.caption(' '.join(quote_rule_notes(cfg, uid, typ, curve)))
            edited = st.data_editor(frame, hide_index=True, num_rows='dynamic', width='stretch',
                key=f'quote_edit:{key}:{uid}:{curve}', disabled=quote_disabled)
            # Browsing never rewrites missing fields, extra metadata or row types.
            # Explicit edits retain unedited per-segment fields and invalid rows.
            if not edited.equals(frame):
                original = ub.get(curve) or []
                segments = []
                for idx, row in edited.iterrows():
                    segment = copy.deepcopy(original[idx]) if idx in range(len(original)) else {}
                    for field, column in [('p_start', '起点（MW）'), ('p_end', '终点（MW）'), ('price', '报价（元/MWh）')]:
                        value = row[column]
                        try:
                            value = float(value)
                        except (TypeError, ValueError):
                            pass
                        segment[field] = value
                    segments.append(segment)
                ub[curve] = segments
                table_changed = True
        if typ == 'storage':
            unit = next(u for u in cfg.units['storage'] if u['id'] == uid)
            for field, label in (('soc_min', '申报SOC下限（0–1）'), ('soc_max', '申报SOC上限（0–1）')):
                previous_limit = ub.get(field, unit[field])
                limit = number_input(label, previous_limit, field, disabled=disabled,
                    help='当前报价申报范围，须位于技术范围内；只改本次申报，不改技术边界、初始SOC或报价曲线。')
                if limit is not None and limit != previous_limit:
                    ub[field] = limit
            previous = ub.get('soc_end')  # Missing and null both mean unreported for batteries.
            declared = st.checkbox('申报期末 SOC', value=previous is not None,
                                   key=f'quote_edit:{key}:{uid}:soc_on', disabled=disabled)
            if declared:
                value = number_input('期末 SOC（0–1，日前硬约束）', previous if previous is not None else .5,
                                     'soc', disabled=disabled)
                if value != previous:
                    ub['soc_end'] = value
            elif previous is not None:
                ub['soc_end'] = None
        if typ == 'pumped':
            unit = next(u for u in cfg.units['pumped'] if u['id'] == uid)
            # This is exactly the model's legacy fallback. Missing != explicit null.
            previous = ub['vup_end'] if 'vup_end' in ub else unit.get('vup_end', unit.get('vup_end_bid', unit['vup0']))
            declared = st.checkbox('申报日末上库库容', value=previous is not None,
                                   key=f'quote_edit:{key}:{uid}:volume_on', disabled=disabled)
            if 'vup_end' not in ub:
                st.caption(f'旧报价未含日末申报字段，当前按场景缺省值 {previous} 万m³；仅查看不会改写。')
            if declared:
                value = number_input('日末上库库容（万m³，日前硬约束）', previous if previous is not None else unit['vup0'],
                                     'volume', disabled=disabled)
                if value != previous:
                    ub['vup_end'] = value
            elif previous is not None:
                ub['vup_end'] = None
        if typ in ('coal', 'gas_ccgt', 'gas_gt'):
            if cfg.mode.get('startup_cost_mode', 'state_dependent') == 'uniform_per_unit':
                try:
                    scalar = resolve_startup_bid(cfg, ub)['cost_yuan']
                except ValueError:
                    scalar = None
                amount = number_input('单次启动报价（元/次）', scalar, 'startup', disabled=disabled)
                if amount is not None and amount != scalar:
                    ub['startup_cost_yuan'] = amount
            else:
                original_startup = pd.DataFrame([ub.get('startup_cost', {})])
                values = st.data_editor(original_startup, hide_index=True,
                    key=f'quote_edit:{key}:{uid}:startup_states', disabled=disabled,
                    column_config={k: st.column_config.NumberColumn(v) for k, v in
                                   {'cold':'冷态启动（元/次）','warm':'温态启动（元/次）','hot':'热态启动（元/次）','very_hot':'极热态启动（元/次）'}.items()})
                if not values.equals(original_startup):
                    ub['startup_cost'] = values.iloc[0].to_dict()
                    table_changed = True
            thermal = next(u for u in cfg.units['thermal'] if u['id'] == uid)
            previous = ub.get('noload_cost', thermal['noload'])
            value = number_input('空载报价（元/h）', previous, 'noload', disabled=disabled)
            if 'noload_cost' not in ub:
                st.caption(f'旧报价未含空载费，当前按技术档案 {previous} 元/h；仅查看不会改写。')
            if value != previous:
                ub['noload_cost'] = value
        other = {k: v for k, v in ub.items() if k not in {*CURVE_CN, 'startup_cost', 'startup_cost_yuan', 'noload_cost', 'soc_min', 'soc_max', 'soc_end', 'vup_end'}}
        if other:
            from unit_input_view import field_rows
            with st.expander('更多申报信息（只读）', expanded=False):
                st.dataframe(pd.DataFrame(field_rows(other))[["参数", "数值"]], hide_index=True, width='stretch')
        if ub != before:
            st.session_state[key + ':manual'] = sorted(set(st.session_state.get(key + ':manual', [])) | {uid})

        with st.expander('生成模拟报价') if compact else nullcontext():
            protected = st.session_state.get(key + ':manual', [])
            replace_manual = st.checkbox('允许模拟报价替换该机组手工修改', key=f'quote_replace:{key}:{uid}', disabled=quote_disabled) if uid in protected else False
            cost_clicked = st.button('按成本生成预览', disabled=quote_disabled, key=f'quote_generate:{key}:{uid}')
            with st.expander('随机报价（可选）', expanded=False):
                random_basis = st.radio('随机报价依据', ['当前报价', '成本报价'], horizontal=True,
                                        key=f'quote_random_basis:{key}:{uid}', disabled=quote_disabled)
                seed = st.number_input('报价随机种子', 0, 2147483647, int(cfg.base['seed']),
                                       key=f'quote_seed:{key}:{uid}', disabled=quote_disabled,
                                       help='相同机组、输入和种子会生成相同预览；修改种子可换一组。不会修改风光预测种子。')
                st.caption('仅预览所选机组；采用后生效。')
                random_clicked = st.button('生成随机报价预览', disabled=quote_disabled, key=f'quote_random_generate:{key}:{uid}')
            inputs = {filename: hashlib.sha256((cfg.root / 'config' / filename).read_bytes()).hexdigest()
                      if (cfg.root / 'config' / filename).exists() else None for filename in ('fixed_costs.yaml', 'bid_cost_basis.yaml', 'generated_research_costs.json')}
            cost_request = dict(uid=uid, method='按成本生成', seed=None, costs=inputs,
                                replace_manual=replace_manual, entry='cost')
            random_request = dict(uid=uid, method='按成本生成' if random_basis == '成本报价' else '随机策略',
                                  seed=seed, costs=inputs, replace_manual=replace_manual, entry='random')
            if cost_clicked or random_clicked:
                request = cost_request if cost_clicked else random_request
                st.session_state.pop(key + ':preview', None)
                try:
                    from smm.bid_simulation import research_cost_bid_preview, simulate_bids
                    method = request['method']
                    generator = research_cost_bid_preview if method == '按成本生成' else simulate_bids
                    seed_arg = {'strategy_seed': request['seed']} if method == '按成本生成' else {'seed': request['seed']}
                    candidate = generator(cfg, selected_unit_ids=[uid], base_bids=draft,
                        protected_unit_ids=[u for u in protected if u != uid or not replace_manual], **seed_arg)
                    st.session_state[key + ':preview'] = dict(candidate=candidate, baseline=copy.deepcopy(draft),
                        request=copy.deepcopy(request), config=copy.deepcopy(dict(base=cfg.base, mode=cfg.mode, units=cfg.units)))
                except (ValueError, TypeError, KeyError, FileNotFoundError) as exc:
                    business_error('模拟报价未生成，当前草稿不变', exc, '请核对成本档案或手工修改保护。')
            preview = st.session_state.get(key + ':preview')
            if preview and preview['request']['uid'] == uid:
                # Old-session previews lack the new entry marker and require regeneration.
                request = random_request if preview['request'].get('entry') == 'random' else cost_request
                st.markdown('**报价预览：确认后再采用**')
                candidate = preview['candidate']
                rows = [dict(曲线=CURVE_CN[curve], 段=i, **{'起点（MW）': seg['p_start'], '终点（MW）': seg['p_end'], '预览报价（元/MWh）': seg['price']})
                        for curve in CURVE_CN for i, seg in enumerate(candidate['units'][uid].get(curve, []), 1)]
                st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
                stale = (preview['baseline'] != draft or preview['request'] != request or
                         preview['config'] != dict(base=cfg.base, mode=cfg.mode, units=cfg.units))
                if stale:
                    st.warning('报价或参数已变化，请重新生成预览。')
                if st.button('采用预览报价', disabled=quote_disabled or stale, key=f'quote_accept:{key}:{uid}'):
                    st.session_state[key] = copy.deepcopy(candidate)
                    st.session_state[key + ':reset_unit'] = uid
                    st.session_state.pop(key + ':preview', None)
                    st.rerun()

    filled = draft.get('_filled_default') or []
    if filled:
        st.warning('原报价未包含以下主体，初始化时以场景缺省报价补齐：' + '、'.join(filled) + '。当前以本页显示的报价为准，可选择相应机组核对或修改；原报价文件未改写。')
    issues = validate_bids(cfg, draft, require_complete=True)
    errors = [i for i in issues if i.level == 'error'] + field_errors
    if issues:
        st.dataframe(pd.DataFrame([{'机组': i.unit_id, '级别': '违规' if i.level == 'error' else '提示',
                                   '说明': ('初始化时以场景缺省报价补齐；当前以本页显示报价为准' if i.rule == '缺省报价补齐' else friendly_diagnostic(i.msg))} for i in issues]), hide_index=True, width='stretch')
    if errors:
        st.error('报价存在违规，修正后才能运行。当前原值仍自动保留以便修复。')
    modified = quotation_changed(draft, baseline)
    try:
        persist_scene_working_draft(st.session_state, name, cfg, only_if_changed=compact)
    except (OSError, ValueError, RuntimeError, TypeError) as exc:
        business_error('当前报价未自动保存，暂不能运行', exc,
                       '本页编辑仍在，请先下载备份；不要刷新或关闭。原报价版本和已有存档未被覆盖。')
        errors.append('当前报价未保存')
    else:
        if table_changed:
            # data_editor identity includes its input dataframe. Finish the
            # rebase now, before the operator's next edit; otherwise the next
            # edit arrives on the old identity and Streamlit silently drops it.
            st.rerun()
        st.caption('已自动保存；运行使用当前报价。' if st.session_state.get(key + ':etag')
                   else '修改后自动保存；运行使用当前报价。')
        with st.expander('报价保存与来源', expanded=False):
            if draft.get('_simulation') or draft.get('_cost_basis'):
                st.caption('生成记录仅作来源；实际报价以当前表格及本次冻结输入为准。')
            st.caption(f'{info["basis_label"]} · {"含当前编辑" if modified else "未修改"}。本次运行会冻结报价，不随之后编辑变化。')
    with st.expander('报价文件') if compact else nullcontext():
        st.download_button('下载当前报价', json.dumps(draft, ensure_ascii=False, indent=2),
                           file_name=f'{name}_当前报价.json', mime='application/json')
    return draft, errors
