"""Allow only fully verified HiGHS tiny-coefficient storage warnings. Never run."""
import math,re,hashlib,json
from fractions import Fraction as F
import numpy as np

def require(ok,msg):
 if not ok:raise RuntimeError('R42 matrix-storage verification: '+msg)
def math_digest(data):
 return hashlib.sha256(json.dumps({k:data[k]for k in ('variables','rows','objective')},sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def verify(data,h,hs,status,messages,policy):
 require(status in (hs.HighsStatus.kOk,hs.HighsStatus.kWarning),'passModel failed')
 require(math_digest(data)==policy['request_math_sha256'],'request mathematics differs from authorized template')
 s,cutoff=h.getOptionValue('small_matrix_value');require(s==hs.HighsStatus.kOk and math.isfinite(cutoff)and cutoff>0,'actual small_matrix_value readback')
 require(str(cutoff)in policy['threshold_profiles'],'unreviewed native cutoff')
 bound=policy['threshold_profiles'][str(cutoff)];box={int(k):F(v)for k,v in policy['injection_box_by_reduced_column'].items()}
 lp=h.getLp();n=len(data['variables']);m=len(data['rows']);require(lp.num_col_==n and lp.num_row_==m,'row/column count changed')
 inf=hs.kHighsInf
 exact=lambda a,b,label:require(np.array_equal(np.asarray(a),np.asarray(b)),label+' changed')
 exact(lp.col_lower_,[-inf if v['lb']is None else v['lb']for v in data['variables']],'column lower bounds')
 exact(lp.col_upper_,[inf if v['ub']is None else v['ub']for v in data['variables']],'column upper bounds')
 exact(lp.row_lower_,[-inf if r['lb']is None else r['lb']for r in data['rows']],'row lower bounds')
 exact(lp.row_upper_,[inf if r['ub']is None else r['ub']for r in data['rows']],'row upper bounds')
 costs=[0.]*n
 for j,c in data['objective']['terms']:costs[j]+=c
 exact(lp.col_cost_,costs,'objective coefficients');require(lp.offset_==data['objective']['constant'],'objective constant changed')
 require(lp.sense_==(hs.ObjSense.kMinimize if data['objective']['minimize']else hs.ObjSense.kMaximize),'objective sense changed')
 exact(lp.integrality_,[hs.HighsVarType.kContinuous if v['kind']=='C'else hs.HighsVarType.kInteger for v in data['variables']],'integrality')
 dropped=affected=0;maxcoef=0.;worst=F(0);counts=np.zeros(n,dtype=np.int64)
 for row in data['rows']:
  loss=F(0);here=0;previous=-1
  for j,c in row['terms']:
   require(type(j)is int and 0<=j<n and j>previous and math.isfinite(c)and c!=0,'unexpected duplicate/unordered/nonfinite original coefficient');previous=j
   if abs(c)<=cutoff:
    require(j in box,'no reviewed physical bound for dropped column');dropped+=1;here+=1;maxcoef=max(maxcoef,abs(c));loss+=abs(F(c))*box[j]
   else:counts[j]+=1
  worst=max(worst,loss);affected+=bool(here)
 require(dropped==bound['potentially_ignored_coefficients']and affected==bound['affected_rows'],'discard counts differ from reviewed profile')
 require(str(worst)==bound['exact_worst_row_activity_loss']and maxcoef==bound['largest_potentially_ignored_abs_coefficient'],'discard coefficient/activity envelope differs')
 require(float(worst)<=1e-6,'activity error exceeds original audit tolerance')
 starts=np.concatenate(([0],np.cumsum(counts)));ind=np.empty(int(starts[-1]),dtype=np.int32);vals=np.empty(int(starts[-1]),dtype=np.float64);cursor=starts[:-1].copy()
 for i,row in enumerate(data['rows']):
  for j,c in row['terms']:
   if abs(c)>cutoff:k=cursor[j];ind[k]=i;vals[k]=c;cursor[j]+=1
 matrix=lp.a_matrix_;require(matrix.format_==hs.MatrixFormat.kColwise,'unexpected stored matrix format')
 exact(matrix.start_,starts,'filtered matrix column starts');exact(matrix.index_,ind,'filtered matrix row indices');exact(matrix.value_,vals,'filtered matrix coefficients')
 warnings=[line.strip()for message in messages for line in message.splitlines()if re.search(r'\b(?:WARNING|ERROR)\b',line,re.I)]
 if status==hs.HighsStatus.kWarning:
  require(dropped>0 and len(warnings)==1,'unexpected/additional warning')
  match=re.fullmatch(r'WARNING: LP matrix packed vector contains (\d+) \|value\| in \[([^,]+), ([^\]]+)\] less than or equal to ([^:]+): ignored',warnings[0])
  require(match is not None and int(match[1])==dropped and float(match[4])==cutoff,'warning is not exactly reviewed tiny-term removal')
 else:require(not warnings and dropped==0,'unexplained warning/drop in kOk result')
 return {'schema':'gd.research.r42.checked_native_storage.v1','status':'verified_exact_requested_storage_modulo_reviewed_small_terms','pass_model_status':str(status),'small_matrix_value_readback':cutoff,'discarded_coefficients':dropped,'affected_rows':affected,'maximum_discarded_abs_coefficient':maxcoef,'worst_activity_error_over_original_injection_box_mw':float(worst),'exact_worst_activity_error':str(worst),'stored_nonzeros':int(starts[-1]),'all_stored_coefficients_indices_bounds_cost_types_rows_exact_checked':True,'warnings':warnings,'optimize_calls_before_accepting_storage':0}
