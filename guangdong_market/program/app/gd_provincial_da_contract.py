"""New provincial DA raw evidence contract; not a legacy acceptance impersonator."""
from pathlib import Path
import hashlib,json,gzip

def read(path):
    p=Path(path);b=p.read_bytes()
    return json.loads(gzip.decompress(b) if p.suffix=='.gz' else b)

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def representative_contract(mapping):
    if not isinstance(mapping,list) or len(mapping)!=2 or {r.get('tick')for r in mapping}!={96,97}:raise ValueError('two explicit non-settled representative indices required')
    if {r.get('kind')for r in mapping}!={'peak','valley'}:raise ValueError('peak and valley representatives required')
    for r in mapping:
        if r.get('role')!='representative' or r.get('settle') is not False or r.get('objective_duration_h')!=.25:raise ValueError('representative accounting metadata invalid')
        if not isinstance(r.get('source_day_quarter'),int) or not 0<=r['source_day_quarter']<96:raise ValueError('representative source must be actual-day quarter')
        if not r.get('state_link_basis') or not r.get('weight_basis'):raise ValueError('compressed representative state/weight basis required')
    return mapping

def verify(path, *, require_price=True):
    path=Path(path).resolve();z=read(path)
    if z.get('schema')!='gd.provincial.da_raw.v1':raise ValueError('explicit provincial DA evidence schema required')
    if z.get('grid_minutes')!=15 or z.get('executed_periods')!=96 or not z.get('run_id'):
        raise ValueError('new DA identity must declare 96 x 15-minute cells')
    model_periods=z.get('model_periods',96)
    if model_periods not in (96,98):raise ValueError('unsupported DA model horizon')
    if model_periods==98:representative_contract(z.get('representative_mapping'))
    pins=z.get('source_pins')
    if not pins:raise ValueError('source-bound DA evidence required')
    for f,h in pins.items():
        if digest(f)!=h:raise ValueError('DA source changed: '+f)
    def file(rec):
        p=Path(rec['path']).resolve()
        if digest(p)!=rec['sha256']:raise ValueError('DA artifact changed: '+str(p))
        return p
    for name in ('SCUC','SCED'):
        st=z['stages'][name]
        for field in ('request','response','names'):file(st[field])
        r=read(file(st['response']))
        if st.get('status')!='accepted' or r.get('status')!='optimal':raise ValueError('DA stage not accepted: '+name)
        if name=='SCED' and r.get('dual_valid') is not True:raise ValueError('SCED native dual missing')
        if name=='SCED' and st.get('KKT',{}).get('passed') is not True:raise ValueError('SCED KKT acceptance required')
    physical=read(file(z['physical_solution']))
    if physical.get('grid_minutes')!=15 or len(physical.get('physical_cells',[]))!=96:
        raise ValueError('DA physical grid differs from accepted stage')
    if require_price and ('PRICE' not in z['stages'] or z['stages']['PRICE'].get('status')!='accepted'):
        raise ValueError('new provincial DA PRICE missing; raw dispatch is not a complete market baseline')
    if require_price:
        st=z['stages']['PRICE']
        for field in ('request','response','names','raw_prices'):file(st[field])
        if st.get('KKT',{}).get('passed') is not True:raise ValueError('DA PRICE KKT required')
    return dict(run_id=z['run_id'],grid_minutes=15,physical_cells=96,market_baseline_complete=require_price,
                evidence_path=str(path),evidence_sha256=digest(path),legacy_seed_compatible=False,
                original_stage_statuses_preserved=True)

