"""Source-bound PS initialization and first-executed-period carry, not window tail."""
from pathlib import Path
from copy import deepcopy
import json,hashlib,math

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def attach_initial(carry,da_case,manifest,out):
    out=Path(out);inv=da_case['battery_contract']['storage']['pumped_storage']['inventory'];sources=manifest['source_pins']
    choices=[p for p in sources if Path(p).name=='PUMPED_DECLARATIONS.json']
    if len(choices)!=1:raise ValueError('one source-bound DA pumped declaration required')
    src=Path(choices[0]);assert sha(src)==sources[str(src)],'DA pumped declaration changed'
    declarations=json.loads(src.read_bytes());ph=manifest['physical_solution'];assert sha(ph['path'])==ph['sha256'],'new DA physical changed';physical=json.loads(Path(ph['path']).read_bytes())
    if physical.get('grid_minutes')!=15 or len(physical['physical_cells'])!=96:raise ValueError('new DA 96-grid reference required')
    state={}
    for s,z in inv.items():
        d=declarations[s];d.update(stage='RT',band_m3=.02*z['parameters']['v_total']*10000,band_penalty_yuan_m3=1.0,rt_band_basis='research assumption: 2% total water, 1 yuan/m3 excess; editable, not official numeric parameter',da_upper_m3={t:row['pumped'][s]['upper_m3']for t,row in enumerate(physical['physical_cells'])},da_lower_m3={t:row['pumped'][s]['lower_m3']for t,row in enumerate(physical['physical_cells'])},DA_reference_source=ph)
        # t0 is declared actual boundary, NOT end of DA first or last interval.
        state[s]=dict(upper_m3=z['upper_m3'],lower_m3=z['lower_m3'],net_power_mw=z['net_power_mw'],units={u:dict(d['initial_units'][u],mode=mode)for u,mode in z['modes'].items()},boundary_day_tick=-1,initial_basis='declared_t0_not_DA_executed_state')
        # DA already accepts sourced active initial modes. Preserve those exact
        # t0 states, while rejecting missing/inconsistent per-unit power.
        from .numeric_boundary import check_state
        parameters=z['parameters'];net=0.
        for u,mode in z['modes'].items():
            if mode not in ('off','gen','pump'):
                raise ValueError('unknown initial pumped mode')
            unit=d['initial_units'][u]
            for key,bounds,scale in (
                ('gen_mw',(parameters['unit_gen_min'],parameters['unit_gen_max']) if mode=='gen' else (0.,0.),parameters['unit_gen_max']),
                ('pump_mw',(parameters['unit_pump'],parameters['unit_pump']) if mode=='pump' else (0.,0.),parameters['unit_pump'])):
                if key not in unit:raise ValueError('explicit per-unit initial pumped power required')
                check_state(unit[key],lower=bounds[0],upper=bounds[1],capacity_scale=scale,unit='MW')
            net+=unit['gen_mw']-unit['pump_mw']
        declared_net=z['net_power_mw']
        if not math.isfinite(declared_net) or abs(net-declared_net)>32*math.ulp(max(parameters['unit_gen_max'],parameters['unit_pump'])*parameters['n_units']):
            raise ValueError('initial pumped net power disagrees with unit powers')
    target=out/'PUMPED_RT_DECLARATIONS.json'
    with target.open('x')as f:json.dump(declarations,f,indent=2)
    carry.update(pumped_storage=state,pumped_storage_declaration_path=str(target.resolve()),pumped_storage_declaration_sha256=sha(target));carry['source_pins'].update({str(target.resolve()):sha(target),str(src):sha(src),ph['path']:ph['sha256'],str(Path(__file__).resolve()):sha(__file__)})
    return carry

def derive_pumped_carry(case,raw,names,first_tick=0):
    if first_tick!=0:raise ValueError('rolling handoff executes first local period only')
    if not raw.get('has_primal'):raise ValueError('SCED primal required')
    values=dict(zip(names['variables'],raw['primal']))
    if len(values)!=len(names['variables']) or len(raw['primal'])!=len(names['variables']):raise ValueError('complete named witness required')
    def val(key):
        z=float(values[key])
        if not math.isfinite(z):raise ValueError('finite pumped witness required')
        return z
    def bit(key):
        z=val(key)
        if abs(z-round(z))>1e-6:raise ValueError('integer pumped mode required')
        return int(round(z))
    inv=case['battery_contract']['storage']['pumped_storage']['inventory'];decl=case['pumped_storage_declarations'];out={}
    for s,r in inv.items():
        units={};net=0.;p=r['parameters']
        for u in r['modes']:
            gm=bit(f'pumped.gen_mode[{s},{u},0]');pm=bit(f'pumped.pump_mode[{s},{u},0]')
            if gm+pm>1:raise ValueError('pumped mode conflict')
            gen=val(f'pumped.gen[{s},{u},0]');pump=val(f'pumped.pump[{s},{u},0]');old=decl[s]['initial_units'][u];mode='gen'if gm else'pump'if pm else'off'
            units[u]=dict(mode=mode,mode_age_steps=old['mode_age_steps']+1 if mode==r['modes'][u] else 1,gen_mw=gen,pump_mw=pump,gen_starts_used=old['gen_starts_used']+bit(f'pumped.gen_start[{s},{u},0]'),pump_starts_used=old['pump_starts_used']+bit(f'pumped.pump_start[{s},{u},0]'),last_active_mode=mode if mode!='off'else old['last_active_mode'],opposite_mode_block_steps=p['switch_gap']if mode!='off'else max(0,old['opposite_mode_block_steps']-1));net+=gen-pump
        up=val(f'pumped.upper[{s},0]');low=val(f'pumped.lower[{s},0]')
        delta=.25*sum(10000/p['e_pump']*z['pump_mw']-10000/p['e_gen']*z['gen_mw']for z in units.values())
        if abs(up-r['upper_m3']-delta)>1e-5 or abs(low-r['lower_m3']+delta)>1e-5:raise ValueError('first-step pumped water balance mismatch')
        out[s]=dict(upper_m3=up,lower_m3=low,net_power_mw=net,units=units,executed_local_tick=0,source='accepted_SCED_first_15min_only')
    return out

