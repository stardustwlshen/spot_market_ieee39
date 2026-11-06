"""Compact, explicit advanced-input forms and effective-input evidence."""
import copy

import pandas as pd
import streamlit as st

from smm.config import load_config
from smm.advanced_parameters import (fields, get_value, prepare_parameter_edit,
    apply_parameter_edit, effective_rows, price_rows, inactive_reason, readonly_rows, _update)
from smm.unit_editor import scene_fingerprint

CHOICES={'wet':'丰水期','dry':'枯水期','value':'价值法','sced_anchor':'锚定日前',
         'fixed_da':'固定日前','window_end_da':'窗口末跟踪日前','sced':'自由SCED',
         'two_hour_eq':'偶数整点等式','hourly_eq':'每小时等式','window_end_pct':'窗口末比例带',
         'hard':'硬约束','soft':'软目标','off':'关闭','reference_continuation':'参考续程',
         'intraday':'优先日内','day_ahead':'日前','inherit':'继承','free':'自由'}


def render_effective_parameters(cfg):
    with st.expander('运行生效参数与来源'):
        # Always read persisted input, never a stale widget/session object.
        current=load_config(root=cfg.root)
        st.dataframe(pd.DataFrame(effective_rows(current)),hide_index=True,width='stretch')
        st.dataframe(pd.DataFrame(price_rows(current)),hide_index=True,width='stretch')
        st.caption('下次启动冻结此配置及当前报价；短时试跑仅截短实时执行。')


def _widget(spec, value, key, disabled):
    kwargs=dict(key=key,disabled=disabled,help=spec.help)
    if spec.kind=='bool':return st.checkbox(spec.label,value=value,**kwargs)
    if spec.kind=='choice':
        options=list(spec.options)
        if value not in options:options=[value,*options]
        return st.selectbox(spec.label,options,index=options.index(value),format_func=lambda x:CHOICES.get(x,x),**kwargs)
    if spec.kind=='multi':return st.multiselect(spec.label,list(spec.options),default=value,**kwargs)
    if spec.kind=='date':return st.text_input(spec.label,value=value,**kwargs)
    if spec.optional:
        active=st.checkbox('设置'+spec.label,value=value is not None,key=key+':set',disabled=disabled,
                           help='不勾选表示移除本层覆盖，继承上层报价限价。')
        number=st.number_input(spec.label,value=float(value) if value is not None else 0.,format='%.15g',**kwargs)
        return number if active else None
    try:
        number = (int(value) if spec.kind=='integer' else float(value)) if value is not None else None
    except (TypeError, ValueError):
        number = None
    return st.number_input(spec.label,value=number,
                           format='%d' if spec.kind=='integer' else '%.15g',**kwargs)


def render_advanced_parameters(cfg,name,*,disabled=False):
    key='advanced_parameters:'+name
    notice=st.session_state.pop(key+':notice',None)
    if notice:st.success(notice)
    for warning in st.session_state.pop(key+':warnings',[]):st.warning(warning)
    with st.expander('高级参数',expanded=key in st.session_state):
        with st.expander('固定结构与遗留配置（只读）'):
            st.dataframe(pd.DataFrame(readonly_rows(cfg)),hide_index=True,width='stretch')
        if key not in st.session_state:
            if st.button('编辑高级参数',key=key+':open',disabled=disabled):
                current=load_config(root=cfg.root)
                specs=fields(current)
                st.session_state[key]=dict(before=scene_fingerprint(cfg.root),
                    values={f.key:get_value(current,f.key,f.default) for f in specs},
                    token=st.session_state.get(key+':token',0))
                st.rerun()
            return
        editor=st.session_state[key]
        specs=fields(cfg)
        display_cfg=copy.deepcopy(cfg)
        # Form controls update their session values on Check/Save. Activation is
        # then recalculated from the submitted selectors without persisting them.
        for spec in specs:
            selector=key+':'+str(editor['token'])+':'+spec.key
            if spec.kind in ('bool','choice') and selector in st.session_state:
                namespace,*parts=spec.key.split('.')
                _update(display_cfg.base if namespace=='base' else display_cfg.mode,
                        parts,st.session_state[selector])
        with st.form(key+':form'):
            values={}
            for group in dict.fromkeys(f.group for f in specs):
                with st.expander(group):
                    for spec in specs:
                        if spec.group!=group:continue
                        old=editor['values'].get(spec.key,get_value(cfg,spec.key,spec.default))
                        reason=inactive_reason(display_cfg,spec.key)
                        values[spec.key]=_widget(spec,old,key+':'+str(editor['token'])+':'+spec.key,disabled or bool(reason))
            st.caption('检查不保存；切换策略后点检查，启用对应参数。保存只作用于下次运行。')
            check=st.form_submit_button('检查高级参数',disabled=disabled)
            save=st.form_submit_button('保存高级参数',type='primary',disabled=disabled)
            cancel=st.form_submit_button('取消高级参数编辑')
        if cancel:
            st.session_state.pop(key,None)
            st.session_state[key+':token']=editor['token']+1
            st.rerun()
        if (check or save) and not disabled:
            try:
                changes={field:copy.deepcopy(value) for field,value in values.items() if value!=editor['values'].get(field)}
                plan=prepare_parameter_edit(cfg.root,changes,expected=editor['before'])
                if save:
                    apply_parameter_edit(plan)
                    st.session_state.pop(key,None)
                    st.session_state[key+':token']=editor['token']+1
                    st.session_state[key+':notice']='高级参数已保存；下次运行按所选策略使用。'
                    st.session_state[key+':warnings']=plan['warnings']
                    st.rerun()
                else:
                    st.success('输入与当前报价检查通过；尚未保存。')
                    st.dataframe(pd.DataFrame(plan['effective']),hide_index=True,width='stretch')
                    st.dataframe(pd.DataFrame(plan['prices']),hide_index=True,width='stretch')
                    with st.expander('线路有效限额预览'):
                        st.dataframe(pd.DataFrame(plan['lines']),hide_index=True,width='stretch')
                    for warning in plan['warnings']:st.warning(warning)
            except (ValueError,KeyError,TypeError,OSError,RuntimeError) as exc:
                st.error('未保存，原场景保持不变：'+str(exc))
