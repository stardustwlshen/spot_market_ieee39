from mechanisms import need,digest
def build(q,names):
 ri={n:i for i,n in enumerate(names['constraints'])};vn=names['variables'];balances=[n for n in names['constraints']if n.startswith('balance[')];balance=[];accident=[];positive=[]
 periods=sorted({int(n.rsplit(',',1)[1][:-1]) for n in balances});need(periods==list(range(len(periods))) and 1<=len(periods)<=8,'contiguous1..8 period domain')
 for t in periods:
  rows={n:(1. if n.split('[',1)[1].split(',')[0].strip("'")=='39'else 0.)for n in balances if n.endswith(','+str(t)+']')};need(len(rows)==39,'complete39balance inventory');balance.append(dict(period=t,duration_h=.25,rows=rows))
  name=f'contingency_reserve[{t}]';r=q['rows'][ri[name]];terms=[(vn[j],c)for j,c in r['terms']]
  need(r['lb'] is not None and r['lb']>=0 and r['ub'] is None and all(c==1 for _,c in terms),'explicit contingency capacity row')
  need(all(s.startswith(('accident[','price_reserve.battery_up[','price_reserve.pumped_up[')) and s.endswith(','+str(t)+']') for s,_ in terms),'only thermal/battery/PS capacity')
  accident.append(dict(period=t,duration_h=.25,row=name,requirement_MW=r['lb'],qualified_columns=[s for s,_ in terms]));positive.append(f'positive_reserve[{t}]')
 need(all(n in ri for n in positive),'explicit positive capacity rows')
 flows=[n for n in vn if n.startswith('flow[')];m=dict(schema='gd.PRICE.slack_mapping.v1',base_template_sha256=digest(q),base_names_sha256=digest(names),balance=balance,balance_inventory=balances,accident=accident,lines=[dict(id=s[5:-1],duration_h=.25,column=s)for s in flows],line_column_inventory=flows,sections=[],section_row_inventory=[],positive_reserve_rows=positive)
 return m
