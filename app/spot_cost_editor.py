"""Small explicit cost form, separate from the automatically saved quote draft."""
from __future__ import annotations
import copy
import pandas as pd
import streamlit as st
from smm import spot_costs as costs

LABELS={
'fuel_cost_yuan_per_mwh':'燃料发电成本（元/MWh）','online_cost_yuan_per_h':'在线运行费（元/h）',
'startup_cost_yuan_per_start':'启动费（元/次）','shutdown_cost_yuan_per_stop':'停机费（元/次）',
'gen_start_yuan_per_unit_start':'发电启动费（元/台次）','pump_start_yuan_per_unit_start':'抽水启动费（元/台次）',
'gen_stop_yuan_per_unit_stop':'发电停机费（元/台次）','pump_stop_yuan_per_unit_stop':'抽水停机费（元/台次）'}
ONLINE={'no_load_fuel':'空载燃料','online_operation_excluding_om':'在线运行费（不含运维）'}


def _reset_editor(key):
    # Clearing Python state alone leaves the browser's form draft attached to
    # the same widget identities. Rotate every form widget before reloading.
    generation = st.session_state.get(key + ':generation', 0) + 1
    for item in list(st.session_state):
        if item.startswith(key + ':'):
            del st.session_state[item]
    st.session_state[key + ':generation'] = generation
    st.rerun()


def render_spot_cost_editor(cfg,uid,*,disabled=False):
    with st.expander('编辑现货核算成本',expanded=False):
        st.caption('只影响成本与利润；报价和历史结果不变。')
        try:
            profile=costs.load_profile(cfg);typ=costs.specs(cfg)[uid];old=profile['units'].get(uid)
            if old and old['type']!=typ:
                st.warning('原成本类型不匹配，请明确填写当前机组成本。');old=None
            key=f'spot_cost:{cfg.root}:{uid}'
            widget_key=f"{key}:generation:{st.session_state.get(key+':generation',0)}"
            if key+':draft' not in st.session_state:st.session_state[key+':draft']={'token':costs.digest(costs.snapshot(cfg.root)),'old':copy.deepcopy(old)}
            draft=st.session_state[key+':draft'];source_token=draft['token'];old=copy.deepcopy(draft['old'])
            if old is None:st.info('本机组未设置现货核算成本。')
            else:st.caption('生效来源：'+('研究假设（非实测）' if old['source_kind']==costs.SOURCE_KIND else '用户明确成本'))
            plan={'updates':{},'profile':{'units':{}}};missing={}
            if old is None:
                try:plan,missing=costs.prepare_defaults(cfg)
                except (ValueError,KeyError,TypeError,OSError):
                    missing[uid]='研究来源不足，请明确手工填写现货成本。'
            if uid in plan['updates'] and old is None:
                if st.button('采用本机组现货研究成本',key=widget_key+':defaults',disabled=disabled):
                    chosen={uid:plan['profile']['units'][uid]}
                    costs.apply_edit(costs.prepare_edit(cfg,chosen),running=disabled)
                    _reset_editor(key)
            elif old is None and uid in missing:st.caption(missing[uid])
            record=copy.deepcopy(old) if old is not None else dict(type=typ,source='',source_kind='user_defined',**{k:None for k in costs.FIELDS[typ]})
            with st.form(widget_key+':form'):
                for field in costs.FIELDS[typ]:
                    record[field]=st.number_input(LABELS[field],value=float(record[field]) if record.get(field) is not None else None,min_value=0.,format='%.15g',disabled=disabled,
                        key=widget_key+':'+field,help='仅实际执行量计费；不含运维/折旧。事件费用不乘时长，购电另在净结算内。')
                if typ in costs.THERMAL:
                    choices=['未明确',*costs.ONLINE_BASES]
                    record['online_cost_basis']=st.selectbox('在线费定义',choices,index=choices.index(record.get('online_cost_basis','未明确')),
                        format_func=lambda x:ONLINE.get(x,x),disabled=disabled,key=widget_key+':basis')
                elif not costs.FIELDS[typ]:
                    accepted=st.checkbox('本口径无燃料及独立启停费用，不计运维/寿命折损',value=old is not None,disabled=disabled,key=widget_key+':scope')
                    record['cost_scope']='no_fuel_or_defined_start_stop_costs_excluding_om' if accepted else '未明确'
                record['source']=st.text_input('成本来源/假设说明',value=record['source'],disabled=disabled,key=widget_key+':source')
                c1,c2,c3=st.columns(3)
                check=c1.form_submit_button('检查成本',disabled=disabled)
                save=c2.form_submit_button('保存现货成本',disabled=disabled)
                cancel=c3.form_submit_button('取消成本编辑',disabled=disabled)
            if cancel:
                _reset_editor(key)
            if check or save:
                if disabled:raise ValueError('运行中不能保存成本')
                if old is not None and record!=old:
                    record.pop('derivation',None);record['source_kind']='user_defined'
                plan=costs.prepare_edit(cfg,{uid:record})
                if costs.digest(plan['snapshot'])!=source_token:raise ValueError('成本来源已变化，请重新打开表单')
                if save:
                    costs.apply_edit(plan,running=disabled)
                    st.success('现货成本已保存，下次运行或独立重算生效。')
                    _reset_editor(key)
                else:st.success('成本参数有效，尚未保存。')
            with st.expander('不计项目与购电结算'):
                st.caption('运维、折旧、寿命折损、水机会价值及优化罚值不计入。充电/抽水已在带符号净电能结算中计算一次。')
        except (ValueError,KeyError,TypeError,OSError,RuntimeError) as exc:
            st.error(str(exc))
