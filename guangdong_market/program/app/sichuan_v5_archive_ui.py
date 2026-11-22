"""Read-only archive affordances for the native Sichuan research page."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re

import streamlit as st

from market_rule_profile import SICHUAN_V5, require_profile
from sichuan_v5_workflow import archive_names


def _archive(root, name):
    path=Path(root).resolve()
    if (path.name!=SICHUAN_V5.results_folder or not isinstance(name,str)
            or not re.fullmatch(r'scv5_\d{8}T\d{6}Z_[a-f0-9]{12}',name)):
        raise ValueError("不是四川研究档案")
    path=path/name
    if path.is_symlink() or not path.is_dir():
        raise ValueError("不是四川研究档案")
    return path


def _record(path, key):
    p=path/key
    if p.is_symlink() or not p.is_file() or p.stat().st_size>64*1024*1024:
        raise ValueError("缺少可核验的结果："+key)
    data=p.read_bytes()
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
            or ('input_sha256' in failure and failure['input_sha256']!=sha)):
        raise ValueError("失败记录或输入校验有误")
    return failure,inputs


def archive_label(root,name):
    """Cheap hints only; integrity is checked when opening the result."""
    if name is None:
        return '新任务（不查看旧记录）'
    try:
        path=_archive(root,name)
        if _file_present(path,'RECEIPT.json'):
            status='已保存'
        elif _file_present(path,'FAILURE.json'):
            status='未完成'
        else:
            status='待核验'
        date=datetime.strptime(name[5:21],'%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
        beijing=date.astimezone(timezone(timedelta(hours=8)))
        return f'{status}｜{beijing:%m月%d日 %H:%M:%S} 北京时间｜{name[-6:]}'
    except (ValueError,OSError):
        return f'待核验｜{name}'


def _return_to_new():
    st.session_state['scv5_archive']=None


def render_archive_picker(root, *, latest_failure=None):
    """Select a saved run without restoring inputs or running a solver."""
    with st.expander('找回四川已保存研究任务（只读）',expanded=latest_failure is not None):
        old=st.selectbox('选择四川研究记录',[None,*archive_names(root)],
                         format_func=lambda x:archive_label(root,x),key='scv5_archive')
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