def verify_da(directory, *, require_price=False):
    """Verify new native DA dispatch evidence; no legacy ARTIFACTS claim.

    PRICE completeness is a separate explicit condition. Initialization may bind
    the verified DA physical plan, but cannot settle without its accepted LMP.
    """
    import importlib.util,math
    root=Path(directory).resolve();source=read(root/'SOURCE.json')
    periods=source.get('periods',96)
    if periods not in (96,98):raise ValueError('unsupported native DA periods')
    if periods==98:
        representative_contract(source.get('representative_mapping'))
        if source.get('executed_periods')!=96:raise ValueError('representatives cannot enter execution')
    original_pins={};relocations=[]
    proof_path=root/'REPRODUCED_MODEL.json';reproduced=None
    if proof_path.exists():
        reproduced=read(proof_path);rq=Path(reproduced['request_path'])
        if digest(rq)!=reproduced['request_sha256'] or digest(root/'SCUC_request.json.gz')!=reproduced['original_request_sha256']:raise ValueError('reproduced model binding changed')
        original=read(root/'SCUC_request.json.gz');fresh=read(rq)
        if any(original[k]!=fresh[k]for k in ('variables','rows','objective')):raise ValueError('reproduced native model differs')
        for f,h in reproduced['pins'].items():
            if digest(f)!=h:raise ValueError('reproduced source snapshot changed')
        original_pins.update(reproduced['pins']);original_pins[str(proof_path)]=digest(proof_path);original_pins[str(rq)]=digest(rq)

    snapshot_map={}
    snapshot_index=root/'SOURCE_SNAPSHOT.json'
    if snapshot_index.exists():
        for rec in read(snapshot_index):
            if digest(rec['snapshot_path'])!=rec['sha256']:raise ValueError('construction snapshot changed')
            snapshot_map[rec['original_path']]=rec
        original_pins[str(snapshot_index)]=digest(snapshot_index)
    for f,h in source['source_pins'].items():
        if f in snapshot_map and snapshot_map[f]['sha256']==h:
            rec=snapshot_map[f];original_pins[rec['snapshot_path']]=h;relocations.append(dict(original_path=f,original_sha256=h,archived_path=rec['snapshot_path'],meaning='exact construction source snapshot'))
        elif digest(f)==h:original_pins[f]=h
        else:
            saved=root/'source_snapshot'/Path(f).name
            if saved.is_file() and digest(saved)==h:
                original_pins[str(saved)]=h;relocations.append(dict(original_path=f,original_sha256=h,archived_path=str(saved),meaning='exact construction-time source preserved; active file later changed'))
            elif reproduced is not None:
                relocations.append(dict(original_path=f,original_sha256=h,original_source_recovered=False,meaning='current snapshot independently reproduced identical native variables/rows/objective; original SOURCE retained'))
            else:raise ValueError('DA build source changed and original bytes unavailable: '+f)
    names=read(root/'names.json');raw={}
    pins={str(root/'SOURCE.json'):digest(root/'SOURCE.json'),**original_pins}
    for stage in ('SCUC','SCED'):
        qp=root/(stage+'_request.json.gz');rp=root/(stage+'_response.json.gz');q=read(qp);r=read(rp)
        if not r.get('has_primal') or r.get('status')!='optimal':raise ValueError('DA native stage not optimal: '+stage)
        x=r['primal']
        if len(x)!=len(q['variables']) or len(x)!=len(names['variables']):raise ValueError('DA primal dimension mismatch')
        residual=0.
        for v,a in zip(q['variables'],x):
            if not math.isfinite(a):raise ValueError('nonfinite DA primal')
            if v['lb'] is not None:residual=max(residual,v['lb']-a)
            if v['ub'] is not None:residual=max(residual,a-v['ub'])
            if v['kind']!='C' and abs(a-round(a))>1e-6:raise ValueError('DA integer mismatch')
        for row in q['rows']:
            value=math.fsum(coef*x[i]for i,coef in row['terms'])
            if row['lb'] is not None:residual=max(residual,row['lb']-value)
            if row['ub'] is not None:residual=max(residual,value-row['ub'])
        if residual>1e-6:raise ValueError('DA primal residual too large')
        raw[stage]=(q,r,residual);pins.update({str(qp):digest(qp),str(rp):digest(rp)})
    q,r,_=raw['SCED'];uq,ur,_=raw['SCUC']
    if r.get('dual_valid') is not True or any(v['kind']!='C'for v in q['variables']):raise ValueError('continuous DA SCED dual required')
    for u,v,x in zip(uq['variables'],q['variables'],ur['primal']):
        if u['kind']!='C' and not(v['lb']==v['ub']==round(x)):raise ValueError('SCED not fixed to accepted SCUC integer')
    kfile=Path(__file__).parent/'gd_provincial_kkt.py'
    spec=importlib.util.spec_from_file_location('_prov_da_kkt',kfile);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    kkt=mod.metrics(q,r,names,angle_tolerance=1e-7)
    if not kkt['passed']:raise ValueError('DA SCED KKT failed')
    phys=read(root/'physical_solution.json')
    if phys.get('grid_minutes')!=15 or len(phys['physical_cells'])!=96:raise ValueError('DA physical grid mismatch')
    if periods==98:
        reps=phys.get('representative_cells',[])
        if phys.get('model_periods')!=98 or {c.get('tick')for c in reps}!={96,97}:raise ValueError('explicit separated representative output required')
        for c in reps:
            if c.get('settlement_included')is not False or c.get('carry_included')is not False or c.get('index_is_compressed_not_chronological')is not True:raise ValueError('representative must not enter delivery/carry')
    values=dict(zip(names['variables'],r['primal']))
    for t,cell in enumerate(phys['physical_cells']):
        if cell['tick']!=t or cell['duration_h']!=.25:raise ValueError('DA physical clock mismatch')
        for g,z in cell['thermal'].items():
            if abs(z['mean_mw']-values[f'power[{g},{t}]'])>1e-7:raise ValueError('DA physical thermal not raw SCED')
        for g,z in cell['battery'].items():
            if abs(z['energy_end_mwh']-values[f'battery.energy[{g},{t}]'])>1e-7:raise ValueError('DA physical SOC not raw SCED')
        for station,z in cell.get('pumped',{}).items():
            for field,var in [('upper_m3','upper'),('lower_m3','lower')]:
                if abs(z[field]-values[f'pumped.{var}[{station},{t}]'])>1e-6:raise ValueError('DA pumped state not raw SCED')
            actual_net=sum(values[f'pumped.gen[{station},{u},{t}]']-values[f'pumped.pump[{station},{u},{t}]']for u in z['units'])
            if abs(actual_net-z['net_power_mw'])>1e-7:raise ValueError('DA pumped net power not raw SCED')
            for unit,u in z['units'].items():
                for field,var in [('gen_mw','gen'),('pump_mw','pump')]:
                    if abs(u[field]-values[f'pumped.{var}[{station},{unit},{t}]'])>1e-7:raise ValueError('DA pumped unit not raw SCED')
    for f in [root/'case.json',root/'names.json',root/'physical_solution.json',root/'agc.json',root/'PUMPED_DECLARATIONS.json',kfile,Path(__file__)]:pins[str(f.resolve())]=digest(f)
    price=root/'DA_PRICE_ACCEPTED.json'
    if require_price and not price.exists():raise ValueError('independent DA LMP not accepted; market baseline incomplete')
    return dict(schema='gd.provincial.da_verified.v1',run_id='provincial-da-'+digest(root/'SCED_response.json.gz')[:24],source_pins=pins,case=str(root/'case.json'),names=str(root/'names.json'),SCED_response=str(root/'SCED_response.json.gz'),physical_solution=str(root/'physical_solution.json'),grid_minutes=15,periods=periods,executed_periods=96,representative_mapping=source.get('representative_mapping',[]),max_primal_violation=max(v[2]for v in raw.values()),SCED_KKT=kkt,dispatch_verified=True,price_present=price.exists(),market_baseline_complete=False,legacy_acceptance_claimed=False,source_relocations=relocations)
