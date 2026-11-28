"""Single-day Dadu filing, published dispatch, and operator verification."""
import csv
from datetime import datetime, timedelta
from io import BytesIO, StringIO
import json
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd
import streamlit as st

from sichuan_v5_ancillary import Award
from sichuan_v5_da_report import render_review, review_data, standalone_report
from sichuan_v5_da_review import station_quarters, system_quarters
from sichuan_v5_dadu_da import (DAY_SCENARIOS, ERROR_LEVELS, LEGACY_SCENARIOS, NET_ERROR_SIGMA,
                                SEASONS, SEASON_TEMPLATE, list_days, read_day, run_day)
from sichuan_v5_dadu_live import solve_visible
from sichuan_v5_dadu_lesson import lesson_html, packed_lesson, render_lesson
from sichuan_v5_dadu_operations import DayOperations, LABELS, UNITS, with_operations
from sichuan_v5_pumped_storage import build_day_case
from sichuan_v5_dadu_run import declaration_path, declaration_record, freeze_declaration
from sichuan_v5_dadu_view import KINDS, SCENARIOS as LEGACY_LABELS, _chart, _load_sheet
from sichuan_v5_declaration_sheet import render_sheet

DAY_KINDS = {**KINDS,'storage':'抽水蓄能'}


def stamp(q):
    return f'{q//4:02}:{q%4*15:02}'


def period(q):
    return stamp(q)+'–'+stamp(q+1)


def scenario_label(scenario):
    if scenario in DAY_SCENARIOS:
        season, level = DAY_SCENARIOS[scenario]
        return f'{SEASONS[season]} · {ERROR_LEVELS[level]}'
    return LEGACY_LABELS[scenario]


def archive_label(name):
    scenario, date, digest = name.rsplit('_',2)
    local = datetime.strptime(date,'%Y%m%dT%H%M%S%fZ') + timedelta(hours=8)
    return f'{scenario_label(scenario)} · {local:%m-%d %H:%M:%S} · {digest[:6]}'


def price(row,bus):
    values = row['raw_nodal_prices']
    return values.get(str(bus), values.get(bus))


def pump_rows(result):
    spec = result['inputs'].get('pumped_storage')
    if not spec:
        return []
    uid = spec['id']
    return [{'时段':period(q),'日前净计划 MW':spec['signed_mw'][q],
             'SCUC 抽水 MW':uc['charge'][uid],'SCUC 发电 MW':uc['power'][uid],
             'SCED 抽水 MW':r['charge'][uid],'SCED 发电 MW':r['power'][uid],
             '净注入 MW':r['power'][uid]-r['charge'][uid],'段末库存 MWh':r['energy'][uid],
             '节点参考价 元/MWh':price(r,spec['bus'])}
            for q,(uc,r) in enumerate(zip(result['scuc'],result['da']))]


def pump_schedule_text(p):
    def window(start,count):
        return stamp(start)+'–'+stamp(start+count)
    return (f"节点 {p['bus']} · 抽水、发电各 {p['capacity_mw']:,.0f} MW　｜　"
            f"{window(p['pump_start'],p['pump_quarters'])} 抽水　→　"
            f"{window(p['generate_start'],p['generate_quarters'])} 发电")


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


def instruction_label(e):
    k = e['kind']
    return LABELS[k]+(f" {e['value']:g} {UNITS[k]}" if UNITS[k] else '')


def instruction_rows(entries,names):
    return [{'电站':names[e['station']],'生效时间':stamp(e['first'])+'–'+stamp(e['last']+1),
             '工况或要求':instruction_label(e)} for e in entries]


