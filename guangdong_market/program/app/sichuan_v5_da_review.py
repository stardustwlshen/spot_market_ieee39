"""Day-ahead comparisons and station water diagnostics in physical units."""
from math import fsum


def system_quarters(rows, assets, lines):
    groups = {kind:[a['id'] for a in assets if a['kind']==kind]
              for kind in ('hydro','thermal','fixed','renewable','storage')}
    result = []
    for row in rows:
        demand = row['demand']
        prices = row['raw_nodal_prices']
        load = fsum(demand.values())
        weighted = fsum(p*prices[b] for b,p in demand.items())/load if load else None
        pump = fsum(row['charge'].values())
        gen = {k:fsum(row['power'][u] for u in ids) for k,ids in groups.items()}
        pv_cut = fsum(row['curtailed_mw'].values())
        spread = max(prices.values())-min(prices.values())
        limit = max(abs(row['flows'][l['id']])/l['limit_mw'] for l in lines)
        result.append(dict(load=load,pump=pump,**gen,pv_cut=pv_cut,weighted_price=weighted,
                           price_spread=spread,max_line_pu=limit,online=sum(row['online'].values()),
                           reserve_short=row['reliability_reserve_short_mw'],shed=fsum(row['shed'].values()),
                           excess=fsum(row['spill'].values()),
                           station_spill_m3=900*fsum(w['water_spill_m3s'] for w in row['hydro'].values()),
                           residual_load=load+pump-gen['storage']-gen['fixed']-gen['renewable']-pv_cut))
    return result


def daily_metrics(rows):
    total = lambda key: fsum(r[key] for r in rows)
    load = total('load')
    return dict(hydro_mwh=.25*total('hydro'),coal_mwh=.25*total('thermal'),
                pv_cut_mwh=.25*total('pv_cut'),station_spill_m3=total('station_spill_m3'),
                residual_peak_mw=max(r['residual_load'] for r in rows),
                pump_mwh=.25*total('pump'),generation_mwh=.25*total('storage'),
                weighted_price=fsum(r['load']*r['weighted_price'] for r in rows if r['load'])/load if load else None,
                shed_mwh=.25*total('shed'),reserve_short_mwh=.25*total('reserve_short'),
                tight_quarters=sum(r['max_line_pu']>=1.-1e-6 for r in rows))


def spill_budget(*, inflow, start_m3, upper_m3, minimum_release, bypass, maximum_turbine, spill):
    """Conditional lower bound holding incoming water and starting stock fixed."""
    required = max(minimum_release,inflow+(start_m3-upper_m3)/900.)
    mandatory = max(0.,required-bypass-maximum_turbine)
    if mandatory > spill + 1e-4:
        raise ValueError('本站条件弃水下界超过出清弃水，请检查水量或功率单位')
    return min(mandatory,spill),max(0.,spill-mandatory)


def station_quarters(result, station):
    uid = station['id']
    company = result['company']
    offer = company['offers'][uid]
    reservoir = next(r for r in company['hydraulic_declaration']['reservoirs'] if r['id']==uid)
    start = reservoir['initial_storage_m3']
    entries = [e for e in company.get('operations',{}).get('entries',[]) if e['station']==uid]
    output = []
    for t,row in enumerate(result['da']):
        water = row['hydro'][uid]
        low,high = result['bounds'][t][uid]
        slot = {e['kind']:e['value'] for e in entries if e['first']<=t<=e['last']}
        island = slot.get('island',0.)
        up,down = row.get('ancillary_holds_mw',{}).get(uid,(0.,0.))
        ceiling = reservoir['storage_max_m3']
        if t==95 and reservoir.get('terminal_max_m3') is not None:
            ceiling = min(ceiling,reservoir['terminal_max_m3'])
        if 'level_max' in slot:
            dead = float(station['reference_dead_level_m'])
            normal = float(station['reference_normal_level_m'])
            ceiling = min(ceiling,station['storage_m3']*(slot['level_max']-dead)/(normal-dead))
        inflow = water['natural_inflow_m3s']+water['upstream_arrival_m3']/900.
        minimum = max(reservoir['min_release_m3s'],slot.get('release',0.))
        floor,rest = spill_budget(inflow=inflow,start_m3=start,upper_m3=ceiling,minimum_release=minimum,
                                  bypass=water['eco_bypass_m3s'],maximum_turbine=(high-up+island)*reservoir['generation_m3s_per_mw'],
                                  spill=water['water_spill_m3s'])
        prices = row['raw_nodal_prices']
        bus = station['bus']
        output.append(dict(quarter=t,initial_m3=start,storage_m3=water['storage_m3'],forecast=offer['natural_inflow_m3s'][t],
                           local=water['natural_inflow_m3s'],arrival=water['upstream_arrival_m3']/900.,
                           turbine=water['turbine_m3s'],bypass=water['eco_bypass_m3s'],spill=water['water_spill_m3s'],
                           floor=floor,remaining=rest,declared_low=offer['minimum_mw'][t],declared_high=offer['available_mw'][t],
                           approved_low=low+down,approved_high=high-up,scuc=result['scuc'][t]['power'][uid],
                           sced=row['power'][uid],island=island,price=prices.get(str(bus),prices.get(bus)),
                           accepted=row.get('accepted_segments_mw',{}).get(uid)))
        start = water['storage_m3']
    return output
