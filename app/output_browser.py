"""Read only this run's indexed artifacts; never fill absent historical outputs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import streamlit as st

from common import display_frame


def indexed_artifacts(directory):
    root = Path(directory)
    base = root if (root/'output_manifest.json').is_file() else root/'da'
    manifest_path = base/'output_manifest.json'
    if not manifest_path.is_file():
        return base, []
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if not isinstance(manifest, dict) or not isinstance(manifest.get('artifacts', []), list):
        raise ValueError('结果文件索引须为对象且 artifacts 须为列表')
    items = []
    for item in manifest.get('artifacts', []):
        if not isinstance(item, dict):
            continue
        name = item.get('file', '')
        # The index is saved evidence, never a permission to read arbitrary paths.
        if not isinstance(name, str) or not name or Path(name).name != name or name in ('.', '..') or '\\' in name:
            continue
        path = base/name
        if path.is_file() and not path.is_symlink():
            items.append(item)
    return base, items


def render_output_browser(directory):
    with st.expander('完整结果文件：筛选、核对与下载', expanded=False):
        try:
            base, items = indexed_artifacts(directory)
        except (OSError, ValueError, TypeError):
            st.warning('结果文件索引无法读取；请使用各结果页已有表格，不以当前输入补造历史数据。')
            return
        if not items:
            st.caption('此历史运行没有文件索引；仍可使用下方已保存的结果表。')
            return
        st.caption('日前是完整计划；实时表只含已执行首段。前瞻诊断窗口可能重叠，不能当作实际电量或利润累计。')
        st.caption('实时首段使用离线合成实际值，是已知当段实际值的理想化研究假设，不代表线上数据已可获得。价格表分别保留正式调度原始价、选用的辅助原始价与限价后结算价；备用影子价格不是单独的备用收入。')
        mapping = {item['file']: item for item in items}
        name = st.selectbox('结果文件', list(mapping), key='output_file:'+str(directory))
        item = mapping[name]
        content = (base/name).read_bytes()
        st.caption(item.get('scope', '此历史索引未记录统计范围，使用前请核对。'))
        if hashlib.sha256(content).hexdigest() != item.get('sha256'):
            st.warning('此文件内容与保存索引不一致，不能视为原始完整存档；请先核对。')
        if name.endswith('.csv'):
            try:
                frame = pd.read_csv(base/name)
            except pd.errors.EmptyDataError:
                st.info('此项没有记录。')
            except (pd.errors.ParserError, UnicodeError, OSError) as exc:
                st.error('此文件无法解析，暂不能预览或筛选；可切换其他文件。原文件下载仍保留，未补值或改写。')
                with st.expander('文件读取详情'):
                    st.code(str(exc))
            else:
                filtered = frame
                for field,label in (('unit','机组'),('station','电站'),('bus','节点'),('line','线路'),('entity','结算主体'),('period','时段')):
                    if field in frame and frame[field].notna().any():
                        options = list(frame[field].dropna().unique())
                        selected = st.multiselect(label, options, key=f'output_filter:{directory}:{name}:{field}')
                        if selected:
                            filtered = filtered[filtered[field].isin(selected)]
                st.caption(f'当前 {len(filtered)} 行 / 文件共 {len(frame)} 行；空白表示未记录或不适用。')
                st.dataframe(display_frame(filtered), hide_index=True, width='stretch')
                st.download_button('下载筛选后表格（原字段）', filtered.to_csv(index=False).encode('utf-8-sig'), file_name='filtered_'+name, key='output_filtered:'+str(directory)+name)
        elif name.endswith('.png'):
            st.image(content)
        st.download_button('下载此存档原文件', content, file_name=name, key='output_raw:'+str(directory)+name)
        st.download_button('下载完整文件索引', (base/'output_manifest.json').read_bytes(), file_name='output_manifest.json', key='output_index:'+str(directory))
