"""Reviewed quarter-hour operating instructions for the Dadu day-ahead experiment."""
from math import inf, isfinite


LABELS = {'offline':'不可用（检修等）','island':'特殊可用（孤网）','fixed':'固定并网出力',
          'minimum':'并网出力下限','maximum':'并网出力上限','release':'总下泄下限',
          'level_min':'段末水位下限','level_max':'段末水位上限'}
UNITS = {'offline':'','island':'MW','fixed':'MW','minimum':'MW','maximum':'MW',
         'release':'m³/s','level_min':'m','level_max':'m'}


def with_operations(base, entries):
    from sichuan_v5_dadu_run import _hash
    records = []
    for e in entries:
        if not isinstance(e,dict) or set(e) != {'station','first','last','kind','value'}:
            raise ValueError('分时申报字段无效')
        uid,first,last,kind,value = (e[k] for k in ('station','first','last','kind','value'))
        if uid not in base['offers'] or kind not in LABELS:
            raise ValueError('电站或运行要求无效')
        if type(first) != int or type(last) != int or not 0 <= first <= last < 96:
            raise ValueError('起止时段须位于同一运行日，结束时间须晚于开始时间')
        if type(value) not in (int,float) or not isfinite(value) or value < 0 or (kind=='offline' and value):
            raise ValueError('控制值无效')
        records.append(dict(station=uid,first=first,last=last,kind=kind,value=float(value)))
    records.sort(key=lambda r:(r['station'],r['first'],r['last'],r['kind'],r['value']))
    payload = dict(default_status='available',periods=96,stations=sorted(base['offers']),entries=records)
    anchor = base['declaration_sha256']
    return dict(base,base_declaration_sha256=anchor,operations=payload,
                declaration_sha256=_hash(dict(base_declaration_sha256=anchor,operations=payload)))


class DayOperations:
    def __init__(self, company, stations):
        self.slots = {}
        catalog = {s['id']:s for s in stations}
        lakes = {r['id']:r for r in company['hydraulic_declaration']['reservoirs']}
        for r in company.get('operations',{}).get('entries',[]):
            uid,kind,value = r['station'],r['kind'],r['value']
            station,lake = catalog[uid],lakes[uid]
            for t in range(r['first'],r['last']+1):
                slot = self.slots.setdefault((uid,t),{})
                if kind in slot:
                    raise ValueError(f"{station['name']} 第 {t+1} 段重复填报{LABELS[kind]}")
                slot[kind] = value
                if kind.startswith('level_'):
                    if station['model'] != 'reservoir':
                        raise ValueError(f"{station['name']} 按零调节库容建模，不能受理水位要求")
                    dead,normal = float(station['reference_dead_level_m']),float(station['reference_normal_level_m'])
                    volume = station['storage_m3']*(value-dead)/(normal-dead)
                    if not lake['storage_min_m3'] <= volume <= lake['storage_max_m3']:
                        raise ValueError(f"{station['name']} 第 {t+1} 段水位超出本水期模拟库容范围")
                    slot[kind+'_m3'] = volume
                if kind == 'release' and lake['max_release_m3s'] is not None and value > lake['max_release_m3s']:
                    raise ValueError(f"{station['name']} 总下泄要求超过登记上限")
        for (uid,t),s in self.slots.items():
            if ('offline' in s or 'island' in s) and len(s.keys() & {'offline','island','fixed','minimum','maximum'}) > 1:
                raise ValueError(f'{uid} 第 {t+1} 段工况与并网出力要求冲突')
            if s.get('level_min_m3',0) > s.get('level_max_m3',inf):
                raise ValueError(f'{uid} 第 {t+1} 段水位上下限冲突')
            offer = company['offers'][uid]
            if self.island(uid,t) > offer['technical_available_mw'][t]:
                raise ValueError(f'{uid} 第 {t+1} 段孤网计划超过技术可用容量')
            self.grid_bounds(uid,t,offer['minimum_mw'][t],min(offer['available_mw'][t],offer['technical_available_mw'][t]))

    def grid_bounds(self,uid,t,low,high):
        s = self.slots.get((uid,t),{})
        if 'offline' in s or 'island' in s:
            return 0.,0.
        lo = max(low,s.get('minimum',low),s.get('fixed',low))
        hi = min(high,s.get('maximum',high),s.get('fixed',high))
        if lo > hi:
            raise ValueError(f'{uid} 第 {t+1} 段要求与并网能力无交集（{lo:g} > {hi:g} MW）')
        return lo,hi

    def island(self,uid,t):
        return self.slots.get((uid,t),{}).get('island',0.)

    def attach_water(self,model,constraints):
        for (uid,t),s in self.slots.items():
            if 'release' in s:
                constraints.add(model.cascade_release[uid,t] >= s['release'])
            if 'level_min_m3' in s:
                constraints.add(model.cascade_volume[uid,t] >= s['level_min_m3'])
            if 'level_max_m3' in s:
                constraints.add(model.cascade_volume[uid,t] <= s['level_max_m3'])
