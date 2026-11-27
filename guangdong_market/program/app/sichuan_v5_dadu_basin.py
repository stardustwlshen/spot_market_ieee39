"""Independent basin-wide summaries of the archived DA plan and executed RT intervals."""
from math import fsum, isfinite

import pandas as pd
import streamlit as st

from sichuan_v5_dadu_charts import time_chart
from sichuan_v5_dadu_water_results import station_water_ledger


def _total(values):
    items = list(values)
    if not items or any(not isinstance(x, (int, float)) or not isfinite(x) for x in items):
        return None
    return fsum(items)


def _net(storage0, river0, inflow, outflow, storage1, river1):
    terms = (storage0, river0, inflow, outflow, storage1, river1)
    if _total(terms) is None:
        return None
    return fsum((storage0, river0, inflow, -outflow, -storage1, -river1))


def _vol(rate):
    return None if rate is None else rate * 900.


def _energy(power):
    return None if power is None else .25 * power


def _time(slot):
    return f'{slot // 4:02d}:{slot % 4 * 15:02d}'


def _queue(values, lag):
    if not isinstance(values, (tuple, list)) or len(values) != lag or _total(values) is None:
        return None
    return tuple(values)


def _stage(result, stage, station_ids, names, reservoirs, edges, outlet, ledger):
    case = result.get('inputs', {})
    available = case.get('available') or (case.get('market_case') or {}).get('available') or []
    model = {r['id']: r for r in reservoirs}
    regulation = {u:model.get(u, {}).get('regulation') or names.get(u, {}).get('model') for u in station_ids}
    four = [u for u in station_ids if regulation[u] == 'reservoir']
    all_classified = all(kind in ('reservoir', 'run_of_river') for kind in regulation.values())
    initq = {k: _queue(e.get('initial_arrivals_m3'), e.get('travel_quarters')) for k,e in edges.items()}
    prev_t, queues = -1, initq
    periods, stations, links = [], [], []
    for pos, item in enumerate(result.get(stage) or []):
        t = item.get('quarter', pos)
        hydro = ledger.get(t, {})
        capacity = (available[t] or {}) if 0 <= t < len(available) else {}
        power = item.get('power') or {}
        section = {u: hydro.get(u, {}).get('release_m3s') for u in station_ids}
        if prev_t != t-1:
            queues = {}
        handoff = item.get('water_handoff') or {}
        observed = {(e.get('upstream'),e.get('downstream')): e.get('next_arrivals_m3')
                    for e in handoff.get('in_transit', [])}
        river0, river1, next_queues = [], [], {}
        for key, edge in edges.items():
            upstream, downstream = key
            before = queues.get(key)
            entered = _vol(section.get(upstream))
            lag = edge.get('travel_quarters')
            if stage == 'rt':
                after = _queue(observed.get(key),lag)
            else:
                # The DA archive saves its dispatch and warm-start queue. Apply
                # the recorded integer lag once per release, even past midnight.
                if before is None and isinstance(lag, int) and lag > 0:
                    before = (None,) * lag
                after = before[1:] + (entered,) if before else None
            start, end = _total(before or ()), _total(after or ())
            river0.append(start)
            river1.append(end)
            next_queues[key] = after
            links.append({'quarter':t,'upstream':upstream,'downstream':downstream,
                          'lag_quarters':lag,'transit_begin_m3':start,'transit_end_m3':end})
        queues, prev_t = next_queues, t
        out = handoff.get('basin_exit_m3') if stage == 'rt' else None
        if isinstance(out,dict) and out:
            # A missing outlet in a partial map must not look like zero export.
            export = _total([out.get(outlet)]) if outlet else _total(out.values())
        else:
            export = _vol(section.get(outlet))
        items = []
        for u in station_ids:
            water = hydro.get(u, {})
            rec = {'quarter':t,'id':u,'name':names.get(u, {}).get('name',u),
                   'generation_mw':power.get(u),'capacity_mw':capacity.get(u),
                   'generation_mwh':_energy(power.get(u)),'capacity_mwh':_energy(capacity.get(u)),
                   'inflow_m3':_vol(water.get('natural_inflow_m3s')),
                   'upstream_arrival_m3':water.get('upstream_arrival_m3'),
                   'turbine_m3':_vol(water.get('turbine_m3s')),
                   'bypass_m3':_vol(water.get('eco_bypass_m3s')),
                   'spill_m3':_vol(water.get('water_spill_m3s')),
                   'storage_begin_m3':water.get('storage_begin_m3'),'storage_end_m3':water.get('storage_end_m3')}
            items.append(rec)
            stations.append(rec)
        sums = {field:_total(row[field] for row in items) for field in
                ('generation_mw','capacity_mw','generation_mwh','capacity_mwh','inflow_m3','spill_m3',
                 'storage_begin_m3','storage_end_m3')}
        reservoir_end = _total(hydro.get(u, {}).get('storage_end_m3') for u in four) if all_classified else None
        start_river, end_river = _total(river0), _total(river1)
        # Only a complete, connected chain represents the whole river.
        if outlet is None:
            start_river = end_river = None
        residual = _net(sums['storage_begin_m3'],start_river,sums['inflow_m3'],export,
                        sums['storage_end_m3'],end_river)
        periods.append(dict(sums, quarter=t, interval_end=_time(t+1), four_storage_m3=reservoir_end,
                            transit_begin_m3=start_river,transit_end_m3=end_river,
                            outlet_m3=export,residual_m3=residual))
    return {'periods':periods,'stations':stations,'reaches':links,'reservoir_ids':four}


