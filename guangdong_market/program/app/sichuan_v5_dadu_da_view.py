"""Single-day Dadu filing, published dispatch, and operator verification."""
import csv
from datetime import datetime, timedelta
from io import BytesIO, StringIO
import json
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd
import streamlit as st

from sichuan_v5_ancillary import Award
from sichuan_v5_dadu_da import list_days, read_day, run_day
from sichuan_v5_dadu_live import solve_visible
from sichuan_v5_dadu_run import declaration_path, declaration_record, freeze_declaration
from sichuan_v5_dadu_view import KINDS, SCENARIOS, _chart, _load_sheet
from sichuan_v5_declaration_sheet import render_sheet


def stamp(q):
    return f'{q//4:02}:{q%4*15:02}'


def period(q):
    return stamp(q)+'–'+stamp(q+1)


def archive_label(name):
    scenario, date, digest = name.rsplit('_',2)
    local = datetime.strptime(date,'%Y%m%dT%H%M%S%fZ') + timedelta(hours=8)
    return f'{SCENARIOS[scenario]} · {local:%m-%d %H:%M:%S} · {digest[:6]}'


def price(row,bus):
    values = row['raw_nodal_prices']
    return values.get(str(bus), values.get(bus))


def read_awards(upload):
    if upload is None:
        return []
    reader = csv.DictReader(StringIO(upload.getvalue().decode('utf-8-sig')))
    fields = ['时段序号','机组编号','调频向上MW','调频向下MW','备用向上MW']
    if reader.fieldnames != fields:
        raise ValueError('请使用辅助服务模板的列名及顺序')
    records = []
    for number, row in enumerate(reader, start=2):
        try:
            if None in row or any(not row[k] or not row[k].strip() for k in fields):
                raise ValueError('字段缺失或列数不符')
            records.append(Award(int(row[fields[0]])-1, row[fields[1]].strip(),
                                 *(float(row[k]) for k in fields[2:])))
        except ValueError as exc:
            raise ValueError(f'占位文件第 {number} 行无效：{exc}') from exc
    return records


def tables(result, q):
    row = result['da'][q]
    inp = result['inputs']
    stations = []
    for s in inp['stations']:
        uid = s['id']
        h = row['hydro'][uid]
        low, high = result['bounds'][q][uid]
        stations.append({'时段':period(q),'电站':s['name'],'节点':s['bus'],'核准下限 MW':low,
            '核准上限 MW':high,'中标 MW':row['power'][uid],'节点参考价 元/MWh':price(row,s['bus']),
            '区间来水 m³/s':h['natural_inflow_m3s'],'上游到水 m³/s':h['upstream_arrival_m3']/900,
            '过机 m³/s':h['turbine_m3s'],'旁泄 m³/s':h['eco_bypass_m3s'],'弃水 m³/s':h['water_spill_m3s'],
            '总下泄 m³/s':h['release_m3s'],'段末库容 亿m³':h['storage_m3']/1e8})
    lines = [{'时段':period(q),'线路':l['id'],'起点':l['source'],'终点':l['target'],
              '潮流 MW':row['flows'][l['id']],'限额 MW':l['limit_mw'],
              '负载率 %':100*abs(row['flows'][l['id']])/l['limit_mw']} for l in inp['lines']]
    return stations,lines


@st.cache_data(show_spinner=False,max_entries=4)
def export_day(root,name):
    result = read_day(root,name)
    buf = BytesIO()
    with ZipFile(buf,'w',ZIP_DEFLATED) as z:
        def write(label,rows):
            z.writestr(label,pd.DataFrame(rows).to_csv(index=False).encode('utf-8-sig'))
        stations,lines,units,prices = [],[],[],[]
        for q, r in enumerate(result['da']):
            water,flows = tables(result,q)
            stations.extend(water)
            lines.extend(flows)
            for a in result['inputs']['assets']:
                uid = a['id']
                units.append({'时段':period(q),'资源':a['name'],'编号':uid,'类型':KINDS[a['kind']],
                        '设计装机 MW':a['capacity_mw'],'可用 MW':result['inputs']['available'][q][uid],
                        'SCUC出力 MW':result['scuc'][q]['power'][uid], '煤机开机':r['online'].get(uid),
                        'SCED中标或计划 MW':r['power'][uid],'电量 MWh':.25*r['power'][uid],
                        '节点参考价 元/MWh':price(r,a['bus'])})
            prices.append({'时段':period(q),**{f'节点{b}':v for b,v in r['raw_nodal_prices'].items()}})
        write('各机组日前计划.csv',units)
        write('各站申报边界与水账.csv',stations)
        write('线路潮流.csv',lines)
        write('39节点参考价_元每MWh.csv',prices)
        z.writestr('完整日前归档.json',json.dumps(result,ensure_ascii=False,separators=(',',':')))
    return buf.getvalue()


