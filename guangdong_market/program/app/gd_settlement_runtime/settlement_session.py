"""Read-only component arithmetic. Never authenticates user data or releases a bill."""
import ast, importlib.util, sys, json, math
from pathlib import Path
from hashlib import sha256
from datetime import datetime
from decimal import Decimal as D
B=Path(__file__).resolve().parent

def need(ok,msg):
    if not ok: raise ValueError(msg)
def sha(p): return sha256(Path(p).read_bytes()).hexdigest()
def module(name,file):
    spec=importlib.util.spec_from_file_location(name,B/'vendor'/file);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
L=module('_settlement_r14','gd_r14_settlement.py')
R=module('_settlement_reserve','r54_reserve.py')
M=module('_settlement_regulation','gd_regulation_math.py')
# Execute unchanged retained function definitions only; omit their workspace-specific import loaders.
def retained(file,names,env):
    tree=ast.parse((B/'vendor'/file).read_text());nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
    need(len(nodes)==len(names),'retained function inventory');exec(compile(ast.Module(body=nodes,type_ignores=[]),str(B/'vendor'/file),'exec'),env);return env
from datetime import timedelta
E=retained('r36_energy.py',{'settle_energy','paired_storage'},dict(legacy=L,require=need,Decimal=D,timedelta=timedelta,SOURCE=B/'vendor/gd_r14_settlement.py',sha256=sha256))
A=retained('r27_agc.py',{'service_gross_statement'},dict(need=need,math=math,mileage_payment=M.mileage_payment))
LABEL='模型列示账户电能量分项净合计（研究）'
CLASSES=('measured','external_publication','research','missing')

def decode(x):
    """Explicit tagged JSON for retained typed API; never accept formal_data."""
    if isinstance(x,list):return tuple(decode(v) for v in x)
    if not isinstance(x,dict):return x
    if '$decimal' in x:return D(x['$decimal'])
    if '$datetime' in x:return datetime.fromisoformat(x['$datetime'])
    if '$type' in x:
        name=x['$type'];need(name in ('Evidence','Value','Attestation','Key','Award','Contract','SettlementBasis','StatementAmount'),'typed whitelist')
        return getattr(L,name)(**{k:decode(v)for k,v in x.items()if k!='$type'})
    return {k:decode(v)for k,v in x.items()}

def classified(fields,required,run_id):
    errors=[]
    for name in required:
        z=fields.get(name)
        if not isinstance(z,dict) or z.get('classification')=='missing':errors.append(name+': missing');continue
        need(z.get('classification') in CLASSES,'invalid input classification')
        need(z.get('source') and z.get('run_id')==run_id,'input source/run mismatch: '+name)
        need('value'in z,'explicit value required: '+name)
    return errors