def basin_tables(result):
    """Both stages, using only the frozen case and archive. All volumes are m³."""
    inputs = result.get('inputs') or {}
    cascade = (inputs.get('market_case') or {}).get('cascade') or {}
    reservoirs = cascade.get('reservoirs') or []
    names = {s['id']:s for s in inputs.get('stations', [])}
    edges = {(e['upstream'],e['downstream']):e for e in cascade.get('reaches', [])}
    ledgers = {}
    ids = dict.fromkeys((*names, *(r['id'] for r in reservoirs)))
    for stage in ('da','rt'):
        by_period = {}
        for row in station_water_ledger(result,stage):
            ids[row['id']] = None
            by_period.setdefault(row['quarter'], {})[row['id']] = row
        ledgers[stage] = by_period
    nodes = set(ids)
    downstream = {v for _, v in edges}
    # Verify topology from the source to the outlet to reject isolated cycles,
    # duplicate discharges or an archive with one of the 18 links missing.
    heads = nodes-downstream
    visited = set()
    targets = {u:v for u,v in edges}
    here = next(iter(heads)) if len(heads) == 1 else None
    while here in nodes and here not in visited:
        visited.add(here)
        if here not in targets:
            break
        here=targets[here]
    outlet = here if nodes and visited==nodes and len(edges)==len(nodes)-1 and here not in targets else None
    return {stage:_stage(result,stage,tuple(ids),names,reservoirs,edges,outlet,ledgers[stage])
            for stage in ('da','rt')}


def basin_balance(periods):
    """Close the selected chronological coverage using initial and final stocks once."""
    if not periods:
        return {}
    first,last=periods[0],periods[-1]
    contiguous=all(a['quarter']+1==b['quarter'] for a,b in zip(periods,periods[1:]))
    natural=_total(p['inflow_m3'] for p in periods) if contiguous else None
    out=_total(p['outlet_m3'] for p in periods) if contiguous else None
    return dict(start=_time(first['quarter']),end=_time(last['quarter']+1),n=len(periods),
                storage_begin_m3=first['storage_begin_m3'],river_begin_m3=first['transit_begin_m3'],
                inflow_m3=natural,outlet_m3=out,storage_end_m3=last['storage_end_m3'],
                river_end_m3=last['transit_end_m3'],
                generation_mwh=_total(p['generation_mwh'] for p in periods) if contiguous else None,
                capacity_mwh=_total(p['capacity_mwh'] for p in periods) if contiguous else None,
                spill_m3=_total(p['spill_m3'] for p in periods) if contiguous else None,
                residual_m3=_net(first['storage_begin_m3'],first['transit_begin_m3'],natural,out,
                                 last['storage_end_m3'],last['transit_end_m3']))


def _fmt(value,digits=2):
    return '未记录' if value is None else f'{value:,.{digits}f}'


def _scaled(value,scale):
    return value/scale if value is not None else None


