"""Explicit resource technology declarations; legacy seed mapping is not ID inference."""
LEGACY={'C1':'coal','C2':'coal','C3':'coal','CCGT':'gas','GT':'gas'}
def thermal_types(case):
 units=case['energy_inputs']['thermal'];reg=case.get('resource_registry')
 if reg is None:
  if set(units)!=set(LEGACY):raise ValueError('新增火电须提供完整 resource_registry 类型声明')
  return dict(LEGACY)
 if reg.get('schema')!='gd.resource_registry.v1':raise ValueError('资源类型声明版本错误')
 rows=reg.get('thermal',{})
 if set(rows)!=set(units):raise ValueError('资源类型声明与机组清单不一致')
 for g,z in rows.items():
  if z.get('technology')not in ('coal','gas') or str(z.get('bus'))!=str(units[g]['bus']) or not z.get('source'):raise ValueError('机组类型/节点/来源声明无效：'+g)
 return {g:z['technology']for g,z in rows.items()}
def initialize_registry(case):
 types=thermal_types(case)
 case['resource_registry']={'schema':'gd.resource_registry.v1','thermal':{g:{'technology':t,'bus':str(case['energy_inputs']['thermal'][g]['bus']),'source':'original five-unit synthetic declaration' if 'resource_registry'not in case else case['resource_registry']['thermal'][g]['source']}for g,t in types.items()}}
 return case['resource_registry']