def tables(result, q):
    row = result['da'][q]
    inp = result['inputs']
    stations = []
    for s in inp['stations']:
        uid = s['id']
        h = row['hydro'][uid]
        low, high = result['bounds'][q][uid]
        filing = result['company'].get('operations')
        active = [e for e in filing['entries'] if e['station']==uid and e['first']<=q<=e['last']] if filing else []
        island = sum(e['value'] for e in active if e['kind']=='island')
        level = None
        if s['model']=='reservoir' and s.get('storage_m3',0)>0 and s.get('reference_dead_level_m') and s.get('reference_normal_level_m'):
            dead,normal = float(s['reference_dead_level_m']),float(s['reference_normal_level_m'])
            level = dead+(normal-dead)*h['storage_m3']/s['storage_m3']
        status = '可用' if filing else '历史未归档'
        for entry in active:
            if entry['kind'] in ('offline','island'):
                status = LABELS[entry['kind']]
        stations.append({'时段':period(q),'电站':s['name'],'节点':s['bus'],'确认工况':status,
            '运行要求':'、'.join(map(instruction_label,active)) or '—','孤网计划 MW':island if filing else None,
            '核准下限 MW':low,
            '核准上限 MW':high,'日前并网 MW':row['power'][uid],'节点参考价 元/MWh':price(row,s['bus']),
            '区间来水 m³/s':h['natural_inflow_m3s'],'上游到水 m³/s':h['upstream_arrival_m3']/900,
            '过机 m³/s':h['turbine_m3s'],'旁泄 m³/s':h['eco_bypass_m3s'],'弃水 m³/s':h['water_spill_m3s'],
            '总下泄 m³/s':h['release_m3s'],'段末库容 亿m³':h['storage_m3']/1e8,'折算段末水位 m':level})
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
                units.append({'时段':period(q),'资源':a['name'],'编号':uid,'类型':DAY_KINDS[a['kind']],
                        '设计装机 MW':a['capacity_mw'],'可用 MW':result['inputs']['available'][q][uid],
                        'SCUC出力 MW':result['scuc'][q]['power'][uid], '煤机开机':r['online'].get(uid),
                        'SCED中标或计划 MW':r['power'][uid],'电量 MWh':.25*r['power'][uid],
                        '抽水 MW':r['charge'].get(uid,0.),'净注入 MW':r['power'][uid]-r['charge'].get(uid,0.),
                        '节点参考价 元/MWh':price(r,a['bus'])})
            prices.append({'时段':period(q),**{f'节点{b}':v for b,v in r['raw_nodal_prices'].items()}})
        write('各机组日前计划.csv',units)
        if result['inputs'].get('pumped_storage'):
            write('抽水蓄能96点计划与库存.csv',pump_rows(result))
        diagnostics = []
        for station in result['inputs']['stations']:
            for data in station_quarters(result,station):
                diagnostics.append({'电站':station['name'],'编号':station['id'],'时段':period(data['quarter']),
                                    '出清弃水 m³/s':data['spill'],'条件下界 m³/s':data['floor'],
                                    '超出下界 m³/s':data['remaining'],'期初库存 m³':data['initial_m3'],
                                    '段末库存 m³':data['storage_m3'],'本段区间来水 m³/s':data['local'],
                                    '上游到水 m³/s':data['arrival']})
        write('各站弃水条件下界.csv',diagnostics)
        ref = result.get('storage_comparison',{})
        if ref.get('status')=='verified':
            active = system_quarters(result['da'],result['inputs']['assets'],result['inputs']['lines'])
            fields = {'hydro':'常规水电 MW','thermal':'煤电 MW','station_spill_m3':'各站弃水之和 m³',
                      'pv_cut':'弃光 MW','weighted_price':'负荷加权价 元每MWh','residual_load':'剩余负荷 MW',
                      'pump':'抽水 MW','storage':'抽蓄发电 MW'}
            paired = []
            for q,(on,off) in enumerate(zip(active,ref['quarters'])):
                values = {'时段':period(q)}
                for k,label in fields.items():
                    values['投入 '+label],values['停运 '+label] = on[k],off[k]
                    values['投入减停运 '+label] = on[k]-off[k] if off[k] is not None and on[k] is not None else None
                paired.append(values)
            write('抽蓄投运停运对照.csv',paired)
        write('各站申报边界与水账.csv',stations)
        write('线路潮流.csv',lines)
        write('39节点参考价_元每MWh.csv',prices)
        z.writestr('完整日前归档.json',json.dumps(result,ensure_ascii=False,separators=(',',':')))
    return buf.getvalue()


