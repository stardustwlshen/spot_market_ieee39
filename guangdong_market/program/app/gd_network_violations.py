"""Source-derived violations; solver optimality is not a network-security certificate."""
import re

def summarize(names,response,executed_periods=96,tolerance=1e-6):
    rows=[]
    for name,value in zip(names['variables'],response['primal']):
        m=re.fullmatch(r'(line_over|line_under|section_over|section_under)\[(.+),(\d+)\]',name)
        if not m or value<=tolerance:continue
        kind,resource,t=m.groups();t=int(t)
        rows.append(dict(resource_type='line' if kind.startswith('line') else 'section',resource=resource.strip("'"),direction='upper' if kind.endswith('over') else 'lower',tick=t,excess_mw=float(value),role='delivery' if t<executed_periods else 'representative'))
    return dict(schema='gd.network_violation_summary.v1',has_violation=bool(rows),network_secure=not rows,maximum_excess_mw=max((x['excess_mw'] for x in rows),default=0.),delivery_violation_count=sum(x['role']=='delivery' for x in rows),representative_violation_count=sum(x['role']=='representative' for x in rows),violations=rows,notice='存在线路或断面越限；已按原罚值计入目标，求解成功不代表无越限。' if rows else '未发现线路或断面越限。')