def market_results(root,name):
    result = read_day(root,name)
    da, inp, audit = result['da'],result['inputs'],result['audit']
    group = {k:[a['id'] for a in inp['assets'] if a['kind']==k] for k in KINDS}
    by_name = {s['name']:s for s in inp['stations']}
    energy = {k:.25*sum(r['power'][u] for r in da for u in ids) for k,ids in group.items()}
    status = '校核通过' if audit['max_slack_mw'] < 1e-5 else '存在可靠性松弛'
    st.subheader('日前预出清结果')
    st.caption(f"{SCENARIOS[result['scenario']]} · 96 个 15 分钟时段 · 冻结申报 {result['company']['declaration_sha256'][:12]} · {status}。日前量价用于计划和分析，不结算。")
    cols = st.columns(4)
    cols[0].metric('全流域水电量',f"{energy['hydro']/1000:,.2f} GWh")
    cols[1].metric('预测负荷峰值',f"{max(sum(r['demand'].values()) for r in da):,.0f} MW")
    cols[2].metric('光伏消纳电量',f"{energy['renewable']/1000:,.2f} GWh")
    cols[3].metric('气电计划电量',f"{energy['fixed']/1000:,.2f} GWh")
    q = st.select_slider('核对时段',list(range(96)),format_func=period,key='da_focus_quarter')
    row = da[q]
    st.markdown('#### 煤机组合与系统出力')
    st.dataframe([{'煤机':a['name'],'节点':a['bus'],'SCUC 状态':'开机' if row['online'][a['id']] else '停机',
        '组合阶段 MW':result['scuc'][q]['power'][a['id']],'固定组合出力 MW':row['power'][a['id']],
        '全天开机 h':.25*sum(t['online'][a['id']] for t in da)} for a in inp['assets'] if a['kind']=='thermal'],
        hide_index=True,width='stretch')
    _chart({**{KINDS[k]:[sum(r['power'][u] for u in ids) for r in da] for k,ids in group.items()},
            '系统负荷':[sum(r['demand'].values()) for r in da]},'MW')
    with st.expander('全电源装机、可用容量与市场采用报价'):
        st.caption(inp.get('pv_category') or result['assumptions']['pv_category'])
        st.dataframe([{'类型':KINDS[k],'设计装机 MW':sum(a['capacity_mw'] for a in inp['assets'] if a['kind']==k),
                       '时段可用 MW':sum(inp['available'][q][u] for u in ids),'时段出力 MW':sum(row['power'][u] for u in ids),
                       '全天电量 MWh':energy[k]} for k,ids in group.items()],hide_index=True)
        policy = result['policy']
        st.write(f"市场力缓解{'已触发' if policy['active'] else '未触发'}。电力供需比 {policy['power_ratio']:.3f}，电量供需比 {policy['energy_ratio']:.3f}。")
        resources = {r['id']:r for r in inp['resources']}
        bids = []
        for a in inp['assets']:
            uid = a['id']
            if uid not in result['raw_offers']:
                continue
            start = resources[uid]['minimum_mw'] if a['kind']=='thermal' else 0.
            awards = row.get('accepted_segments_mw',{}).get(uid,[])
            offset = len(resources[uid]['segments']) - len(result['raw_offers'][uid])
            curves = zip(result['raw_offers'][uid],result['effective_offers'][uid])
            for i, ((width,raw),(_,eff)) in enumerate(curves,start=offset):
                bids.append({'机组':a['name'],'起点 MW':start,'终点 MW':start+width,'申报价 元/MWh':raw,
                             '采用价 元/MWh':eff,'本段中标 MW':awards[i] if awards else None})
                start += width
        st.caption('本段中标取自最终调度的分段变量。早期归档缺少该字段时留空。煤机另计最小稳定出力，气电执行保供计划。')
        st.dataframe(bids,hide_index=True,width='stretch')
        st.dataframe([{'气电保供机组':a['name'],'计划 MW':inp['fixed_schedule'][q][a['id']],
                       '日前安排 MW':row['power'][a['id']]} for a in inp['assets'] if a['kind']=='fixed'],hide_index=True)
    st.markdown('#### 大渡河全流域')
    chosen = st.multiselect('同图比较电站',list(by_name),default=['瀑布沟','深溪沟'],key='da_compare')
    if chosen:
        left,right = st.columns(2)
        with left:
            _chart({n:[r['power'][by_name[n]['id']] for r in da] for n in chosen},'MW')
        with right:
            _chart({n:[price(r,by_name[n]['bus']) for r in da] for n in chosen},'元/MWh')
    stations, lines = tables(result,q)
    st.dataframe(stations,hide_index=True,width='stretch')
    with st.expander('各站全天水量过程与期末库存'):
        st.caption('区间来水与上游到水分列。库容为时段末值，四座库容式电站可跨时段调节。')
        reservoirs = {r['id']:r for r in result['company']['hydraulic_declaration']['reservoirs']}
        st.dataframe([{'电站':n,'初始 亿m³':v['initial_storage_m3']/1e8,'日末 亿m³':da[-1]['hydro'][u]['storage_m3']/1e8,
            '日末下限 亿m³':v['terminal_min_m3']/1e8,'日末上限 亿m³':v['terminal_max_m3']/1e8}
            for n,s in by_name.items() if s['model']=='reservoir' for u in [s['id']] for v in [reservoirs[u]]],hide_index=True)
        field = st.selectbox('水量指标',('区间来水','上游到水','总下泄','弃水','段末库容'))
        key = {'区间来水':'natural_inflow_m3s','上游到水':'upstream_arrival_m3','总下泄':'release_m3s','弃水':'water_spill_m3s','段末库容':'storage_m3'}[field]
        factor = 1e8 if field=='段末库容' else 900 if field=='上游到水' else 1
        if chosen:
            _chart({n:[r['hydro'][by_name[n]['id']][key]/factor for r in da] for n in chosen},'亿 m³' if field=='段末库容' else 'm³/s')
    with st.expander('电网安全、备用占位与 39 节点参考价'):
        st.caption(f"日前可靠性备用需求 {result['margin']['da_requirement_mw']:,.0f} MW，其中预测附加裕度 {result['margin']['da_extra_up_mw']:,.0f} MW。本段煤机预留 {sum(row['thermal_reliability_reserve_mw'].values()):,.1f} MW，缺额 {row['reliability_reserve_short_mw']:.2f} MW。导入的辅助服务占位共 {len(result['awards'])} 条。")
        st.write(f"本段失负荷 {sum(row['shed'].values()):,.3f} MW，过剩松弛 {sum(row['spill'].values()):,.3f} MW，弃光 {sum(row['curtailed_mw'].values()):,.2f} MW。")
        if result['awards']:
            st.dataframe([{'时段':period(a['quarter']),'机组':a['unit'],'调频向上 MW':a['regulation_up_mw'],
                           '调频向下 MW':a['regulation_down_mw'],'备用向上 MW':a['reserve_up_mw']}
                          for a in result['awards']],hide_index=True)
        st.dataframe(sorted(lines,key=lambda l:-l['负载率 %']),hide_index=True,width='stretch')
        st.dataframe([{'节点':int(b),'参考价 元/MWh':v} for b,v in sorted(row['raw_nodal_prices'].items(),key=lambda x:int(x[0]))],hide_index=True)
        st.caption(f"全天最大节点平衡残差 {audit['max_node_residual_mw']:.2g} MW，线路越限 {audit['max_line_overload_mw']:.2g} MW，库容复算误差 {audit['max_storage_error_m3']:.2g} m³，上游到水误差 {audit['max_arrival_error_m3']:.2g} m³。节点价取固定组合 SCED 的原始节点平衡对偶。")
    st.download_button('下载完整日前结果（CSV 与原始数据）',export_day(str(root),name),name[:28]+'-日前结果.zip','application/zip')


