from pathlib import Path
import copy,json,sys,tempfile,hashlib,subprocess
ROOT=Path('/workspace/scratch/42c5fe32dad0/ieee39-reboot-recovery')
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'app')]
from streamlit.testing.v1 import AppTest
from smm import scenario as sc, process_control as pc
from smm.run_recovery import inspect_recovery
import common
out=Path('/workspace/scratch/42c5fe32dad0/ieee39-user-journey-review')
workspace=Path(tempfile.mkdtemp(prefix='ieee39-recovery-review-'))
sc.SCEN_DIR=workspace/'scenarios';sc.RUNS_DIR=workspace/'runs';sc.ensure_default()
report={'root':str(ROOT),'base_commit':subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip(),'workspace':str(workspace)}
def record(name,kind='previous_boot'):
 d=sc.RUNS_DIR/'默认'/name;d.mkdir(parents=True)
 ident=pc.process_identity()
 if kind=='previous_boot':
  ident['scope']['boot_time']-=100000
  ident['create_time']=ident['scope']['boot_time']+60
  ident['scope']['boot_identity']={**ident['scope']['boot_identity'],'sha256':'1'*64}
 elif kind=='legacy':
  ident['scope'].pop('machine_identity');ident['create_time']-=1
 elif kind=='foreign':ident['scope']['machine_identity']['sha256']='2'*64
 meta={'scenario':'默认','mode':'full','status':'running','stage':'日前出清','started':'2026-11-03T00:00:00','bid_version':3,'periods':None,'process_identity':ident,'executed_periods':0}
 (d/'meta.json').write_text(json.dumps(meta,ensure_ascii=False))
 (d/'run.log').write_text('[DA] SCUC beginning\noriginal run log\n')
 (d/'submitted_bids.json').write_text('{"review":"unchanged quote"}\n')
 return d
first=record('first');second=record('second')
original={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for d in [first,second] for p in d.iterdir() if p.name!='meta.json'}
at=AppTest.from_file(str(ROOT/'app/pages/2_运行.py'),default_timeout=60).run()
assert not at.exception,[x.value for x in at.exception]
button=lambda label:next(b for b in at.button if b.label==label)
choose=lambda d:next(x for x in at.selectbox if x.label=='查看运行').set_value(str(d)).run()
choose(first)
assert button('▶ 开始运行').disabled
button('请求取消（窗口边界）').click().run()
assert button('▶ 开始运行').disabled
assert button('确认进程已不存在并恢复').disabled
next(x for x in at.checkbox if x.label=='确认保留全部历史文件，并将此运行标记为中断').check().run()
button('确认进程已不存在并恢复').click().run()
assert not at.exception,[x.value for x in at.exception]
assert button('▶ 开始运行').disabled
report['first_only']={'status':json.loads((first/'meta.json').read_text())['status'],'start_remains_disabled':button('▶ 开始运行').disabled,'cancel_file_preserved':(first/'cancel.request').exists()}
choose(second)
next(x for x in at.checkbox if x.label=='确认保留全部历史文件，并将此运行标记为中断').check().run()
button('确认进程已不存在并恢复').click().run()
assert not at.exception,[x.value for x in at.exception]
assert not button('▶ 开始运行').disabled
assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==sha for p,sha in original.items())
report['all_recovered']={'start_enabled':not button('▶ 开始运行').disabled,'non_meta_files_unchanged':True,'statuses':[m['status'] for m in sc.list_runs()],'settled':[m.get('settled') for m in sc.list_runs()]}
next(x for x in at.text_input if x.label=='新版场景名称').set_value('恢复后新场景')
button('创建并切换到新版场景').click().run()
assert not at.exception,[x.value for x in at.exception]
assert not button('▶ 开始运行').disabled
report['new_scene_after_recovery']={'scene':next(x.value for x in at.selectbox if x.label=='当前场景'),'start_enabled':not button('▶ 开始运行').disabled}
report['blocked']={}
for kind in ['legacy','foreign','alive']:
 d=record(kind,kind)
 at.run();choose(d)
 assert not at.exception,[x.value for x in at.exception]
 assert button('▶ 开始运行').disabled
 assert not any(b.label=='确认进程已不存在并恢复' for b in at.button)
 report['blocked'][kind]={'reason':inspect_recovery(d)['reason'],'start_disabled':button('▶ 开始运行').disabled,'recovery_button_present':False,'warnings':[x.value for x in at.warning],'info':[x.value for x in at.info]}
 # Test fixture only: remove this synthetic active marker before testing next case.
 m=json.loads((d/'meta.json').read_text());m['status']='failed';(d/'meta.json').write_text(json.dumps(m))
report['market_solves_started']=0
(out/'probe_reboot_fix.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False,indent=2))
