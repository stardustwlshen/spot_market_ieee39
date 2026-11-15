"""Export actual SCED primal on its native 15min grid, without invented prices."""
from datetime import datetime,timedelta
import math
import pyomo.environ as pyo
from .pumped_storage import executed_state

def export_physical(m,origin):
    start=datetime.fromisoformat(origin)
    if start.utcoffset() is None:raise ValueError('timezone-aware origin required')
    ts=tuple(m.T)
    if ts not in (tuple(range(96)),tuple(range(98))):raise ValueError('DA96 or DA98 grid required')
    inp=m._r23['inputs'];agc=inp['agc_awards'];thermal=inp['energy_inputs']['thermal'];cells=[];representative=[]
    def val(v):
        x=float(pyo.value(v))
        if not math.isfinite(x):raise ValueError('finite solved primal required')
        return x
    def binary(v):
        x=val(v)
        if abs(x-round(x))>1e-6:raise ValueError('integer SCED fixed witness required')
        return int(round(x))
    for t in ts:
        therm={}
        for g in m.G:
            phase='stable' if binary(m.stable[g,t]) else 'off' if binary(m.off[g,t]) else None
            if phase is None:
                for kind,var in (('startup',m.start),('shutdown',m.stop)):
                    if any(binary(var[g,j]) for j in range(max(0,t-len(inp['curves'][g][kind])+1),t+1)):phase=kind
                if phase is None:phase=m._r23['boundary_states'][g]['phase']
            therm[g]=dict(phase=phase,mean_mw=val(m.power[g,t]),end_mw=val(m.end_power[g,t]),connected=binary(m.online[g,t]),stable=binary(m.stable[g,t]),start_event=binary(m.start[g,t]),stop_event=binary(m.stop[g,t]),agc_up_reserved_mw=agc[g]['up_mw'][t],agc_down_reserved_mw=agc[g]['down_mw'][t])
        batteries={}
        if hasattr(m,'battery'):
            b=m.battery
            for u in b.BAT:
                r=m._battery_inputs[u];mode='charge' if binary(b.charge_mode[u,t]) else 'discharge' if binary(b.discharge_mode[u,t]) else 'idle'
                batteries[u]=dict(mode=mode,charge_mw=val(b.charge[u,t]),discharge_mw=val(b.discharge[u,t]),power_mw=val(b.power[u,t]),energy_begin_mwh=val(b.energy_begin[u,t]),energy_end_mwh=val(b.energy[u,t]),soc_end=val(b.energy[u,t])/r['energy_capacity_mwh'])
        pumped=executed_state(m,t) if hasattr(m,'pumped') else {}
        if hasattr(m,'extra_reserve'):
            for u,z in batteries.items():z.update(reserve_up_mw=val(m.extra_reserve.battery_up[u,t]),primary_mw=0.)
            for station,z in pumped.items():z.update(reserve_up_mw=val(m.extra_reserve.pumped_up[station,t]),primary_mw=val(m.extra_reserve.pumped_primary[station,t]))
        renewable={str(bus):dict(dispatched_mw=val(m.renewable_power[bus,t])) for bus in m.B}
        supply=sum(z['mean_mw']for z in therm.values())+sum(z['power_mw']for z in batteries.values())+sum(z['net_power_mw']for z in pumped.values())+sum(z['dispatched_mw']for z in renewable.values())
        demand=sum(val(m.demand_mw[bus,t])for bus in m.B);tie=sum(val(m.physical_tie[bus,t])for bus in m.B)
        if abs(supply+tie-demand)>1e-5:raise ValueError('exported net injections do not balance')
        row=dict(tick=t,duration_h=.25,thermal=therm,battery=batteries,pumped=pumped,renewable=renewable,pumped_storage_power_mw=sum(z['net_power_mw']for z in pumped.values()),load_mw=demand,fixed_tie_mw=tie,balance_residual_mw=supply+tie-demand)
        if t<96:
            at=start+timedelta(minutes=15*t);row.update(start=at.isoformat(),end=(at+timedelta(minutes=15)).isoformat());cells.append(row)
        else:
            row.update(index_is_compressed_not_chronological=True,settlement_included=False,carry_included=False,weight_basis='0.25h research objective/dynamic weight; not elapsed physical time');representative.append(row)
    return dict(schema='gd.provincial.da_physical.v1',grid_minutes=15,model_periods=len(ts),physical_cells=cells,representative_cells=representative,terminal_pumped=cells[-1]['pumped'],native_output='fixed_integer_SCED_primal',price=None,formal_market_baseline_complete=False,reference_role='new_solved_DA_reservoir_and_SOC_trajectory_not_historical_off_inventory')
