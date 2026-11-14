from prepare import *
from scoped_kkt import metrics
from fractions import Fraction as F
import highspy as hs
q=read(BASE/'sced_native_v1/request.json');r=read(BASE/'sced_native_v1/response.json');n=read(BASE/'actual_review_v1/sced_prepared_v1/names.json');z=metrics(q,r,n,angle_tolerance=1e-7)
h=hs.Highs();status,tol=h.getOptionValue('dual_feasibility_tolerance');need(status==hs.HighsStatus.kOk and tol==1e-7,'native default readback');need('dual_feasibility_tolerance'not in q['options'],'worker option not overridden')
i=z['worst_free_angle']['index'];exact=sum((-F(float(c))*F(float(y))for row,y in zip(q['rows'],r['dual'])for j,c in row['terms']if j==i),F(0))
z.update(status='diagnostic_only_pending_parent_acceptance',native_default_dual_feasibility_tolerance=tol,default_readback_HiGHS_version=h.version(),exact_serialized_angle_stationarity_fraction=str(exact),exact_serialized_angle_stationarity_float=float(exact),solver_calls=0,raw_response_sha256=sha(BASE/'sced_native_v1/response.json'))
write(BASE/'FULL_SCED_KKT_DIAGNOSTIC.json',z);print(json.dumps(z))
