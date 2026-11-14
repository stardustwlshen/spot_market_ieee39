"""Source-only R17 oracle, written independently of producer audit/build code."""
from pathlib import Path
from hashlib import sha256
from datetime import datetime,timedelta
from copy import deepcopy
import json,math,base64

W=Path('/workspace/scratch/42c5fe32dad0')
CORE=W/'guangdong_r14_core95_20261112'
ORIGINAL=W/'guangdong_r15_priced_verified_source_v1_20261112'
PREP=W/'guangdong_r15_pure_proof_20261112/preparation_raw'
OUT=W/'guangdong_r17_midnight_checkpoints_20261112/independent'
TARGET={'ES1':100.,'ES2':200.,'ES3':100.,'ES4':300.,'ES5':300.}
SERVICES={'up':'positive','down':'negative','primary':'primary','accident':'accident'}
def rd(p):return json.loads(Path(p).read_text())
def canonical(o):return json.dumps(o,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(o):return sha256(canonical(o)).hexdigest()
def write(n,o):(OUT/n).write_text(json.dumps(o,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def manifest(root):
    f={str(p.relative_to(root)):{'bytes':p.stat().st_size,'sha256':sha256(p.read_bytes()).hexdigest()} for p in sorted(root.rglob('*')) if p.is_file()}
    return {'file_count':len(f),'tree_sha256':digest(f),'files':f}
def originals():return {n:manifest(p) for n,p in [('R15',ORIGINAL),('core95',CORE),('prep21',PREP)]}
def unpack(v):
    if not isinstance(v,dict):return v
    tag=v['tag'];z=v['value']
    if tag=='dict':return {unpack(k):unpack(t) for k,t in z}
    if tag=='bytes':return base64.b64decode(z)
    if tag=='list':return [unpack(t) for t in z]
    if tag=='tuple':return tuple(unpack(t) for t in z)
    if tag=='datetime':return datetime.fromisoformat(z)
    if tag=='class':return {k:unpack(t) for k,t in z.items()}
    raise AssertionError(tag)
def near(a,b,msg,tol=1e-7):assert abs(a-b)<=tol,(msg,a,b)
def named(folder):
    n=rd(folder/'names.json')['variables'];v=rd(folder/'response.json')['primal'];assert len(n)==len(v)==len(set(n));assert all(type(z) in (int,float) and math.isfinite(z) for z in v)
    return dict(zip(n,v))

class Source:
    def __init__(self):
        self.data=d=rd(CORE/'ieee39_native_v4b/reports/sced/input.json');self.x=x=d['physical_lift']
        self.values=v=named(CORE/'ieee39_native_v4b/reports/sced');self.mip=mv=named(CORE/'bess_native_v4b/reports/native')
        self.pub=rd(CORE/'ieee39_native_v4b/reports/publication96.json')
        self.source=unpack(rd(CORE/'input/historical_da_only/source_reconstructed_day_inputs.json')['value'])
        self.bats=B=[r for r,u in d['resources'].items() if u['kind']=='battery'];self.gens=G=[r for r in d['resources'] if r not in B]
        self.start=datetime.fromisoformat(x['cells'][0]['start']);assert len(x['cells'])==576
        self.maxerr={};self.refs=[];self.states=[];self.zeros=[]
        for j,c in enumerate(x['cells']):
            assert datetime.fromisoformat(c['start'])==self.start+timedelta(minutes=5*j)
            assert datetime.fromisoformat(c['end'])==self.start+timedelta(minutes=5*(j+1))
        assert x['market_to_physical']==[list(range(3*q,3*q+3)) for q in range(96)]+[[363,364,365],[498,499,500]]
        for r,u in d['resources'].items():
            assert len(self.pub['power_mw'][r])==96
            for q,ids in enumerate(x['market_to_physical']):
                self.eq('market_mean',v[f'power[{r},{q}]'],math.fsum(v[f'physical_power[{r},{j}]'] for j in ids)/3)
                if q<96:self.eq('publication',self.pub['power_mw'][r][q],v[f'power[{r},{q}]'])
        for r in B:
            assert len(d['native_upstream_modes'][r])==24
            for h in range(24,48):
                mode=d['native_upstream_modes'][r][h-24]
                assert mode in ('charge','discharge','idle')
                for m in ('charge','discharge','idle'):self.eq('original_MIP_onehot',mv[f'mode[{r},{h},{m}]'],int(m==mode))
                assert all(z['mode']==mode for z in x['resources'][r]['rows'][12*h:12*h+12])
        for q in range(103):
            ids=list(range(3*q,3*q+3));rr={}
            for r,u in d['resources'].items():
                zz=x['resources'][r]['rows'][3*q:3*q+3];ref=math.fsum(v[f'physical_power[{r},{j}]'] for j in ids)/3
                z={'reference_mw':ref,'min_mw':math.fsum(a['min_mw'] for a in zz)/3,'max_mw':math.fsum(a['max_mw'] for a in zz)/3,'source_row_sha256':digest(zz)}
                if r in B:
                    modes={a['mode'] for a in zz};assert not {'charge','discharge'}<=modes
                    mode=next(iter(modes-{'idle'}),'idle');z.update(mode=mode,active_mask=[a['mode']!='idle' for a in zz],price_eligible=mode!='idle')
                    if q>=96:
                        z['permission']={'charge':-1,'discharge':1,'idle':0}[d['native_upstream_modes'][r][q//4-24]]
                        assert mode=='discharge' and z['permission']==1
                        if abs(ref)<1e-9:self.zeros.append({'q':q,'resource':r,'mode':mode,'reference_mw':ref,'eligible':True,'band':[0,0]})
                else:
                    phases={a['phase'] for a in zz};assert len(phases)==1;z.update(phase=next(iter(phases)),price_eligible=phases=={'stable'})
                rr[r]=z
            self.refs.append({'q':q,'start':x['cells'][ids[0]]['start'],'end':x['cells'][ids[-1]]['end'],'indices':ids,'resources':rr})
        state={r:{'energy_mwh':u.get('initial_energy_mwh'),'power_mw':u['initial_power_mw']} for r,u in d['resources'].items()}
        for r in B:
            t=self.source['batteries'][r];state[r].update(mode=t['initial_mode'],mode_age_h=t['initial_mode_age_h'],daily={self.start.date().isoformat():0.,(self.start-timedelta(days=1)).date().isoformat():0.},cumulative=0.)
            self.eq('SOC_initial',state[r]['energy_mwh'],v[f'physical_energy[{r},0]'])
        for r in G:
            t=self.source['energy_inputs']['thermal'][r];state[r].update(phase='stable' if t['initial_online'] else 'off',state_age_h=t['initial_state_age_h'])
        self.states.append(deepcopy(state))
        for j,c in enumerate(x['cells']):
            for r,u in d['resources'].items():
                z=x['resources'][r]['rows'][j];p=v[f'physical_power[{r},{j}]'];prev=state[r]['power_mw'];self.le('physical_bounds',z['min_mw']-p);self.le('physical_bounds',p-z['max_mw'])
                if r in G and z['phase'] in ('startup','shutdown'):
                    self.eq('transition_connection',prev,z['begin_mw']);self.eq('transition_mean',p,z['mean_mw']);state[r]['power_mw']=z['end_mw']
                    t=self.source['energy_inputs']['thermal'][r];phase=z['phase'];oldphase=state[r]['phase'];age=state[r]['state_age_h']
                    if phase!=oldphase:
                        assert oldphase==('off' if phase=='startup' else 'stable')
                        assert age+1e-7>=t['min_down_h' if phase=='startup' else 'min_up_h']
                        if phase=='startup':age=0.
                    age+=1/12
                    nextphase=x['resources'][r]['rows'][j+1]['phase'] if j<575 else phase
                    ending=nextphase!=phase
                    state[r]['phase']=('stable' if phase=='startup' else 'off') if ending else phase
                    state[r]['state_age_h']=0. if ending and phase=='shutdown' else age
                else:
                    self.le('ramp',p-prev-z['ramp_up_mw_h']/12);self.le('ramp',prev-p-z['ramp_down_mw_h']/12);state[r]['power_mw']=p
                    if r in G:
                        assert state[r]['phase']==z['phase'];state[r]['state_age_h']+=1/12
                for f,s in SERVICES.items():
                    res=v[f'physical_{f}[{r},{j}]'];self.le('reserve_bound',-res);self.le('reserve_bound',res-z[s+'_cap_mw']);self.le('reserve_headroom',res-(p-z['min_mw'] if s=='negative' else z['max_mw']-p))
                    if not z['reserve_eligible']:assert z[s+'_cap_mw']==0
                if r in B:
                    mode=z['mode'];assert (mode=='charge' and z['max_mw']<=0) or (mode=='discharge' and z['min_mw']>=0) or (mode=='idle' and z['min_mw']==z['max_mw']==0)
                    charge=-p if mode=='charge' else 0.;discharge=p if mode=='discharge' else 0.
                    energy=state[r]['energy_mwh']+(u['eta_charge']*charge-discharge/u['eta_discharge'])/12
                    self.eq('SOC_recurrence',energy,v[f'physical_energy[{r},{j+1}]']);self.le('SOC_bounds',u['energy_min_mwh']-energy);self.le('SOC_bounds',energy-u['energy_max_mwh'])
                    state[r]['energy_mwh']=energy;state[r]['mode_age_h']=state[r]['mode_age_h']+1/12 if state[r]['mode']==mode else 1/12;state[r]['mode']=mode
                    date=c['start'][:10];amount=(u['eta_charge']*charge+discharge/u['eta_discharge'])/12
                    state[r]['daily'][date]=state[r]['daily'].get(date,0.)+amount;state[r]['daily'].setdefault(c['end'][:10],0.);state[r]['cumulative']+=amount
                    self.le('dated_throughput',state[r]['daily'][date]-x['resources'][r]['cycle_budget'][date]['limit_internal_mwh'])
            inj={n:math.fsum(v[f'physical_power[{r},{j}]'] for r,u in d['resources'].items() if u['node']==n)+x['fixed_injection_mw'][n][j]-x['load_mw'][n][j] for n in d['nodes']}
            self.eq('physical_balance',math.fsum(inj.values()),0)
            for line,z in d['network'].items():
                f=math.fsum(z['ptdf'][n]*inj[n] for n in d['nodes']);self.le('hard_network',f-x['network'][line]['upper_mw'][j]);self.le('hard_network',x['network'][line]['lower_mw'][j]-f)
            for f,s in SERVICES.items():self.le('reserve_requirement',x['reserve'][j][s+'_mw']-math.fsum(v[f'physical_{f}[{r},{j}]'] for r in d['resources']))
            assert x['reserve'][j]=={'positive_mw':400.,'negative_mw':0.,'primary_mw':0.,'accident_mw':400.}
            self.states.append(deepcopy(state))
        for r in B:
            self.eq('D96_target',self.states[288][r]['energy_mwh'],TARGET[r]);assert d['resources'][r]['terminal_targets']==[{'index':95,'energy_mwh':TARGET[r]}]
            assert d['resources'][r]['terminal_interpretation']=='D96_only_research'
            for h in range(48):assert not {'charge','discharge'}<={z['mode'] for z in x['resources'][r]['rows'][12*h:12*h+12]}
        assert max(self.maxerr.values())<1e-7
    def eq(self,k,a,b):self.maxerr[k]=max(self.maxerr.get(k,0.),abs(a-b))
    def le(self,k,v):self.maxerr[k]=max(self.maxerr.get(k,0.),v)
    def forecast(self,start):
        result={}
        for family in ('load_mw','wind_mw','solar_mw','boundary_tie_mw'):
            saved=self.source['contract']['sources']['prior:'+family];assert sha256(saved['artifact']).hexdigest()==saved['sha256'];prior=json.loads(saved['artifact'])
            cutoff=start-timedelta(minutes=15)
            assert prior['uses_future_actual'] is False
            for v in (saved['available_at'],prior['available_at'],prior['information_end']):assert datetime.fromisoformat(v)<=cutoff
            values={n:[0.]*24 for n in self.data['nodes']}
            def at(r,dt):
                info=prior['resources'][r];h=(dt-datetime.fromisoformat(prior['epoch'])).total_seconds()/3600;phase=int(sha256((str(prior['seed'])+':'+r).encode()).hexdigest()[:8],16)/2**32*2*math.pi;s=info['scale_mw']
                if family=='load_mw':y=s*(.91+.09*math.cos(2*math.pi*(h-18)/24)+.025*math.sin(4*math.pi*h/24+phase)+.0002*h)
                elif family=='wind_mw':y=s*(.38+.10*math.sin(2*math.pi*h/18+phase)+.035*math.cos(2*math.pi*h/7+phase))
                elif family=='solar_mw':y=s*.72*max(0.,math.sin(math.pi*((h%24)-6)/12)) if 6<=h%24<=18 else 0.
                else:y=s*(.70+.05*math.cos(2*math.pi*h/24+phase)+.0001*h)
                return round(y,9)
            for r,info in prior['resources'].items():
                knots=[at(r,start+timedelta(minutes=15*q)) for q in range(9)]
                for j in range(24):q,k=divmod(j,3);values[info['bus']][j]+=knots[q]+(knots[q+1]-knots[q])*(k+.5)/3
            result[family]=values
        return result
    def check_contract(self,c):
        assert len(c['physical_cells'])==309 and len(c['references'])==103
        assert c['publication96']==self.pub and c['midnight_targets']==TARGET
        assert [z['physical_indices'] for z in c['representatives']]==[[363,364,365],[498,499,500]]
        assert c['representatives'][0]['grid']==self.data['grid'][96] and c['representatives'][1]['grid']==self.data['grid'][97]
        for j,cell in enumerate(c['physical_cells']):
            assert cell['start']==self.x['cells'][j]['start'] and cell['end']==self.x['cells'][j]['end']
            for r in self.bats:
                z=cell['batteries'][r];assert z['mode']==self.x['resources'][r]['rows'][j]['mode'];near(z['power_mw'],self.values[f'physical_power[{r},{j}]'],'source physical power')
                near(z['energy_begin_mwh'],self.values[f'physical_energy[{r},{j}]'],'begin SOC');near(z['energy_end_mwh'],self.values[f'physical_energy[{r},{j+1}]'],'end SOC')
                if j>=288:assert z['selected_hour_direction_permission']==1
            for r in self.gens:near(cell['thermal'][r]['mean_mw'],self.values[f'physical_power[{r},{j}]'],'thermal physical power')
        for q,ref in enumerate(c['references']):
            expected=self.refs[q];assert ref['physical_indices']==expected['indices'] and ref['start']==expected['start'] and ref['end']==expected['end']
            for r,u in ref['resources'].items():
                z=expected['resources'][r]
                for k in ('reference_mw','min_mw','max_mw'):near(u[k],z[k],f'q{q} {r} {k}')
                for k in ('price_eligible','mode','phase','active_mask'):
                    if k in z:assert u[k]==z[k],(q,r,k)
                assert u['physical_rows_sha256']==z['source_row_sha256']
                assert u['source_market_index']==(q if q<96 else None)
                assert u['published_DA_award']==(self.pub['power_mw'][r][q] if q<96 else None)
                assert u['formal_energy_price_qualification'] is None
                if q>=96:assert u['reference_kind']=='archived_R14_physical_SCED_3cell_mean_not_published_DA_award'
        assert c['availability_is_simulated'] is True and c['future_RT_used'] is False and c['native_release_available'] is False
    def check_window(self,inp,index):
        a=3*index;start=self.start+timedelta(minutes=15*index);w=inp['window'];assert w['index']==index and w['start']==start.isoformat() and w['cutoff']==(start-timedelta(minutes=15)).isoformat()
        assert w['horizon_end']==(start+timedelta(hours=2)).isoformat();assert w['midnight_local_energy_index']==(288-a if a<288<=a+24 else None)
        assert datetime.fromisoformat(inp['publication_at'])<=datetime.fromisoformat(w['cutoff']);assert inp['projection']['state']['at']==w['start'];assert inp['construction_only'] is True and inp['feasibility_established'] is False and inp['native_release_available'] is False
        forecasts=self.forecast(start)
        for fam,v in inp['vintages'].items():
            for k in ('source_available_at','issued_at','available_at','information_end'):assert datetime.fromisoformat(v[k])<=datetime.fromisoformat(w['cutoff'])
            assert v['coverage_start']==w['start'] and v['coverage_end']==w['horizon_end'] and v['uses_future_actual'] is False and v['is_measurement'] is False
            assert v['prior_sha256']==self.source['contract']['sources']['prior:'+fam]['sha256']
        assert inp['nodes']==self.data['nodes']
        for n in inp['nodes']:
            for j in range(24):near(inp['load_mw'][n][j],forecasts['load_mw'][n][j],'forecast load');near(inp['fixed_injection_mw'][n][j],math.fsum(forecasts[f][n][j] for f in ('wind_mw','solar_mw','boundary_tie_mw')),'forecast injection')
        assert inp['reserve']==self.x['reserve'][a:a+24]
        for line,z in inp['network'].items():
            assert z['ptdf']==self.data['network'][line]['ptdf']
            for k in ('lower_mw','upper_mw'):assert z[k]==self.x['network'][line][k][a:a+24]
        for r,u in inp['resources'].items():
            assert u['rows']==self.x['resources'][r]['rows'][a:a+24]
            for k,v in self.data['resources'][r].items():
                if k not in ('rows','terminal_energy_mwh','cycle_budget'):assert u[k]==v,(r,k)
            s=inp['projection']['state']['batteries' if r in self.bats else 'thermal'][r];exp=self.states[a][r]
            near(s['power_mw'],exp['power_mw'],'inherited power')
            if r in self.gens:
                assert s['phase']==exp['phase'];near(s['state_age_h'],exp['state_age_h'],'inherited thermal age')
                t=self.source['energy_inputs']['thermal'][r];near(s['residence_remaining_h'],max(0.,t['min_up_h' if exp['phase']!='off' else 'min_down_h']-exp['state_age_h']),'inherited thermal residence')
            if r in self.bats:
                near(s['energy_mwh'],exp['energy_mwh'],'inherited SOC');assert s['mode']==exp['mode'];near(s['mode_age_h'],exp['mode_age_h'],'mode age')
                assert set(s['daily_used_internal_mwh'])==set(exp['daily'])
                for date,used in exp['daily'].items():near(s['daily_used_internal_mwh'][date],used,'dated inherited usage')
                near(s['cumulative_internal_mwh'],exp['cumulative'],'cumulative usage')
                near(u['terminal_energy_mwh'],self.states[a+24][r]['energy_mwh'],'rolling endpoint')
                assert u['cycle_budget']==self.x['resources'][r]['cycle_budget']
    def report(self):return {'scope':'576 original physical SCED source cells, 103 chronological quarters; no new optimization or live execution authority','max_errors':self.maxerr,'tail_references':self.refs[96:],'zero_reference_discharge_eligible_rows':self.zeros,'boundary_states':{str(j):self.states[j] for j in (264,267,270,273,276,279,282,285,288,291,309,576)},'representatives':self.data['grid'][96:]}

if __name__=='__main__':
    before=originals();write('IMMUTABLE_INPUTS_BEFORE.json',before);assert before['R15']['tree_sha256']=='3fefa3952b84e3f171259f99eb4ceb9b16e934424395245de660df4c9ae92e1a';assert before['core95']['tree_sha256']=='0ab6b4f87541e639e0f5279476df2ed8f734919c434ad593bfbd34f87df34cc2';s=Source();write('INDEPENDENT_SOURCE_NUMERICS.json',s.report());assert originals()==before;print(json.dumps({'max_errors':s.maxerr,'zero_discharge_reference_count':len(s.zeros),'source_trees':{k:{f:v[f] for f in ('file_count','tree_sha256')} for k,v in before.items()}},indent=2))