def market_results(root,name):
    result = read_day(root,name)
    inp, audit = result['inputs'],result['audit']
    status = '校核通过' if audit['max_slack_mw'] < 1e-5 else '存在可靠性松弛'
    st.subheader('日前预出清结果')
    st.caption(f"{scenario_label(result['scenario'])} · 96 个 15 分钟时段 · 冻结申报 {result['company']['declaration_sha256'][:12]} · {status}。日前量价用于计划和分析，不结算。")
    if result.get('scenario_settings'):
        m = result['margin']
        st.caption(f"代表月 {result['scenario_settings']['month']} 月 · 日前净负荷预测误差标准差按负荷峰值的 {m['assumed_net_error_sigma_fraction']:.0%} 取值 · 向上预留 {m['da_extra_up_mw']:,.0f} MW。")
    if 'operations' in result['company']:
        entries = result['company']['operations']['entries']
        st.caption(f"已确认 19 站 × 96 点工况，分时例外 {len(entries)} 条。孤网供电 {audit['island_energy_mwh']:,.2f} MWh 单列，以下水电量为主网口径。")
        if entries:
            st.dataframe(instruction_rows(entries,{s['id']:s['name'] for s in inp['stations']}),hide_index=True,width='stretch')
    review = review_data(result,scenario_label(result['scenario']))
    render_review(review,key='day_review_'+name)
    a,b = st.columns(2)
    a.download_button('下载交互复盘（离线 HTML）',standalone_report([review]),result['scenario']+'-日前复盘.html','text/html')
    b.download_button('下载完整日前结果（CSV 与原始数据）',export_day(str(root),name),name[:28]+'-日前结果.zip','application/zip')
    with st.expander('更多调度明细 · 各类电源、抽蓄库存、19 站水账与 39 节点价格'):
        _dispatch_detail(result)