def workspace(root):
    from sichuan_v5_dadu_style import apply_dadu_style
    apply_dadu_style()
    st.html("""<style>
        [data-testid="stMainBlockContainer"]{max-width:1460px;padding-top:2.5rem}
        [data-testid="stSkillsNudge"]{display:none}
        .st-key-dadu_run button{background:#125e53;border-color:#125e53}
        [data-testid="stMetric"]{padding:.65rem .9rem;border-left:3px solid #45857c;background:#f4f8f6;color:#173746}
        </style>""")
    st.title('大渡河 · 日前现货市场')
    if 'dadu_workspace' not in st.session_state and st.query_params.get('stage') == 'da':
        st.session_state['dadu_workspace'] = '日前预出清'
    if 'dadu_scenario' not in st.session_state and st.query_params.get('scenario') in SCENARIOS:
        st.session_state['dadu_scenario'] = st.query_params['scenario']
    if 'da_next_page' in st.session_state:
        st.session_state['dadu_workspace'] = st.session_state.pop('da_next_page')
    page = st.radio('工作区',['机组申报','日前预出清'],horizontal=True,key='dadu_workspace')
    scenario = st.selectbox('申报场景',list(SCENARIOS),format_func=SCENARIOS.get,key='dadu_scenario')
    if page == '日前预出清':
        names = list_days(root,scenario)
        if not names:
            st.info('该场景尚无日前结果。请在机组申报页冻结申报并运行日前预出清。')
            return
        name = st.selectbox('已发布的日前结果',names,format_func=archive_label,key='da_archive_'+scenario)
        try:
            market_results(root,name)
        except (ValueError,OSError) as exc:
            st.error(f'读取日前归档失败：{exc}')
        return
    sheet = _load_sheet(scenario)
    company = sheet['company']
    submitted = declaration_path(root,company).is_file()
    c1,c2,c3 = st.columns(3)
    if c1.button('确认并冻结 19 站申报',key='dadu_freeze',width='stretch'):
        freeze_declaration(root,company)
        submitted = True
    run = c2.button('运行日前预出清',disabled=not submitted,key='dadu_run',type='primary',width='stretch')
    c3.download_button('下载本场景申报单',json.dumps(declaration_record(company),ensure_ascii=False),
                       scenario+'-申报.json','application/json',width='stretch')
    st.caption(('已冻结 · '+company['declaration_sha256'][:12] if submitted else '待确认申报')+'　｜　19 站水电耦合 · 96 个时段 · 日前预出清不结算')
    with st.expander('市场边界与其他电源申报'):
        st.caption('默认申报按站冻结。水电核准区间为申报区间与技术可用容量的交集，出清另用市场侧来水。气电执行外部保供计划，普通集中式光伏采用全量零价申报。')
        from sichuan_v5_dadu_case import build_case
        case,_ = build_case(scenario,39)
        assets = {a['id']:a for a in sheet['inputs']['assets']}
        rows = []
        for u in case.resources:
            if u.kind=='hydro':
                continue
            start = 0.
            for width,p in u.segments or [(u.capacity_mw,None)]:
                stop = start+width
                rows.append({'资源':assets[u.id]['name'],'类型':KINDS[u.kind],'节点':u.bus,'起点 MW':start,'终点 MW':stop,
                    '申报价 元/MWh':p if u.kind!='thermal' or start else None,'用途':'保供计划' if u.kind=='fixed' else
                    '最小稳定出力' if u.kind=='thermal' and start==0 else '竞价段'})
                start = stop
        st.dataframe(rows,hide_index=True)
        _chart({'全网预测负荷':[sum(r.values()) for r in case.demand],'光伏可用':[sum(r[u.id] for u in case.resources if u.kind=='renewable') for r in case.available],
                '气电保供计划':[sum(r.values()) for r in case.fixed_schedule]},'MW')
        st.caption('辅助服务中标量作为外部边界，可导入 CSV。默认无外部中标占位。时段序号为 1–96。')
        st.download_button('下载占位模板','\ufeff时段序号,机组编号,调频向上MW,调频向下MW,备用向上MW\n','辅助服务占位.csv','text/csv')
        upload = st.file_uploader('导入调频、备用占位（可选）',type='csv',key='da_awards_'+scenario)
    if run:
        try:
            records = read_awards(upload)
            name,_ = solve_visible(run_day,root,dict(scenario=scenario,declaration_sha256=company['declaration_sha256'],awards=records),
                                   st.container(),'日前预出清',day_only=True)
        except (ValueError,RuntimeError,OSError,TypeError,csv.Error) as exc:
            st.error(str(exc))
        else:
            st.session_state['da_archive_'+scenario] = name
            st.session_state['da_next_page'] = '日前预出清'
            st.rerun()
    render_sheet(sheet,SCENARIOS[scenario])
    with st.expander('同时比较多个站的 96 点申报'):
        stations = {s['name']:s['id'] for s in sheet['inputs']['stations']}
        selection = st.multiselect('比较申报电站',list(stations),default=['瀑布沟','深溪沟'],key='da_filing_compare')
        field = st.radio('比较指标',['能力上限','能力下限','区间来水'],horizontal=True)
        key = {'能力上限':'available_mw','能力下限':'minimum_mw','区间来水':'natural_inflow_m3s'}[field]
        if selection:
            _chart({n:company['offers'][stations[n]][key] for n in selection},'m³/s' if field=='区间来水' else 'MW')