class SettlementSession:
    """hour path is an export, not a live host. All supplied data remain unverified research inputs.
    Parent UI may run its existing trusted hour verifier first; this API never infers trust from JSON.
    """
    def __init__(self,hour_path,*,expected_run_id,expected_hour_sha256):
        self.path=Path(hour_path).resolve();need(sha(self.path)==expected_hour_sha256,'hour digest mismatch')
        self.h=json.loads(self.path.read_text());need(self.h['run_id']==expected_run_id,'hour/run mismatch')
        need(self.h.get('schema')=='gd.live_host.PRICE.hour.v3','hour export schema')
        self.run_id=expected_run_id;self.digest=expected_hour_sha256
        periods={(r['start'],r['end'])for r in self.h['rows']};need(len(periods)==1,'one hour only');self.period=next(iter(periods))
        for p,s in self.h.get('source_pins',{}).items():need(sha(p)==s,'export source changed')
        self.binding={'run_id':self.run_id,'hour_sha256':self.digest,'source_authentication':'export hashes checked; mathematical acceptance must be supplied by host verifier; no JSON grants formal status'}
    def check(self):need(sha(self.path)==self.digest,'hour mutated')
    def fields(self,fields):
        for z in fields.values():
            if isinstance(z,dict) and z.get('classification')!='missing':need((z.get('start'),z.get('end'))==self.period,'field interval mismatch')
    def result(self,**kw):return dict(binding=self.binding,formal_bill=None,payable=False,safety_release=False,native_calls=0,**kw)
    def hour_summary(self):
        self.check();rows=self.h['rows'];storage={}
        for r in rows:
            if r['technology']=='independent_storage':
                s=storage.setdefault(r['resource'],{});need(r['direction']not in s,'duplicate storage ledger');s[r['direction']]=D(r['energy_amount_CNY'])
        for v in storage.values():v['net']=sum(v.values(),D(0)) if {'charge','discharge'}<=v.keys() else None
        total=sum((D(r['energy_amount_CNY'])for r in rows),D(0));need(total==D(self.h['total_energy_component_CNY']),'hour sum mismatch')
        return self.result(status='research_export_summary',label=LABEL,total=total,storage=storage,rows=rows,not_turnover_or_profit=True)
    def energy(self,fields):
        self.check();self.fields(fields);missing=classified(fields,['typed_inputs'],self.run_id)
        if missing:return self.result(status='missing',missing=missing)
        kw=decode(fields['typed_inputs']['value']);need(kw.get('channel')=='simulation','formal_data is not authorized by user JSON')
        key=kw['key'];matches=[r for r in self.h['rows']if r['resource']==key.resource_id and r['node']==key.node_id and r['direction']==key.direction and r['start']==key.start.isoformat() and r['end']==key.end.isoformat()]
        need(len(matches)==1,'account/period not in selected hour')
        # Component-specific missing outputs from R14, even when R36 cannot form a subtotal.
        base={k:v for k,v in kw.items()if k not in ('technology','participation','participation_source','agency_mode','agency_contracts')}
        try:
            detail=L.calculate(**base)
            z=E['settle_energy'](**kw)
            return self.result(status='research_component_arithmetic',components=z['components'],subtotal=z['energy_component_subtotal'],detail=detail)
        except (ValueError,TypeError) as ex:
            return self.result(status='missing_or_unsupported',missing=[str(ex)],detail=locals().get('detail'),subtotal=None)
    def agc(self,fields):
        self.check();self.fields(fields);names=['measured_mileage_mw','uniform_price_yuan_mw','settlement_performance','designated_test'];missing=classified(fields,names,self.run_id)
        if missing:return self.result(status='missing',missing=missing)
        kw={k:fields[k]['value']for k in names};adj=None
        if 'adjustments'in fields and not classified(fields,['adjustments'],self.run_id):adj=fields['adjustments']['value']
        z=A['service_gross_statement'](**kw,adjustments=adj,source={k:fields[k]['source']for k in names})
        return self.result(status='research_component_arithmetic',component=z,missing=[]if adj is not None else ['reviewed adjustments; net unavailable'],capacity_is_not_mileage=True)
    def reserve(self,fields):
        self.check();self.fields(fields);missing=classified(fields,['scope','units'],self.run_id)
        if missing:return self.result(status='missing',missing=missing)
        s=dict(fields['scope']['value']);need((s['start'],s['end'])==self.period,'reserve interval mismatch');s['channel']='synthetic_fixture';s['start']=datetime.fromisoformat(s['start']);s['end']=datetime.fromisoformat(s['end'])
        for k in ('lower_requirement_mw','rate_yuan_per_MWh_service'):
            if s.get(k)is not None:s[k]=D(str(s[k]))
        need('rate_yuan_per_MWh_service'in s,'explicit R6 required, no silent dynamic default')
        us=[]
        for u in fields['units']['value']:
            u=dict(u)
            for k in ('rated_mw','declared_upper_mw','actual_mw','nuclear_normal_lower_mw'):
                if u.get(k)is not None:u[k]=D(str(u[k]))
            us.append(R.Unit(**u))
        z=R.calculate(R.Scope(**s),tuple(us));return self.result(status=z['status'],component=z,pool_claim='user-supplied research pool, not verified province pool',demand400plus400_not_compensation=True)

# Independent v2 extension loaded by absolute package-local path, no global import collisions.
_extspec=importlib.util.spec_from_file_location('_gd_settlement_extensions',B/'extensions.py')
_ext=importlib.util.module_from_spec(_extspec);sys.modules[_extspec.name]=_ext;_extspec.loader.exec_module(_ext)
SettlementSession.non_da_renewable=_ext.renewable
SettlementSession.operating_compensation=_ext.compensation