def _dispatch_detail(result):
    da,inp,audit = result['da'],result['inputs'],result['audit']
    group = {k:[a['id'] for a in inp['assets'] if a['kind']==k] for k in DAY_KINDS
             if any(a['kind']==k for a in inp['assets'])}
    by_name = {s['name']:s for s in inp['stations']}
    energy = {k:.25*sum(r['power'][u] for r in da for u in ids) for k,ids in group.items()}
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
    supply = {DAY_KINDS[k]:[sum(r['power'][u] for u in ids) for r in da] for k,ids in group.items()}
    supply['用户负荷'] = [sum(r['demand'].values()) for r in da]
    if inp.get('pumped_storage'):
        supply['含抽水的总负荷'] = [sum(r['demand'].values())+sum(r['charge'].values()) for r in da]
    _chart(supply,'MW')
    if inp.get('pumped_storage'):
        spec = inp['pumped_storage']
        log = pump_rows(result)
        totals = audit['pumped_storage']
        st.subheader('抽水蓄能 · 抽发与库存',anchor='pumped-storage')
        st.caption(pump_schedule_text(spec))
        cols = st.columns(4)
        for col,label,key in zip(cols,['抽水用电','发电上网','循环损耗','日末可发电量'],
                                 ['pumping_mwh','generation_mwh','cycle_loss_mwh','terminal_mwh']):
            col.metric(label,f"{totals[key]:,.0f} MWh")
        left,right = st.columns(2)
        with left:
            _chart({'计划净注入':[r['日前净计划 MW'] for r in log],'出清净注入':[r['净注入 MW'] for r in log]},'MW')
        with right:
            _chart({'上库可发电量':[r['段末库存 MWh'] for r in log]},'MWh')
        st.caption(f"当前 {period(q)}　抽水 {log[q]['SCED 抽水 MW']:,.0f} MW · 发电 {log[q]['SCED 发电 MW']:,.0f} MW · "
                   f"段末库存 {log[q]['段末库存 MWh']:,.0f} MWh · 节点参考价 {log[q]['节点参考价 元/MWh']:.2f} 元/MWh。")
        with st.expander('查看抽蓄 96 点计划、出清及节点参考价'):
            st.dataframe(log,hide_index=True,width='stretch')
    with st.expander('全电源装机、可用容量与市场采用报价'):
        st.caption(inp.get('pv_category') or result['assumptions']['pv_category'])
        st.dataframe([{'类型':DAY_KINDS[k],'设计装机 MW':sum(a['capacity_mw'] for a in inp['assets'] if a['kind']==k),
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
        st.caption('区间来水与上游到水分列。库容为时段末值，四座库容式电站可跨时段调节。段末水位由调节库容作线性换算，仅供算例核对。')
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


def published_days(root,scenario):
    names = list_days(root,scenario)
    if not names:
        st.info('该场景尚无日前结果。请在机组申报页冻结申报并运行日前预出清。')
        return
    name = st.selectbox('已发布的日前结果',names,format_func=archive_label,key='da_archive_'+scenario)
    try:
        market_results(root,name)
    except (ValueError,OSError) as exc:
        st.error(f'读取日前归档失败：{exc}')


def edit_operations(root,sheet):
    base,catalog = sheet['company'],sheet['inputs']['stations']
    names = {s['id']:s['name'] for s in catalog}
    key = 'da_instructions_'+base['declaration_sha256']
    pointer = Path(root)/'dadu19_declarations'/('latest-'+base['declaration_sha256']+'.txt')
    if key not in st.session_state:
        entries = []
        if pointer.is_file():
            digest = pointer.read_text(encoding='ascii')
            if len(digest)==64 and all(c in '0123456789abcdef' for c in digest):
                saved = json.loads(declaration_path(root,dict(declaration_sha256=digest)).read_bytes())
                entries = saved['operations']['entries']
                if with_operations(base,entries)['declaration_sha256'] != digest:
                    raise ValueError('最近冻结的工况与本水期不一致')
        st.session_state[key] = entries
    entries = st.session_state[key]
    with st.expander(f'次日工况与特殊运行要求　·　{len(entries)} 条例外',expanded=bool(entries)):
        st.caption('草案 §7.5.1 要求竞价日 09:45 前反馈次日 96 点工况，§7.5.11 规定特殊运行要求。本算例预置 19 站全天可用，按北京时间录入例外。冻结后由程序校验。')
        if entries:
            st.dataframe(instruction_rows(entries,names),hide_index=True,width='stretch')
        a,b = st.columns(2)
        uid = a.selectbox('申报电站',list(names),format_func=names.get,index=list(names.values()).index('瀑布沟'),key='da_op_station')
        station = next(s for s in catalog if s['id']==uid)
        kinds = [k for k in LABELS if station['model']=='reservoir' or not k.startswith('level_')]
        kind = b.selectbox('工况或特殊要求',kinds,format_func=LABELS.get,key='da_op_kind')
        a,b,c = st.columns(3)
        first = a.selectbox('开始时间',range(96),format_func=stamp,key='da_op_first')
        end = b.selectbox('结束时间',range(1,97),format_func=stamp,index=95,key='da_op_end')
        offer = base['offers'][uid]
        lake = next(r for r in base['hydraulic_declaration']['reservoirs'] if r['id']==uid)
        default = 0.
        if first < end:
            lower = max(offer['minimum_mw'][first:end])
            upper = min(offer['available_mw'][first:end])
            if kind in ('minimum','maximum','fixed'):
                st.caption(f'所选各段共同的基础并网区间为 {lower:,.1f}–{upper:,.1f} MW。')
                default = {'minimum':lower,'maximum':upper,'fixed':(lower+upper)/2}[kind]
            elif kind=='island':
                default = min(offer['technical_available_mw'][first:end])/2
            elif kind=='release':
                default = lake['min_release_m3s']
            elif kind.startswith('level_'):
                dead,normal = float(station['reference_dead_level_m']),float(station['reference_normal_level_m'])
                default = dead+(normal-dead)*lake['initial_storage_m3']/station['storage_m3']
        value = 0.
        if kind != 'offline':
            value = c.number_input('控制值（'+UNITS[kind]+'）',min_value=0.,value=float(default),format='%.3f',
                                   key='da_op_value_'+kind+'_'+uid)
        if kind.startswith('level_'):
            st.caption(f"本算例在死水位 {station['reference_dead_level_m']} m 与正常蓄水位 {station['reference_normal_level_m']} m 之间线性换算段末调节库容，并校核水期运行边界。")
        if kind=='island':
            st.caption('孤网按给定功率供本地负荷。过机耗水计入梯级，主网中标为零。')
        if st.button('加入申报',key='da_op_add'):
            try:
                candidate = with_operations(base,[*entries,dict(station=uid,kind=kind,first=first,last=end-1,value=value)])
                DayOperations(candidate,catalog)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.session_state[key] = candidate['operations']['entries']
                st.rerun()
        if entries:
            index = st.selectbox('选择要撤回的例外',range(len(entries)),format_func=lambda i:
                f"{names[entries[i]['station']]} {stamp(entries[i]['first'])}–{stamp(entries[i]['last']+1)} {instruction_label(entries[i])}")
            if st.button('撤回所选例外',key='da_op_remove'):
                st.session_state[key] = entries[:index]+entries[index+1:]
                st.rerun()
    return with_operations(base,entries),pointer


@st.cache_data(show_spinner=False, max_entries=6)
def _lesson_archive(root, name, label):
    return packed_lesson(root, name, label)


def beginner_workspace(root, scenario):
    names = list_days(root, scenario)
    if names:
        try:
            case = _lesson_archive(str(root), names[0], scenario_label(scenario))
            render_lesson(case, key='lesson_'+names[0])
            st.download_button('下载这一课，离线练习', lesson_html([case]), scenario+'-日前入门.html','text/html')
        except (OSError, ValueError) as exc:
            st.error(f'打开教学算例失败：{exc}')
    else:
        st.info('这个场景还没有日前结果。先使用默认申报运行一次，出清后即可在这里练习。')
    if st.button('用这个场景自己申报', key='da_lesson_file'):
        st.session_state['da_next_page'] = '机组申报'
        st.rerun()


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
    requested = st.query_params.get('scenario')
    if requested in LEGACY_SCENARIOS and st.query_params.get('stage') == 'da':
        st.caption('历史场景 · '+LEGACY_LABELS[requested])
        if st.button('返回水期与预测误差选择'):
            st.query_params.clear()
            st.rerun()
        published_days(root,requested)
        return
    pages = {'learn':'入门体验', 'filing':'机组申报', 'da':'日前预出清'}
    if 'dadu_workspace' not in st.session_state:
        default = 'learn' if list_days(root, requested if requested in DAY_SCENARIOS else 'wet_normal') else 'filing'
        st.session_state['dadu_workspace'] = pages.get(st.query_params.get('stage'), pages[default])
    initial_season, initial_level = DAY_SCENARIOS.get(requested, ('wet','normal'))
    if 'da_next_page' in st.session_state:
        st.session_state['dadu_workspace'] = st.session_state.pop('da_next_page')
    page = st.radio('工作区',list(pages.values()),horizontal=True,key='dadu_workspace')
    left, right = st.columns(2)
    st.session_state.setdefault('da_season', initial_season)
    st.session_state.setdefault('da_error_level', initial_level)
    season = left.radio('水期',list(SEASONS),format_func=SEASONS.get,horizontal=True,key='da_season')
    level = right.radio('预测误差',list(ERROR_LEVELS),format_func=lambda k:'正常' if k=='normal' else '极端',
                        horizontal=True,key='da_error_level')
    scenario = f'{season}_{level}'
    stage = next(k for k, v in pages.items() if v == page)
    if (st.query_params.get('scenario'), st.query_params.get('stage')) != (scenario,stage):
        st.query_params.from_dict({'scenario':scenario,'stage':stage})
    if page == '入门体验':
        beginner_workspace(root,scenario)
        return
    if page == '日前预出清':
        published_days(root,scenario)
        return
    template = SEASON_TEMPLATE[season]
    sheet = _load_sheet(template)
    st.caption(f'同一水期共用来水、负荷预测和机组申报。本组合按负荷峰值的 {NET_ERROR_SIGMA[level]:.0%} 模拟净负荷预测误差标准差，用于确定日前向上裕度。')
    st.caption('第一次动手可以直接用默认参数：先点“确认并冻结 19 站申报”，保存这次提交的内容；再点“运行日前预出清”，查看市场排出的明日计划。')
    company,pointer = edit_operations(root,sheet)
    ops = DayOperations(company,sheet['inputs']['stations'])
    submitted = declaration_path(root,company).is_file()
    c1,c2,c3 = st.columns(3)
    if c1.button('确认并冻结 19 站申报',key='dadu_freeze',width='stretch'):
        freeze_declaration(root,company)
        temporary = pointer.with_suffix('.'+uuid4().hex+'.tmp')
        try:
            temporary.write_text(company['declaration_sha256'],encoding='ascii')
            temporary.replace(pointer)
        finally:
            temporary.unlink(missing_ok=True)
        submitted = True
    run = c2.button('运行日前预出清',disabled=not submitted,key='dadu_run',type='primary',width='stretch')
    c3.download_button('下载本场景申报单',json.dumps(declaration_record(company),ensure_ascii=False),
                       scenario+'-申报.json','application/json',width='stretch')
    st.caption(('已冻结 · '+company['declaration_sha256'][:12] if submitted else '待确认申报')+'　｜　19 站水电耦合 · 96 个时段 · 日前预出清不结算')
    case,meta = build_day_case(template)
    pump = meta['pumped_storage']
    st.subheader('抽水蓄能 · 日前抽发计划',anchor='pumped-storage-plan')
    st.write(pump_schedule_text(pump))
    st.caption(f"独立闭式抽蓄按外部计划参与本算例。可发电量上限 {pump['usable_energy_mwh']:,.0f} MWh，"
               f"期初和日末目标均为 {pump['initial_mwh']:,.0f} MWh，往返效率 {pump['cycle_efficiency']:.0%}。")
    with st.expander('查看抽蓄计划曲线与取值依据'):
        st.write(pump['plan_basis']+' 两种工况之间至少停机 15 分钟。功率以向电网送电为正，抽水为负。')
        _chart({'抽蓄计划净注入':pump['signed_mw']},'MW')
        st.download_button('下载抽蓄日前计划',json.dumps(pump,ensure_ascii=False),scenario+'-抽蓄计划.json','application/json')
    with st.expander('市场边界与其他电源申报'):
        st.caption('默认申报按站冻结。水电核准区间为申报区间与技术可用容量的交集，出清另用市场侧来水。气电执行外部保供计划，普通集中式光伏采用全量零价申报。')
        assets = {a['id']:a for a in meta['assets']}
        rows = []
        for u in case.resources:
            if u.kind=='hydro':
                continue
            start = 0.
            for width,p in u.segments or [(u.capacity_mw,None)]:
                stop = start+width
                rows.append({'资源':assets[u.id]['name'],'类型':DAY_KINDS[u.kind],'节点':u.bus,'起点 MW':start,'终点 MW':stop,
                    '申报价 元/MWh':p if u.kind!='thermal' or start else None,'用途':'日前抽发计划' if u.kind=='storage' else '保供计划' if u.kind=='fixed' else
                    '最小稳定出力' if u.kind=='thermal' and start==0 else '竞价段'})
                start = stop
        st.dataframe(rows,hide_index=True)
        _chart({'全网预测负荷':[sum(r.values()) for r in case.demand],'光伏可用':[sum(r[u.id] for u in case.resources if u.kind=='renewable') for r in case.available],
                '气电保供计划':[sum(r.values()) for r in case.fixed_schedule]},'MW')
        st.caption('辅助服务中标量作为外部边界，可导入 CSV。默认无外部中标占位。时段序号为 1–96。')
        st.download_button('下载占位模板','\ufeff时段序号,机组编号,调频向上MW,调频向下MW,备用向上MW\n','辅助服务占位.csv','text/csv')
        upload = st.file_uploader('导入调频、备用占位（可选）',type='csv',key='da_awards_'+scenario)
        compare = st.checkbox('同时复算抽蓄停运对照',value=True,key='da_psh_compare')
        st.caption('勾选后，以同一份冻结申报分别运行抽蓄投运和停运的日前 SCUC、SCED。')
    if run:
        try:
            records = read_awards(upload)
            name,_ = solve_visible(run_day,root,dict(scenario=scenario,declaration_sha256=company['declaration_sha256'],awards=records,compare_storage=compare),
                                   st.container(),'日前预出清',day_only=True)
        except (ValueError,RuntimeError,OSError,TypeError,csv.Error) as exc:
            st.error(str(exc))
        else:
            st.session_state['da_archive_'+scenario] = name
            st.session_state['da_next_page'] = '日前预出清'
            st.rerun()
    render_sheet(dict(sheet,company=company),scenario_label(scenario),operations=ops)
    with st.expander('同时比较多个站的 96 点申报'):
        stations = {s['name']:s['id'] for s in sheet['inputs']['stations']}
        selection = st.multiselect('比较申报电站',list(stations),default=['瀑布沟','深溪沟'],key='da_filing_compare')
        field = st.radio('比较指标',['能力上限','能力下限','区间来水'],horizontal=True)
        key = {'能力上限':'available_mw','能力下限':'minimum_mw','区间来水':'natural_inflow_m3s'}[field]
        if selection:
            curves = {}
            for n in selection:
                uid = stations[n]
                offer = company['offers'][uid]
                curves[n] = offer[key] if field=='区间来水' else [
                    ops.grid_bounds(uid,t,lo,hi)[int(field=='能力上限')]
                    for t,(lo,hi) in enumerate(zip(offer['minimum_mw'],offer['available_mw']))]
            _chart(curves,'m³/s' if field=='区间来水' else 'MW')
