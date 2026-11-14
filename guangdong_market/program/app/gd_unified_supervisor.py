from pathlib import Path
import sys
import gd_unified_backend as api
p=Path(sys.argv[1]).resolve();j=api.read(p/'JOB.json');code=1
try:
 api.need(api.sha(api.CONFIG)==j['config_sha256'],'安装身份发生变化')
 args=api.native_command(j['kind'],j['run'],j['window']);api.need(list(map(str,args))==api.read(p/'COMMAND.json'),'命令记录改变')
 state=api.rt_proof(j['da'],j['run'],j['window'])if j['kind']=='rt'else api.price_status(j['run'],j['window'])
 api.need(state==j['state'],'启动前状态改变，拒绝迟到执行')
 result=api.call(args,timeout=7800 if j['kind']=='rt'else 3900);api.write(p/'RESULT.json',result);code=0
except Exception as e:api.write(p/'ERROR.json',dict(error=str(e),automatic_retry=False))
api.write(p/'EXIT.json',dict(returncode=code,automatic_retry=False,requires_core_verification=True));raise SystemExit(code)
