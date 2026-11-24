"""Read-only archive affordances for the native Sichuan research page."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from math import isfinite
from pathlib import Path
import re

import streamlit as st

from market_rule_profile import SICHUAN_V5, require_profile
from sichuan_v5_workflow import archive_names, archive_scenario


def _archive(root, name):
    path=Path(root).resolve()
    if (path.name!=SICHUAN_V5.results_folder or not isinstance(name,str)
            or not re.fullmatch(r'scv5_\d{8}T\d{6}Z_[a-f0-9]{12}',name)):
        raise ValueError("不是四川研究档案")
    path=path/name
    if path.is_symlink() or not path.is_dir():
        raise ValueError("不是四川研究档案")
    return path


def _record(path, key, *, limit=64*1024*1024):
    p=path/key
    if p.is_symlink() or not p.is_file() or p.stat().st_size>limit:
        raise ValueError("缺少可核验的结果："+key)
    with p.open('rb') as handle:
        data=handle.read(limit+1)
    if len(data)>limit:
        raise ValueError("结果超出读取限制："+key)
    value=json.loads(data)
    if not isinstance(value,dict):
        raise ValueError("四川研究记录格式有误")
    require_profile(value.get('rules'),SICHUAN_V5)
    return value, hashlib.sha256(data).hexdigest()


def _file_present(path,key):
    p=path/key
    return p.exists() or p.is_symlink()


def read_failure(root,name):
    """Return a diagnosed failure, not an integrity error; no writes."""
    path=_archive(root,name)
    if _file_present(path,'RECEIPT.json') or not _file_present(path,'FAILURE.json'):
        return None
    failure,_=_record(path,'FAILURE.json')
    inputs,sha=_record(path,'INPUT.json')
    if (failure.get('failed_research') is not True or failure.get('automatic_restart') is not False
            or not isinstance(failure.get('error'),str) or not failure['error'].strip()
            or ('input_sha256' in failure and failure['input_sha256']!=sha)
            or ('scenario_hint' in failure and failure['scenario_hint']!=archive_scenario(inputs))):
        raise ValueError("失败记录或输入校验有误")
    return failure,inputs


def _hint(path,key):
    """Read only the compact header; a bad/stale hint cannot block the picker."""
    try:
        record,_=_record(path,key,limit=16*1024)
        hint=record.get('scenario_hint',{})
        if not isinstance(hint,dict):
            return ''
        m,f,b,n,s=(hint.get(k) for k in ('month','load_factor','coal_offer','requested_periods','storage_product'))
        if (type(m) is not int or not 1<=m<=12 or type(n) is not int or not 1<=n<=96
                or not all(type(x) in (int,float) and isfinite(x) for x in (f,b))
                or not .65<=f<=1.25 or not 220<=b<=500 or not isinstance(s,str) or s not in ('spot','none')):
            return ''
        coverage='全天' if n==96 else '首小时' if n==4 else f'{n}段'
        return f'{coverage}｜{m}月 · C1 {b:g}元/MWh · 负荷×{f:.2f}'+(' · 储能停用' if s=='none' else '')
    except (ValueError,OSError,OverflowError):
        return ''


def archive_label(root,name):
    """Cheap hints only; integrity is checked when opening the result."""
    if name is None:
        return '新任务（不查看旧记录）'
    try:
        path=_archive(root,name)
        if _file_present(path,'RECEIPT.json'):
            status='已保存'
            hint=_hint(path,'RECEIPT.json')
        elif _file_present(path,'FAILURE.json'):
            status='未完成'
            hint=_hint(path,'FAILURE.json')
        else:
            status='待核验'
            hint=''
        date=datetime.strptime(name[5:21],'%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
        beijing=date.astimezone(timezone(timedelta(hours=8)))
        return '｜'.join(s for s in (status,hint,f'{beijing:%m月%d日 %H:%M:%S} 北京时间',name[-6:]) if s)
    except (ValueError,OSError):
        return f'待核验｜{name}'


def _return_to_new():
    st.session_state['scv5_archive']=None


def render_archive_picker(root, *, latest_failure=None):
    """Select a saved run without restoring inputs or running a solver."""
    with st.expander('找回四川已保存研究任务（只读）',expanded=latest_failure is not None):
        names=[None,*archive_names(root)]
        labels={name:archive_label(root,name) for name in names}
        # Streamlit maps serialized labels to values. Keep full identity
        # when unlikely equal timestamp/suffix labels actually collide.
        from collections import Counter
        counts=Counter(labels.values())
        old=st.selectbox('选择四川研究记录',names,
                         format_func=lambda x:labels[x] if counts[labels[x]]==1 else f'{labels[x]}｜{x}',
                         key='scv5_archive')
        st.caption('浏览旧任务不会加载参数到表单，也不会启动或恢复求解。')
        if latest_failure is not None:
            st.caption('最近一次尝试未完成：'+archive_label(root,latest_failure))
        if old is not None:
            st.button('返回新任务',key='scv5_leave_archive',on_click=_return_to_new)
        return old


def render_failed_archive(root,name):
    """Handle a readable failure; integrity errors propagate to caller."""
    result=read_failure(root,name)
    if result is None:
        return False
    failure,inputs=result
    st.subheader('这次研究任务未完成')
    st.warning('失败原因：'+failure['error'])
    labels={'month':'运行月份','load_factor':'负荷倍率','solar_factor':'光伏倍率',
            'coal_offer':'煤机首段报价（元/MWh）','requested_periods':'请求时段数'}
    shown=['{}：{}'.format(label,inputs[key]) for key,label in labels.items()
           if type(inputs.get(key)) in (int,float)]
    if shown:
        st.text('当时设置（只读）\n'+'\n'.join(shown))
    st.caption('没有正式结果，也不会自动重试。可点击上方“返回新任务”，核对参数后手动运行。')
    return True
