"""Explicit 96-window research declaration inventory. Never publishes receipts."""
from pathlib import Path
from copy import deepcopy
from datetime import timedelta
from .phase_runtime import need,stamp,digest
from .live_hook import read,sha
from .planning import algebra

def expected(run,nextday_path):
 import agc_lifecycle
 from nextday_declaration import validate
 run=Path(run).resolve();origin=stamp(read(run/'seed/request.json')['origin'])
 source=Path(agc_lifecycle.B)/'agc_runtime/RESEARCH_INPUT.json'
 need(sha(source)==agc_lifecycle.INPUT_SHA,'fixed research qualifications changed')
 base=read(source);nd=Path(nextday_path).resolve();extension=read(nd)
 validate(nd,cutoff=extension['research_extension']['available_at'])
 need(extension['units']==base['units'],'D1 qualifications changed')
 rows={}
 for i in range(1,25):
  start=origin+timedelta(hours=i);cut=start-timedelta(minutes=30)
  d=deepcopy(extension if i==24 else base);d.update(hour_start=start.isoformat(),as_of=cut.isoformat())
  algebra(d,hour_start=d['hour_start'],cutoff=d['as_of'])
  rows[i]=d
 return rows,{str(source):sha(source),str(nd):sha(nd),str(run/'seed/request.json'):sha(run/'seed/request.json')}

def prepare(run,output,*,nextday_path,research=False):
 need(research is True,'explicit named D1 research selection required')
 rows,pins=expected(run,nextday_path);out=Path(output).resolve();need(not out.exists(),'new declaration inventory only');out.mkdir(parents=True)
 files=[]
 for i,d in rows.items():
  f=out/f'H{i:02}.json';f.write_text(__import__('json').dumps(d,indent=2));files.append(str(f));pins[str(f)]=sha(f)
 z=dict(schema='gd.post_scuc.day_inventory.v1',run=str(Path(run).resolve()),nextday_path=str(Path(nextday_path).resolve()),declarations=files,source_pins=pins,publication=False,H25_authorized=False,native_calls=0,formal=False)
 f=out/'INVENTORY.json';f.write_text(__import__('json').dumps(z,indent=2));return dict(z,inventory_path=str(f),inventory_sha256=sha(f))

def verify(path,run,declarations):
 f=Path(path).resolve();z=read(f);need(z['schema']=='gd.post_scuc.day_inventory.v1'and z['run']==str(Path(run).resolve()),'calendar source mismatch')
 for p,h in z['source_pins'].items():need(sha(p)==h,'calendar input drift')
 rows,pins=expected(run,z['nextday_path']);need([str(Path(p).resolve())for p in declarations]==z['declarations'],'complete ordered H1..H24 inventory required')
 need(len(declarations)==24,'missing hour declaration')
 for i,p in enumerate(declarations,1):need(read(p)==rows[i],'not deterministic original qualification/hour declaration')
 need(all(z['source_pins'].get(p)==h for p,h in pins.items()),'calendar provenance mismatch')
 return z

def bind(controller,path):
 h=controller.h;z=verify(path,h.session.run,[controller.declarations[i][0]for i in sorted(controller.declarations)])
 need(set(controller.declarations)==set(range(1,25)),'complete H1..24 only')
 need(getattr(h,'windows',None)==96 and h.window==0 and h.pending is None,'fresh96 calendar only')
 need(str(Path(h.agc.nextday_agc_path).resolve())==z['nextday_path'],'host selected different D1 declaration')
 h.session.registry['source_pins'].update(z['source_pins']);h.session.registry['source_pins'][str(Path(path).resolve())]=sha(path)
 controller.calendar_inventory=deepcopy(z)
 return dict(hours=list(range(1,25)),H0_preserved=True,H24_after_W94_SCUC_only=True,H25_authorized=False,native_calls=0)
