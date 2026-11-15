"""Source-pinned research bid defaults and explicit new-day pumped declarations."""
from pathlib import Path
import ast,hashlib,math

def day_ahead(inventory, *, bid_source):
    """New simulation day starts from the case's declared state, not a resumed run.
    Original project pumped defaults are assumptions, never official numeric bids.
    """
    path=Path(bid_source).resolve();body=path.read_bytes();tree=ast.parse(body);values={}
    for node in tree.body:
        if isinstance(node,ast.Assign):
            for target in node.targets:
                if isinstance(target,ast.Name) and target.id in ('PUMPED_GEN_PRICES','PUMPED_PUMP_PRICES'):values[target.id]=ast.literal_eval(node.value)
    gen=values['PUMPED_GEN_PRICES'];pump=values['PUMPED_PUMP_PRICES']
    if len(pump)!=1 or not gen:raise ValueError('supported source defaults require single pump bid, nonempty generation curve')
    source=dict(path=str(path),sha256=hashlib.sha256(body).hexdigest(),classification='existing_project_research_bid_defaults_not_official_parameters')
    result={}
    for s,z in inventory.items():
        p=z['parameters'];mode=z['modes']
        if any(v!='off' for v in mode.values()):raise ValueError('non-off day initial state needs explicit carried startup/switch counters')
        terminal=p.get('vup_end_bid')
        if terminal is None:raise ValueError('explicit DA upper reservoir terminal declaration required')
        result[s]=dict(stage='DA',basis='research',source=source,gen_bid_yuan_mwh=gen[0],pump_bid_yuan_mwh=pump[0],gen_segments=[dict(width_mw=p['n_units']*p['unit_gen_max']/len(gen),price_yuan_mwh=v)for v in gen],band_m3=0,band_penalty_yuan_m3=1,terminal_upper_m3=terminal*10000,terminal_lower_m3=(p['v_total']-terminal)*10000,initial_units={u:dict(gen_starts_used=0,pump_starts_used=0,last_active_mode='off',opposite_mode_block_steps=0,mode_age_steps=max(1,math.ceil(p.get('min_off_time_h',.25)/.25)),gen_mw=0.,pump_mw=0.)for u in mode},initial_state_basis='research assumption: new-day idle already satisfies minimum off duration; not inferred resumed history')
    return result


def realtime(inventory, da_declarations, *, physical_path, expected_sha256, first_tick, count, boundary, boundary_day_tick, band_m3, band_penalty_yuan_m3):
    """Use NEW solved DA references and actual executed incoming state, never tail plans.
    band and penalty are caller-declared research parameters, not official defaults.
    Returns updated inventory + RT declarations; caller rebinds contract source bytes.
    """
    import copy,json,math
    path=Path(physical_path).resolve();body=path.read_bytes()
    if hashlib.sha256(body).hexdigest()!=expected_sha256:raise ValueError('DA physical source changed')
    physical=json.loads(body)
    if physical.get('schema')!='gd.provincial.da_physical.v1' or physical.get('grid_minutes')!=15 or len(physical['physical_cells'])!=96:raise ValueError('new solved provincial DA physical reference required')
    if type(first_tick)is not int or type(count)is not int or not 0<=first_tick<96 or count<=0 or first_tick+count>96:raise ValueError('valid same-day reference slice required')
    if set(boundary)!=set(inventory) or set(da_declarations)!=set(inventory):raise ValueError('all station actual boundary states required')
    if not all(math.isfinite(x) for x in (band_m3,band_penalty_yuan_m3)) or band_m3<0 or band_penalty_yuan_m3<=0:raise ValueError('explicit finite band/penalty required')
    if type(boundary_day_tick)is not int or boundary_day_tick!=first_tick-1:raise ValueError('boundary must end immediately before RT reference slice')
    inv=copy.deepcopy(inventory);decl=copy.deepcopy(da_declarations)
    for s in inv:
        state=boundary[s];inv[s]['upper_m3']=state['upper_m3'];inv[s]['lower_m3']=state['lower_m3'];inv[s]['net_power_mw']=state['net_power_mw']
        inv[s]['modes']={u:z['mode'] for u,z in state['units'].items()}
        d=decl[s];d.update(stage='RT',band_m3=band_m3,band_penalty_yuan_m3=band_penalty_yuan_m3,initial_units={u:{k:z[k] for k in ('gen_starts_used','pump_starts_used','last_active_mode','opposite_mode_block_steps','mode_age_steps','gen_mw','pump_mw')}for u,z in state['units'].items()},da_upper_m3={},da_lower_m3={})
        d['DA_reference_source']=dict(path=str(path),sha256=expected_sha256,first_day_tick=first_tick,count=count,kind='new_actual_DA_SCED_primal_reference')
        for local in range(count):
            cell=physical['physical_cells'][first_tick+local]
            if cell['tick']!=first_tick+local:raise ValueError('DA physical time mapping mismatch')
            d['da_upper_m3'][local]=cell['pumped'][s]['upper_m3'];d['da_lower_m3'][local]=cell['pumped'][s]['lower_m3']
    return inv,decl