def render_basin(result, quarter):
    """The shared quarter control is a zero-based index of the executed archive."""
    tables=basin_tables(result)
    rt=tables['rt']['periods']
    da=tables['da']['periods']
    if not (rt or da):
        st.info('这份归档缺少全流域分时数据。')
        return
    t=rt[quarter]['quarter'] if isinstance(quarter,int) and 0 <= quarter < len(rt) else quarter
    bystage={stage:{r['quarter']:r for r in data['periods']} for stage,data in tables.items()}
    selected=bystage['rt'].get(t) or bystage['da'].get(t)
    if not selected:
        st.info('所选时段没有归档数据。')
        return
    tag=str(result.get('run','dadu'))
    st.subheader('全流域总览')
    used='实时执行' if t in bystage['rt'] else '日前预出清'
    st.caption(f'{_time(t)}–{_time(t+1)} · {used}。全流域外部入水只计头水与区间天然来水，出口取末站总下泄。')
    cols=st.columns(4)
    for col,label,key,scale,unit in zip(cols,('水电出力','冻结可用上限','四库期末库存','河道期末在途'),
                  ('generation_mw','capacity_mw','four_storage_m3','transit_end_m3'),(1,1,1e6,1e6),
                  ('MW','MW','百万 m³','百万 m³')):
        col.metric(label,_fmt(_scaled(selected[key],scale))+' '+unit)
    opts={'水电出力与可用上限':('generation_mw','MW',1),
          '实发较日前偏差':('deviation_mw','MW',1),
          '四库库存':('four_storage_m3','百万 m³',1e6),
          '河道在途':('transit_end_m3','百万 m³',1e6),
          '天然来水':('inflow_m3','m³/s',900),
          '流域出口':('outlet_m3','m³/s',900),
          '各站弃水量之和':('spill_m3','百万 m³/段',1e6)}
    option=st.selectbox('全日对照指标',list(opts),key='basin_series_'+tag)
    key,unit,scale=opts[option]
    chart=[]
    for k in sorted(set(bystage['da']) | set(bystage['rt'])):
        row={'时段末':_time(k+1)}
        if key=='deviation_mw':
            scheduled=bystage['da'].get(k,{}).get('generation_mw')
            executed=bystage['rt'].get(k,{}).get('generation_mw')
            row['实发较日前偏差']=executed-scheduled if executed is not None and scheduled is not None else None
        else:
            for stage,label in (('da','日前'),('rt','实时')):
                row[label]=_scaled(bystage[stage].get(k,{}).get(key),scale)
        if key=='generation_mw':
            row['冻结可用上限']=bystage['da'].get(k,bystage['rt'].get(k,{})).get('capacity_mw')
        chart.append(row)
    time_chart(pd.DataFrame(chart).set_index('时段末'),unit,height=330)
    if key in ('generation_mw','spill_m3'):
        st.caption('可用上限取归档的分站设备边界，实际出力另受来水、库存、网络和报价约束。'
                   '各站弃水可流入下游再次利用，同一股水可能多次出现。')
    for stage,label in (('da','日前计划'),('rt','实时执行')):
        bill=basin_balance(tables[stage]['periods'])
        if bill:
            st.caption(f"{label} {bill['start']}–{bill['end']}（{bill['n']} 段）："
                       f"水电发电量 {_fmt(_scaled(bill['generation_mwh'],1000))} GWh，"
                       f"设备可用上限积分 {_fmt(_scaled(bill['capacity_mwh'],1000))} GWh，"
                       f"各站弃水量之和 {_fmt(_scaled(bill['spill_m3'],1e6))} 百万 m³。")
    snapshots=[]
    for stage,label in (('da','日前计划'),('rt','实时执行')):
        for period,subset in (('所选时段',[bystage[stage][t]] if t in bystage[stage] else []),
                              ('归档全段',tables[stage]['periods'])):
            b=basin_balance(subset)
            if not b: continue
            snapshots.append({'阶段':label,'范围':period+' '+b['start']+'–'+b['end'],
                **{title:_scaled(b[k],1e6) for k,title in (
                     ('storage_begin_m3','期初库内'),('river_begin_m3','期初在途'),('inflow_m3','天然入水'),
                     ('outlet_m3','出口下泄'),('storage_end_m3','期末库内'),('river_end_m3','期末在途'))},
                '残差 m³':b['residual_m3']})
    st.markdown('**全流域水量平衡**')
    st.caption('水量单位为百万 m³，残差以 m³ 显示。期初库内＋期初在途＋天然入水－出口下泄－期末库内－期末在途＝残差。'
               '过机及站间传水均在流域内部。日前在途按期初队列、计划下泄和河段时滞推算；实时取执行交接。')
    st.dataframe(pd.DataFrame(snapshots),hide_index=True)
    with st.expander('所选时段的厂站分布与河段在途'):
        locations={}
        rivers={}
        for stage,label in (('da','日前'),('rt','实时')):
            for s in tables[stage]['stations']:
                if s['quarter']!=t: continue
                site=locations.setdefault(s['id'],{'站号':s['id'],'厂站':s['name']})
                site['冻结可用 MW']=s['capacity_mw']
                site.update({label+col:_scaled(s[k],scale) for k,col,scale in (
                    ('generation_mw','出力 MW',1),('inflow_m3','区间来水 m³/s',900),
                    ('spill_m3','弃水 m³/s',900),('storage_end_m3','期末库存 百万m³',1e6))})
            for z in tables[stage]['reaches']:
                if z['quarter']!=t: continue
                pair=(z['upstream'],z['downstream'])
                reach=rivers.setdefault(pair,{'上游':pair[0],'下游':pair[1],'时滞/刻':z['lag_quarters']})
                reach[label+'期末在途 百万m³']=_scaled(z['transit_end_m3'],1e6)
        st.dataframe(pd.DataFrame(locations.values()),hide_index=True)
        if rivers:
            for row in rivers.values():
                row['上游']=locations.get(row['上游'],{}).get('厂站',row['上游'])
                row['下游']=locations.get(row['下游'],{}).get('厂站',row['下游'])
            st.dataframe(pd.DataFrame(rivers.values()),hide_index=True)
