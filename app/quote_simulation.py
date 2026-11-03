"""Quote one resource and run the market from one explicit, retained draft."""
from __future__ import annotations

import copy
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


def quotation_workspace(cfg, name, *, disabled=False):
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
    unit_types = {u['id']: u.get('type', family) for family in ('thermal', 'renewable', 'hydro', 'storage', 'pumped', 'tie')
                  for u in cfg.units.get(family, [])}
    uid = st.selectbox('模拟机组', list(draft['units']), key=f'quote_unit:{name}',
        format_func=lambda unit: f'{unit} · {TYPE_CN.get(unit_types.get(unit), "主体")}', disabled=disabled)
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
            st.caption('历史编辑已完整备份，不作为隐藏运行输入。需要找回时下载并解压，上传其中的报价 JSON，核对后仅恢复所选机组。')
            st.download_button('下载保留的旧编辑', quotation_backup(st.session_state, name),
                               file_name=f'{name}_报价备份.zip', mime='application/zip')
            uploaded = st.file_uploader('恢复报价 JSON（仅所选机组）', type=['json'],
                                        key=f'quote_recovery_file:{name}', disabled=disabled)
            if uploaded is not None:
                try:
                    recovered = json.loads(uploaded.getvalue())['units'][uid]
                    if not isinstance(recovered, dict):
                        raise ValueError('该机组报价不是有效对象')
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
                        info['history'].setdefault(f'恢复前当前报价:{stamp}', previous)
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
    def number_input(label, raw, suffix, *, disabled):
        valid = isinstance(raw, Real) and not isinstance(raw, bool) and math.isfinite(raw)
        value = st.number_input(label, value=float(raw) if valid else None,
                                key=f'quote_edit:{key}:{uid}:{suffix}', disabled=disabled)
        if value is None:
            field_errors.append(label)
            st.error(f'{label} 原值为 {raw!r}，请输入有限数值；原草稿未被替换。')
            return raw
        return value

    quote_column, information_column = st.columns([1.2, 1])
    with information_column:
        render_unit_information(cfg, uid, compact=True)
    with quote_column:
        st.subheader(f'{uid} 报价')
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
            st.warning(reasons.get(bid_mode, '当前申报模式无效，不能模拟改价') + '。未自动改变运行模式。')
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
        if typ == 'storage':
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
            thermal = next(u for u in cfg.units['thermal'] if u['id'] == uid)
            previous = ub.get('noload_cost', thermal['noload'])
            value = number_input('空载报价（元/h）', previous, 'noload', disabled=disabled)
            if 'noload_cost' not in ub:
                st.caption(f'旧报价未含空载费，当前按技术档案 {previous} 元/h；仅查看不会改写。')
            if value != previous:
                ub['noload_cost'] = value
        other = {k: v for k, v in ub.items() if k not in {*CURVE_CN, 'startup_cost', 'startup_cost_yuan', 'noload_cost', 'soc_end', 'vup_end'}}
        if other:
            from unit_input_view import field_rows
            st.dataframe(pd.DataFrame(field_rows(other))[["参数", "数值"]], hide_index=True, width='stretch')
        if ub != before:
            st.session_state[key + ':manual'] = sorted(set(st.session_state.get(key + ':manual', [])) | {uid})

        st.markdown('**模拟生成该机组报价**')
        method = st.radio('生成方式', ['按成本生成', '随机策略'], horizontal=True, key=f'quote_method:{key}:{uid}', disabled=quote_disabled)
        use_strategy = st.checkbox('叠加随机策略', key=f'quote_strategy:{key}:{uid}', disabled=quote_disabled) if method == '按成本生成' else True
        seed = (st.number_input('报价随机种子', 0, 2147483647, int(cfg.base['seed']), key=f'quote_seed:{key}:{uid}', disabled=quote_disabled)
                if use_strategy else None)
        st.caption('仅改变所选机组报价，其他机组和设备参数不变。先预览，采用后再运行。')
        protected = st.session_state.get(key + ':manual', [])
        replace_manual = st.checkbox('允许模拟报价替换该机组手工修改', key=f'quote_replace:{key}:{uid}', disabled=quote_disabled) if uid in protected else False
        inputs = {filename: hashlib.sha256((cfg.root / 'config' / filename).read_bytes()).hexdigest()
                  if (cfg.root / 'config' / filename).exists() else None for filename in ('fixed_costs.yaml', 'bid_cost_basis.yaml')}
        request = dict(uid=uid, method=method, seed=seed, costs=inputs, replace_manual=replace_manual)
        if st.button('生成报价预览', disabled=quote_disabled, key=f'quote_generate:{key}:{uid}'):
            st.session_state.pop(key + ':preview', None)
            try:
                from smm.bid_simulation import research_cost_bid_preview, simulate_bids
                generator = research_cost_bid_preview if method == '按成本生成' else simulate_bids
                seed_arg = {'strategy_seed': seed} if method == '按成本生成' else {'seed': seed}
                candidate = generator(cfg, selected_unit_ids=[uid], base_bids=draft,
                    protected_unit_ids=[u for u in protected if u != uid or not replace_manual], **seed_arg)
                st.session_state[key + ':preview'] = dict(candidate=candidate, baseline=copy.deepcopy(draft),
                    request=copy.deepcopy(request), config=copy.deepcopy(dict(base=cfg.base, mode=cfg.mode, units=cfg.units)))
            except (ValueError, TypeError, KeyError, FileNotFoundError) as exc:
                business_error('模拟报价未生成，当前草稿不变', exc, '请核对成本档案或手工修改保护。')
        preview = st.session_state.get(key + ':preview')
        if preview and preview['request']['uid'] == uid:
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
    if draft.get('_simulation') or draft.get('_cost_basis'):
        st.caption('生成记录仅保留来源；后续手工编辑或恢复后的实际报价，以本页表格和本次冻结提交为准。')
    modified = quotation_changed(draft, baseline)
    try:
        persist_scene_working_draft(st.session_state, name, cfg)
    except (OSError, ValueError, RuntimeError, TypeError) as exc:
        business_error('当前报价未自动保存，暂不能运行', exc,
                       '本页编辑仍在，请先下载备份；不要刷新或关闭。原报价版本和已有存档未被覆盖。')
        errors.append('当前报价未保存')
    else:
        st.caption(f'{info["basis_label"]} · {"含当前编辑" if modified else "未修改"}。'
                   '已自动保存；切换场景、刷新或重启后保留。运行直接冻结当前报价，无需另存版本。')
    st.download_button('下载当前报价', json.dumps(draft, ensure_ascii=False, indent=2),
                       file_name=f'{name}_当前报价.json', mime='application/json')
    return draft, errors
