"""R30 independent balanced AC snapshot verification. No dispatch optimization.
Missing AC parameters fail closed. No DC result or convergence alone is security.
"""
from copy import deepcopy
from hashlib import sha256
import json,math

class ACInputError(ValueError):pass

def need(condition,message):
    if not condition:raise ACInputError(message)

def numeric(record,fields):
    for key in fields:
        need(key in record and type(record[key]) in (int,float) and math.isfinite(record[key]),'missing/nonfinite numeric field: '+key)

def validate(case):
    need(isinstance(case,dict) and case.get('schema')=='r30.ac_snapshot.v1','explicit r30.ac_snapshot.v1 input required')
    numeric(case,['base_mva','frequency_hz']);need(case['base_mva']>0 and case['frequency_hz']>0,'positive base/frequency required')
    need(isinstance(case.get('provenance'),str) and case['provenance'].strip(),'input provenance required')
    need(case.get('operating_point_kind') in ('research_snapshot','user_supplied_snapshot'),'explicit operating-point scope required')
    for key in ('buses','generators','branches'):need(isinstance(case.get(key),list) and len(case[key])>0,'explicit nonempty '+key+' required')
    buses=case['buses'];byid={}
    for b in buses:
        need(isinstance(b,dict),'bus record required');numeric(b,['pd_mw','qd_mvar','gs_mw','bs_mvar','vm_pu','va_degree','base_kv','vmin_pu','vmax_pu'])
        need(isinstance(b.get('id'),str) and b['id'] not in byid,'unique string bus ID required')
        need(b.get('type') in ('PQ','PV','SLACK'),'PQ/PV/SLACK bus type required')
        need(b['base_kv']>0 and 0<b['vmin_pu']<=b['vmax_pu'] and b['vm_pu']>0,'invalid voltage/base limits')
        byid[b['id']]=b
    need(sum(b['type']=='SLACK' for b in buses)==1,'exactly one explicit slack bus required')
    gens=set();gids=set()
    for g in case['generators']:
        need(isinstance(g,dict),'generator record required');numeric(g,['pg_mw','qg_mvar','pmin_mw','pmax_mw','qmin_mvar','qmax_mvar','vg_pu'])
        need(g.get('bus') in byid,'generator bus unknown');need(isinstance(g.get('id'),str) and g['id'] not in gids,'unique generator ID required');gids.add(g['id'])
        need(g['bus'] not in gens,'multiple generators at one bus require an explicit aggregation/allocation adapter');gens.add(g['bus'])
        need(g['pmin_mw']<=g['pmax_mw'] and g['qmin_mvar']<=g['qmax_mvar'] and g['vg_pu']>0,'invalid generator limits')
    need(all(b['id'] in gens for b in buses if b['type'] in ('PV','SLACK')),'PV/slack bus needs explicit generator and Q/P limits')
    adjacency={k:set() for k in byid};ids=set()
    for line in case['branches']:
        need(isinstance(line,dict),'branch record required');numeric(line,['r_pu','x_pu','b_pu','rate_mva','tap','shift_degree'])
        need(isinstance(line.get('id'),str) and line['id'] not in ids,'unique branch ID required');ids.add(line['id'])
        a,b=line.get('from_bus'),line.get('to_bus');need(a in byid and b in byid and a!=b,'invalid branch endpoints')
        need(type(line.get('status')) is int and line['status'] in (0,1),'explicit branch status 0/1 required')
        need(line['r_pu']>=0 and abs(line['r_pu'])+abs(line['x_pu'])>0 and line['rate_mva']>0 and line['tap']>=0,'positive thermal rating and nonzero impedance required; zero rating is not unlimited')
        if line['status']:adjacency[a].add(b);adjacency[b].add(a)
    seen=set();todo=[buses[0]['id']]
    while todo:
        x=todo.pop()
        if x in seen:continue
        seen.add(x);todo.extend(adjacency[x]-seen)
    need(len(seen)==len(byid),'islanded topology rejected; no silent disconnected-load removal')
    return byid

