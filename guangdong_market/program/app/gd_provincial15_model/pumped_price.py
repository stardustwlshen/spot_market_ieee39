"""Independent RT LMP pumped block, bound to accepted same-period SCED inputs.
No new physical solve; mode/start decisions fixed, continuous generation retains
explicit delta range. Fixed-speed pumping stays fixed by physical equation.
"""
from pathlib import Path
import hashlib,math,json,gzip
import pyomo.environ as pyo
from .pumped_storage import attach
from .numeric_boundary import reference_value

def attach_from_prepared(m,prepared_dir,absolute_x,first,delta,*,source_pins):
    from smm.gd_october_job_codec import loads
    path=(Path(prepared_dir)/'derived_case.json').resolve();digest=hashlib.sha256(path.read_bytes()).hexdigest()
    if source_pins.get(str(path))!=digest:raise ValueError('accepted SCED derived case must be explicitly source-bound')
    if isinstance(delta,bool) or not math.isfinite(delta) or not 0<=delta<=1:raise ValueError('explicit finite price reference delta required')
    case=loads(path.read_bytes());inv=case['battery_contract']['storage']['pumped_storage']['inventory'];decl=case['pumped_storage_declarations']
    b=attach(m,inv,decl);b.reference=pyo.ConstraintList();trace=[]
    def value(name,s,u,t):
        x=float(absolute_x[f'pumped.{name}[{s},{u},{first+t}]'])
        if not math.isfinite(x):raise ValueError('finite source SCED pumped witness required')
        return x
    for s,u in b.U:
        for t in b.T:
            for name in ('gen_mode','pump_mode','gen_start','pump_start'):
                x=value(name,s,u,t)
                if abs(x-round(x))>1e-6 or round(x)not in (0,1):raise ValueError('accepted integer PS mode/start required')
                v=getattr(b,name)[s,u,t];v.fix(round(x));v.domain=pyo.Reals
            for name in ('gen','pump'):
                raw=value(name,s,u,t)
                data=m._pumped_data[s]['p'];capacity=float(data['unit_gen_max'] if name=='gen' else data['unit_pump'])
                active=int(round(value(name+'_mode',s,u,t)))
                # Exact fixed mode plus original capacity inequality defines this interval.
                x,numeric_audit=reference_value(raw,lower=0.,upper=capacity*active,capacity_scale=capacity,unit='MW')
                if numeric_audit['delta']:
                    # Recheck the actual accepted serialized inequalities, not just labels.
                    files=[Path(prepared_dir)/'names.json',Path(prepared_dir).parent/'actual/SCED/request.json.gz',Path(prepared_dir).parent/'actual/SCED/response.json.gz']
                    for f in files:
                        f=f.resolve()
                        if source_pins.get(str(f))!=hashlib.sha256(f.read_bytes()).hexdigest():raise ValueError('numeric boundary raw evidence not source-bound')
                    names=json.loads(files[0].read_bytes());q=json.load(gzip.open(files[1]));response=json.load(gzip.open(files[2]))
                    column=f'pumped.{name}[{s},{u},{t}]';mode_column=f'pumped.{name}_mode[{s},{u},{t}]';i=names['variables'].index(column);j=names['variables'].index(mode_column)
                    if response['primal'][i]!=raw or q['variables'][i]['lb']!=0 or q['variables'][j]['lb']!=active or q['variables'][j]['ub']!=active:raise ValueError('raw bound/mode proof mismatch')
                    rows=[(k,row) for k,row in enumerate(q['rows']) if dict(row['terms'])=={i:1.,j:-capacity} and row['ub']==0]
                    if not rows:raise ValueError('original mode-capacity inequality absent')
                    numeric_audit.update(source_column=column,source_mode=mode_column,source_row=names['constraints'][rows[0][0]],source_request_sha256=hashlib.sha256(files[1].read_bytes()).hexdigest())
                lo=(1-delta)*x;hi=(1+delta)*x
                b.reference.add(getattr(b,name)[s,u,t]>=lo);b.reference.add(getattr(b,name)[s,u,t]<=hi)
                trace.append(dict(station=s,unit=u,local_tick=t,absolute_tick=first+t,direction=name,SCED_mw=raw,reference_SCED_mw=x,numeric_boundary=numeric_audit,reference_min_mw=lo,reference_max_mw=hi,fixed_mode=True))
    pins={str(path):digest,str(Path(__file__).resolve()):hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    from . import pumped_storage,numeric_boundary
    src=Path(numeric_boundary.__file__).resolve();pins[str(src)]=hashlib.sha256(src.read_bytes()).hexdigest()
    src=Path(pumped_storage.__file__).resolve();pins[str(src)]=hashlib.sha256(src.read_bytes()).hexdigest()
    return dict(cost=b.cost,trace=trace,source_pins=pins,delta=delta,policy='same_period_SCED_modes_fixed_continuous_generation_delta; fixed-speed_pump_exact',raw_physical_recomputed=False)
