"""Display verified persistence fields without inferring process or backup state."""
def period_end(window):
 if window is None:return '未有证据'
 if type(window) is not int or window < 0:return '未有证据（无效时段记录）'
 minutes=(window+1)*15
 day, minutes=divmod(minutes,1440);hour,minute=divmod(minutes,60)
 return ('次日 ' if day==1 else f'第{day+1}日 ' if day else '')+f'{hour:02d}:{minute:02d}'

def progress_rows(status):
 p=status.get('persistence_progress') or {}
 rows=[{'进度':label,'模拟结束时刻':period_end(p.get(key))} for key,label in [('computed','已计算'),('validated','已校验'),('local_checkpoint','已本机检查点'),('external_saved','已外存')]]
 source=p.get('source_checkpoint_restore_verified')
 # A proof about the source is never promoted to current-output restore_verified.
 source_label='已通过；源检查点至 '+period_end(source.get('window')) if isinstance(source,dict) and type(source.get('window')) is int and source['window']>=0 else '未有证据'
 rows.append({'进度':'源检查点恢复验证','模拟结束时刻':source_label})
 return rows

def render(status):
 import streamlit as st
 st.dataframe(progress_rows(status),hide_index=True)
 st.caption('源恢复验证仅指接续起点；外存和恢复验证不等于调度安全通过。普通新运行不自动启用此接续外存流程。')
