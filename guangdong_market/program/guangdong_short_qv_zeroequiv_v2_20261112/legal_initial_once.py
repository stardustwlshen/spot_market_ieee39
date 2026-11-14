from pathlib import Path
import json,hashlib,copy,sys
B=Path(__file__).resolve().parent;R=B.parent;S=R/'guangdong_short_loss_ac_loop_v1_20261112'
read=lambda p:json.loads(Path(p).read_text());sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(name,x):
 with(B/name).open('x')as f:json.dump(x,f,indent=2,allow_nan=False)
manifest=read(S/'MANIFEST.json');assert sha(S/'MANIFEST.json')=='16ef6dc859e5fc64d8f01ce6ee46a3de2f987dceefadd0a7dca76afd69822152'
for e in manifest['files']:assert sha(S/e['path'])==e['sha256']
m=read(S/'round_1/MAPPING.json');bs={b['id']:b for b in m['ac_input']['buses']};changed=[]
for g in m['ac_input']['generators']:
 b=bs[g['bus']];old=g['vg_pu'];g['vg_pu']=min(b['vmax_pu'],max(b['vmin_pu'],old));changed.append({'id':g['id'],'original_VG':old,'legal_VG':g['vg_pu']})
write('LEGAL_INITIAL_POLICY.json',{'policy':'deterministic projection of original input thermal VG into existing legal interval, Q0 unchanged; no optimized future controls','controls':changed,'source_mapping_sha256':sha(S/'round_1/MAPPING.json'),'source_response_sha256':sha(S/'round_1/SCED/response.json.gz'),'source_original_voltage_failure_preserved':True,'not_claimed_operational_instruction':True})
write('LEGAL_INITIAL_MAPPING.json',m);write('LEGAL_INITIAL_STARTED.json',{'AC_budget':1,'mapping_sha256':sha(B/'LEGAL_INITIAL_MAPPING.json')})
sys.path.insert(0,str(R/'guangdong_r30_ac_security_20261112'));from ac_security import check_ac
z=check_ac(m['ac_input'],max_iterations=60,tolerance_mva=1e-10);write('LEGAL_INITIAL_AC.json',z);assert z['ac_converged'];print(z['status'],z['max_original_network_residual_mva'])
