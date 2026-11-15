"""Source-bound independent DA LMP native job, preserving previous attempts."""
from pathlib import Path
import json,gzip,time,hashlib,importlib.util,shutil
from gd_provincial_da_lmp import build,extract
from gd_provincial_kkt import metrics

read=lambda p:json.loads(gzip.decompress(p.read_bytes()) if p.suffix=='.gz' else p.read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()

def solve(da_directory,output_directory,*,delta,network_penalty_yuan_mwh,representative_periods=None):
    d=Path(da_directory).resolve();o=Path(output_directory).resolve();p=Path(__file__).resolve().parents[1]
    source_files=[d/f for f in ('SCED_request.json.gz','SCED_response.json.gz','names.json','case.json','physical_solution.json')]
    source_pins={str(f):sha(f)for f in source_files}
    # Fail closed if dispatch provenance no longer matches the implementation
    # that generated its raw solution; valid raw KKT alone is not provenance.
    manifest=d/'SOURCE.json'
    if not manifest.exists():raise ValueError('source dispatch provenance required')
    source_pins[str(manifest)]=sha(manifest)
    for f,h in read(manifest).get('source_pins',{}).items():
        if not Path(f).is_file() or sha(f)!=h:raise ValueError('source dispatch provenance drift: '+f)
        source_pins[f]=h
    request=read(source_files[0]);response=read(source_files[1]);names=read(source_files[2])
    sk=metrics(request,response,names,angle_tolerance=1e-7)
    if not sk['passed']:raise ValueError('source SCED fails disclosed full-model KKT: '+str(sk))
    q,n,review=build(request,names,response,delta=delta,network_penalty_yuan_mwh=network_penalty_yuan_mwh,representative_periods=representative_periods)
    o.mkdir(parents=True,exist_ok=False)
    sources=o/'source_snapshot';sources.mkdir()
    for f in (Path(__file__),Path(__file__).with_name('gd_provincial_da_lmp.py'),Path(__file__).with_name('gd_provincial_kkt.py')):
        shutil.copy2(f,sources/f.name);source_pins[str(sources/f.name)]=sha(sources/f.name)
    worker=p/'engines/guangdong_short_unified_v6/rt/guangdong_rt_recommit_program_v1_20261112/vendor/guangdong_r65_three_state_startup_20261112/standard_counted_worker.py'
    shutil.copy2(worker,sources/worker.name);source_pins[str(sources/worker.name)]=sha(sources/worker.name)
    config=dict(delta=delta,network_penalty_yuan_mwh=network_penalty_yuan_mwh,representative_periods=representative_periods or [])
    (o/'CONFIG.json').write_text(json.dumps(config,indent=2));source_pins[str(o/'CONFIG.json')]=sha(o/'CONFIG.json')
    (o/'BUILD_REVIEW.json').write_text(json.dumps(review,indent=2));(o/'names.json').write_text(json.dumps(n))
    spec=importlib.util.spec_from_file_location('_provincial_da_lmp_native',worker);w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)
    q['deadline']=time.perf_counter()+3900.;(o/'request.json.gz').write_bytes(gzip.compress(json.dumps(q).encode()))
    start=time.monotonic();r=w.solve(q);elapsed=time.monotonic()-start;(o/'response.json.gz').write_bytes(gzip.compress(json.dumps(r).encode()))
    if r.get('status')!='optimal' or not r.get('dual_valid'):raise ValueError('independent DA LMP not optimal: '+str(r.get('status')))
    k=metrics(q,r,n,angle_tolerance=1e-7);(o/'KKT.json').write_text(json.dumps(k,indent=2))
    if not k['passed']:raise ValueError('independent DA LMP KKT failed: '+str(k))
    prices=extract(q,n,r,representative_periods=representative_periods);(o/'prices.json').write_text(json.dumps(prices,indent=2))
    for f,h in source_pins.items():
        if sha(f)!=h:raise ValueError('source changed during native solve: '+f)
    record=dict(schema='gd.provincial.da_lmp_evidence.v1',status='accepted_research_'+str(review['periods']),grid_minutes=15,periods=review['periods'],settlement_periods=96,elapsed_s=elapsed,KKT=k,SCED_KKT=sk,source_pins=source_pins,scope=review['scope_exclusions'],formal_rule_certified=False,artifacts={key:dict(path=str(o/f),sha256=sha(o/f))for key,f in [('request','request.json.gz'),('response','response.json.gz'),('names','names.json'),('raw_prices','prices.json')]})
    (o/'COMPLETE.json').write_text(json.dumps(record,indent=2));return record
