"""Quarter-hour plots with a continuous time axis for 4- and 96-slot replays."""
import pandas as pd
import streamlit as st


def time_chart(frame, unit, *, area=False, height=255):
    """Accept a wide frame indexed by the interval end in local HH:MM."""
    if frame.empty:
        return
    records = []
    times = []
    for stamp in frame.index:
        h, m = map(int, str(stamp).split(':'))
        times.append(60*h + m)
    for label in frame.columns:
        segment = 0
        for minute, stamp, value in zip(times, frame.index, frame[label]):
            if pd.isna(value):
                segment += 1
                continue
            records.append({'minute':minute, '时段末':str(stamp), '系列':label,
                            '数值':float(value), 'segment':segment})
    if not records:
        st.caption('所选指标在该范围内暂无有效数值。')
        return
    step = next(s for s in (15,30,60,120,240,360,1440) if max(times)/s<=8)
    ticks = list(range(0, max(times)+1, step))
    if ticks[-1] != max(times):
        ticks.append(max(times))
    axis = {
        'title':'时段末（北京时间）','values':ticks,'labelAngle':0,'labelOverlap':'greedy',
        'labelExpr':"format(floor(datum.value/60),'02d') + ':' + format(datum.value%60,'02d')",
    }
    y = {'field':'数值','type':'quantitative','title':unit}
    if area:
        y['stack'] = 'zero'
    spec = {
        'height':height,
        'mark':{'type':'area' if area else 'line','clip':True},
        'encoding':{
            'x':{'field':'minute','type':'quantitative','scale':{'domain':[0,max(times)],'nice':False},'axis':axis},
            'y':y, 'color':{'field':'系列','type':'nominal','title':None,
                            'sort':list(frame.columns),
                            'scale':{'range':['#1769aa','#bd510c','#167858','#754c9b','#844754','#56616e']},
                            'legend':{'orient':'bottom','columns':2}},
            'detail':{'field':'segment','type':'nominal'},
            'tooltip':[{'field':'时段末','type':'nominal'}, {'field':'系列','type':'nominal'},
                       {'field':'数值','type':'quantitative','title':unit,'format':',.2f'}],
        },
    }
    st.vega_lite_chart(pd.DataFrame(records), spec, width='stretch')
