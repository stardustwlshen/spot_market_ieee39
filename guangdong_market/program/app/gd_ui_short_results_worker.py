"""Read-only installed CLI1 result projection, never an optimizer entry."""
from pathlib import Path
import sys,json,subprocess,math
from datetime import datetime,timedelta
B=Path(__file__).resolve().parent
ENGINE=B.parent/'engines/guangdong_short_price_v1/guangdong_short_price_user_cli_v1_20261112'
sys.path.insert(0,str(ENGINE))
import loader
from gd_ui_short_read_adapter import load_prefix

def project(da,run,w,price=''):
 da,run=Path(da).resolve(),Path(run).resolve()
 (accepted,req,x),proof,case,carry=load_prefix(da,run,w)
 end=carry['executed_end_exclusive'];issued=carry['issued_end_exclusive']
 origin=datetime.fromisoformat(req['origin']);e=case['energy_inputs'];thermal=[];battery=[];vre=[];balance=[]
 for t in range(issued):
  row=dict(tick=t,start=(origin+timedelta(minutes=5*t)).isoformat(),end=(origin+timedelta(minutes=5*(t+1))).isoformat(),state='模拟已执行'if t<end else'下发未执行')
  for g in e['thermal']:thermal.append(dict(row,resource=g,mean_MW=x[f'power[{g},{t}]']))
  for u in case['batteries']:
   ch=x[f'battery.charge[{u},{t}]'];di=x[f'battery.discharge[{u},{t}]'];battery.append(dict(row,resource=u,charge_MW=ch,discharge_MW=di,net_MW=di-ch,end_SOC_MWh=x[f'battery.energy[{u},{t}]']))
  for b in e['buses']:
   avail=e['renewable_mw'][b,t];p=x[f"renewable_power['{b}',{t}]"];vre.append(dict(row,resource='VRE:'+str(b),available_MW=avail,dispatched_MW=p,curtailed_MW=avail-p))
  supply=math.fsum(z['mean_MW']for z in thermal if z['tick']==t)+math.fsum(z['net_MW']for z in battery if z['tick']==t)+math.fsum(z['dispatched_MW']for z in vre if z['tick']==t)
  load=math.fsum(e['load_mw'][b,t]for b in e['buses']);tie=math.fsum(e['physical_tie_mw'][b,t]for b in e['buses']);balance.append(dict(row,supply_MW=supply,load_MW=load,fixed_tie_MW=tie,residual_MW=supply+tie-load))
 loader.need(max(abs(z['residual_MW'])for z in balance)<1e-6,'已执行/下发段能量平衡不符')
 result=dict(status='verified_read_only',native_calls=0,run=str(run),selected_DA=str(da),window=w,executed_cells=end,issued_cells=issued,execution_end=(origin+timedelta(minutes=5*end)).isoformat(),issued_end=(origin+timedelta(minutes=5*issued)).isoformat(),thermal=thermal,battery=battery,VRE=vre,balance=balance,formal=False,safety_release=False,payable=False,price=None)
 if price:
  import program
  z=program.read_result(price);ctx=z.get('context',{})
  loader.need(ctx.get('short_run')==str(run) and ctx.get('selected_DA')==str(da) and ctx.get('window')==w and ctx.get('run_id')==accepted['run_id'] and ctx.get('SCED_response_sha256')==accepted['SCED_response_sha256'],'价格不属于所选实时窗/日前/原始解')
  loader.need(ctx['start']==(origin+timedelta(minutes=15*(w+1))).isoformat() and ctx['end']==(origin+timedelta(minutes=15*(w+2))).isoformat(),'价格交付时标错误');result['price']=z
 return result

if __name__=='__main__':
 try:print(json.dumps(project(sys.argv[1],sys.argv[2],int(sys.argv[3]),sys.argv[4]if len(sys.argv)>4 else''),allow_nan=False))
 except Exception as e:print(json.dumps(dict(status='blocked',error=str(e),native_calls=0)));raise SystemExit(1)
