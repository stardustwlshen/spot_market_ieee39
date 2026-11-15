from pathlib import Path
import sys,json,hashlib
A=Path(__file__).resolve().parent;sys.path.insert(0,str(A));from gd_provincial_da_contract import verify_da,verify,read
root=Path(sys.argv[1]).resolve();lp=Path(sys.argv[2]).resolve();sourcecase=Path(sys.argv[3]).resolve();sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest();ref=lambda p:dict(path=str(Path(p).resolve()),sha256=sha(p));write=lambda p,z:exclusive_write(p,z)
def exclusive_write(p,z):
 with Path(p).open('x')as f:json.dump(z,f,indent=2,allow_nan=False)
z=verify_da(root);l=read(lp/'COMPLETE.json')
assert l['status']=='accepted_research_98' and l['KKT']['passed']
for p,h in l['source_pins'].items():assert sha(p)==h,p
for r in l['artifacts'].values():assert sha(r['path'])==r['sha256']
if (root/'MANIFEST.json').exists():raise RuntimeError('new registration only')
write(root/'VERIFIED_DISPATCH.json',z)
raw=read(l['artifacts']['raw_prices']['path']);ticks=raw['raw_prices_by_tick'];assert set(ticks)=={str(t)for t in range(96)}
assert set(raw['representative_prices_by_tick'])=={'96','97'}
buses=set(ticks['0']);assert len(buses)==39 and all(set(v)==buses for v in ticks.values())
write(root/'DA_PRICES_VIEW.json',dict(schema='gd.provincial.da_price_read_view.v1',grid_minutes=15,raw_unclipped_LMP={b:[ticks[str(t)][b]for t in range(96)]for b in buses},source=ref(l['artifacts']['raw_prices']['path']),arithmetic='identity transpose only; no price modification'))
pins={**z['source_pins'],**l['source_pins'],str(lp/'COMPLETE.json'):sha(lp/'COMPLETE.json')}
for r in l['artifacts'].values():pins[r['path']]=r['sha256']
pins[str(sourcecase)]=sha(sourcecase)
m=dict(schema='gd.provincial.da_raw.v1',run_id=z['run_id'],grid_minutes=15,executed_periods=96,model_periods=98,representative_mapping=z['representative_mapping'],source_pins=pins,model_case=ref(root/'case.json'),source_case=ref(sourcecase),physical_solution=ref(root/'physical_solution.json'),pumped_declarations=ref(root/'PUMPED_DECLARATIONS.json'),stages={})
for stage in ['SCUC','SCED']:
 m['stages'][stage]=dict(status='accepted',request=ref(root/(stage+'_request.json.gz')),response=ref(root/(stage+'_response.json.gz')),names=ref(root/'names.json'),KKT=z['SCED_KKT']if stage=='SCED'else None)
m['stages']['PRICE']=dict(status='accepted',**l['artifacts'],KKT=l['KKT'],acceptance=ref(lp/'COMPLETE.json'))
m['limits']=['98 compressed model indices; only actual-day96 enter execution and settlement; next-day representative points use same-day peak/trough by user decision','inherited research AGC awards not newly optimized','no AC by user instruction','RT LMP reference user-selected SCED; rule text ambiguity retained']
write(root/'MANIFEST.json',m);verify(root/'MANIFEST.json',require_price=True)
art=dict(schema='gd.provincial.da_artifacts.v1',grid_minutes=15,status='accepted_research',run_id=z['run_id'],source_identity=sha(root/'SOURCE.json'),input_identity=sha(root/'case.json'),inputs=dict(case=ref(sourcecase),model_case=ref(root/'case.json'),agc=ref(root/'agc.json'),policy=ref(root/'MANIFEST.json')),stages={})
art['stages']['da_scuc']=dict(acceptance=ref(root/'VERIFIED_DISPATCH.json'),request=ref(root/'SCUC_request.json.gz'),response=ref(root/'SCUC_response.json.gz'),full_names=ref(root/'names.json'))
art['stages']['da_sced']=dict(acceptance=ref(root/'VERIFIED_DISPATCH.json'),request=ref(root/'SCED_request.json.gz'),response=ref(root/'SCED_response.json.gz'),expanded_response=ref(root/'SCED_response.json.gz'),full_names=ref(root/'names.json'),physical_solution=ref(root/'physical_solution.json'))
art['stages']['da_price']=dict(acceptance=ref(lp/'COMPLETE.json'),request=l['artifacts']['request'],response=l['artifacts']['response'],full_names=l['artifacts']['names'],raw_prices=ref(root/'DA_PRICES_VIEW.json'))
write(root/'ARTIFACTS.json',art);write(root/'RUN.json',dict(run_id=z['run_id'],schema='gd.provincial.da_run.v1',grid_minutes=15,source_identity=art['source_identity'],input_identity=art['input_identity'],inputs=art['inputs'],source_pins=pins));print(z['run_id'],'new DA registered; stated research limits retained')