def check_ac(case,*,max_iterations=40,tolerance_mva=1e-7,limit_tolerance=1e-6):
    """Return safe/unsafe/nonconverged/rejected. Never changes the input.

    P/Q demand and fixed-P generation are given, not inferred from DC schedules.
    PV Q limits are enforced by the backend; slack P/Q are checked after solving.
    Slack balances losses; its actual injection and limits are always reported.
    """
    try:
        byid=validate(case);need(type(max_iterations) is int and 1<=max_iterations<=100,'iterations must be 1..100')
        need(type(tolerance_mva) in (float,int) and 0<tolerance_mva<=1e-4,'AC tolerance must be positive and <=1e-4 MVA')
        need(type(limit_tolerance) in (float,int) and 0<=limit_tolerance<=1e-4,'limit tolerance must be 0..1e-4')
    except ACInputError as exc:
        return dict(status='rejected_input',ac_converged=False,ac_snapshot_safe=False,reason=str(exc),official_operational_acceptance=False)
    data=deepcopy(case);raw=json.dumps(data,sort_keys=True,separators=(',',':'),allow_nan=False).encode();digest=sha256(raw).hexdigest()
    import numpy as np
    import pandapower as pp
    from pandapower.converter.pypower import from_ppc
    from pandapower.pypower.makeYbus import makeYbus
    buses=data['buses'];gens=data['generators'];branches=data['branches'];idx={b['id']:j for j,b in enumerate(buses)}
    bus=np.array([[j,{'PQ':1,'PV':2,'SLACK':3}[b['type']],b['pd_mw'],b['qd_mvar'],b['gs_mw'],b['bs_mvar'],1,b['vm_pu'],b['va_degree'],b['base_kv'],1,b['vmax_pu'],b['vmin_pu']] for j,b in enumerate(buses)],dtype=float)
    gen=np.array([[idx[g['bus']],g['pg_mw'],g['qg_mvar'],g['qmax_mvar'],g['qmin_mvar'],g['vg_pu'],data['base_mva'],1,g['pmax_mw'],g['pmin_mw']]+[0.]*11 for g in gens])
    branch=np.array([[idx[l['from_bus']],idx[l['to_bus']],l['r_pu'],l['x_pu'],l['b_pu'],l['rate_mva'],l['rate_mva'],l['rate_mva'],l['tap'],l['shift_degree'],l['status'],-360,360] for l in branches],dtype=float)
    ppc=dict(version='2',baseMVA=data['base_mva'],bus=bus,gen=gen,branch=branch)
    base=dict(input_sha256=digest,backend='pandapower Newton-Raphson',backend_version=pp.__version__,official_operational_acceptance=False,balanced_positive_sequence_snapshot_only=True,dispatch_optimization_performed=False,HiGHS_calls=0,scope=data['operating_point_kind'],limitations=['No AC OPF or automatic dispatch repair','No dynamic/transient/voltage stability proof','No N-1 screening unless each explicit contingency is separately submitted','Not Guangdong official AC security approval'])
    try:
        net=from_ppc(deepcopy(ppc),f_hz=data['frequency_hz'],validate_conversion=False)
        pp.runpp(net,algorithm='nr',calculate_voltage_angles=True,init='flat',max_iteration=max_iterations,tolerance_mva=tolerance_mva,enforce_q_lims=True,numba=False,check_connectivity=False,trafo_model='pi',voltage_depend_loads=False,distributed_slack=False)
    except pp.LoadflowNotConverged as exc:
        return dict(base,status='nonconverged',ac_converged=False,ac_snapshot_safe=False,reason=str(exc),feedback=[dict(kind='AC_NONCONVERGENCE',action='Review explicit operating point/topology/voltage support; do not accept DC feasibility as AC validation')])
    except Exception as exc:
        return dict(base,status='backend_error',ac_converged=False,ac_snapshot_safe=False,reason=type(exc).__name__+': '+str(exc))
    V=np.array([net.res_bus.loc[j,'vm_pu']*np.exp(1j*np.deg2rad(net.res_bus.loc[j,'va_degree'])) for j in range(len(buses))]);violations=[];generation=[];Sspec=np.zeros(len(buses),dtype=complex)
    def check(kind,element,value,lower=None,upper=None):
        if (lower is not None and value<lower-limit_tolerance) or (upper is not None and value>upper+limit_tolerance):violations.append(dict(kind=kind,element=element,value=float(value),lower=lower,upper=upper))
    for j,g in enumerate(gens):
        lookup=net._from_ppc_lookups['gen'].loc[j];row=net['res_'+lookup.element_type].loc[int(lookup.element)];p,q=float(row.p_mw),float(row.q_mvar);Sspec[idx[g['bus']]]+=complex(p,q)
        generation.append(dict(id=g['id'],bus=g['bus'],p_mw=p,q_mvar=q,scheduled_p_mw=g['pg_mw'],p_change_mw=p-g['pg_mw'],slack=byid[g['bus']]['type']=='SLACK'))
        check('GENERATOR_P_LIMIT',g['id'],p,g['pmin_mw'],g['pmax_mw']);check('GENERATOR_Q_LIMIT',g['id'],q,g['qmin_mvar'],g['qmax_mvar'])
        if byid[g['bus']]['type']!='SLACK':check('FIXED_P_MISMATCH',g['id'],p-g['pg_mw'],-tolerance_mva,tolerance_mva)
    voltages=[]
    for j,b in enumerate(buses):
        Sspec[j]-=complex(b['pd_mw'],b['qd_mvar']);vm=abs(V[j]);check('BUS_VOLTAGE_LIMIT',b['id'],vm,b['vmin_pu'],b['vmax_pu']);voltages.append(dict(id=b['id'],vm_pu=float(vm),va_degree=float(np.angle(V[j],deg=True))))
    Y,Yf,Yt=makeYbus(data['base_mva'],bus,np.pad(branch,((0,0),(0,13)),constant_values=0));calc=V*np.conj(Y@V)*data['base_mva'];residual=calc-Sspec;maximum=float(np.max(np.abs(residual)))
    if not np.all(np.isfinite(V)) or not math.isfinite(maximum):return dict(base,status='invalid_numeric_result',ac_converged=False,ac_snapshot_safe=False)
    if maximum>max(10*tolerance_mva,1e-6):violations.append(dict(kind='ORIGINAL_NETWORK_AC_RESIDUAL',value=maximum,upper=max(10*tolerance_mva,1e-6)))
    Sf=V[branch[:,0].astype(int)]*np.conj(Yf@V)*data['base_mva'];St=V[branch[:,1].astype(int)]*np.conj(Yt@V)*data['base_mva'];flows=[]
    for j,l in enumerate(branches):
        for side,s in [('from',Sf[j]),('to',St[j])]:check('BRANCH_MVA_LIMIT',l['id']+':'+side,abs(s),upper=l['rate_mva'])
        flows.append(dict(id=l['id'],from_p_mw=float(Sf[j].real),from_q_mvar=float(Sf[j].imag),to_p_mw=float(St[j].real),to_q_mvar=float(St[j].imag),from_mva=float(abs(Sf[j])),to_mva=float(abs(St[j])),rate_mva=l['rate_mva'],status=l['status']))
    return dict(base,status='ac_snapshot_unsafe' if violations else 'ac_snapshot_safe',ac_converged=True,ac_snapshot_safe=not violations,max_original_network_residual_mva=maximum,generators=generation,buses=voltages,branches=flows,violations=violations,feedback=[dict(kind=v['kind'],element=v.get('element'),action='Return explicit violation to dispatch/input owner; no automatic constraint relaxation or setpoint rewrite') for v in violations],input_unchanged=(case==data))
