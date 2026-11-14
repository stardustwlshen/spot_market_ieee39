"""Re-derive thermal source events/curves without project compilation helpers."""
from datetime import timedelta
from hashlib import sha256
from r17_source_oracle import rd,unpack,canonical
from independent_chain import require,equal,stamp

def check(source,contract,core):
    historical=unpack(rd(core/'input/historical_da_only/issued_day_research_plan.json')['value'])
    expected=[];checked=0
    for unit,path_id in historical['selected_paths'].items():
        selected=[x for x in source.source['candidates'][unit] if x['id']==path_id]
        require(len(selected)==1,'unique original fixed thermal path')
        for event in selected[0]['events']:
            effective=source.start+timedelta(minutes=5*event['at'])
            saved=source.source['contract']['sources'][event['source_id']]
            require(sha256(saved['artifact']).hexdigest()==saved['sha256'],'original curve source bytes')
            record=dict(unit=unit,kind=event['kind'],effective_at=effective.isoformat(),notification_at=event['notification_at'],
                        available_at=contract['publication_at'],curve_steps=[list(s) for s in event['steps']],source_sha256=saved['sha256'],research_schedule=True,actual_command_history=False)
            expected.append(record)
            if event['kind']=='startup':
                require(stamp(contract['publication_at'])<=stamp(event['notification_at'])<=effective-timedelta(minutes=30),'original planned thirty-minute notice')
            for k,step in enumerate(event['steps']):
                j=event['at']+k;row=contract['physical_cells'][j]['thermal'][unit];physical=source.x['resources'][unit]['rows'][j]
                require(row['phase']==physical['phase']==event['kind'] and row['curve_step']==k and row['curve_length']==len(event['steps']),'original exact curve cursor')
                for position,key in enumerate(('begin_mw','mean_mw','end_mw')):
                    equal(row[key],step[position],'original event '+key);equal(physical[key],step[position],'fixed physical event '+key)
                equal(row['mean_mw'],(row['begin_mw']+row['end_mw'])/2,'exact linear transition mean')
                checked+=1
    require(canonical(expected)==canonical(contract['events']),'complete original event notice contract')
    return dict(events=len(expected),transition_cells=checked,original_source_sha256s=sorted({z['source_sha256'] for z in expected}),
                planned_notice_not_actual_command_history=True,events_rederived=expected)
