"""Immutable full matrix with sparse window objective and committed-prefix bounds."""
from common67 import *
def location(label):
 s=name(label)
 if s=='zero':return None,None
 stem,inside=s.split('[',1);parts=inside[:-1].split(',')
 require(stem in ('power','energy','reserve','segment','vre_power','vre_curtailment'),'unclassified full column '+s)
 return stem,int(parts[1])
class TemplateCache:
 def __init__(self,template,names):
  self.template=template;self.names=names;self.names_hash=digest(names);self.base_hash=digest(template);self.rows_hash=digest(template['rows']);self.locations=[location(n) for n in names['variables']]
  self.fixed={};self.committed_end=0;self.numeric_boundary_records={}
 def request(self,window):
  require(window*3==self.committed_end and 0<=window<96,'exact issued prefix/current window')
  q=dict(self.template);q['options']=dict(self.template['options'],dual_feasibility_tolerance=1e-9,simplex_scale_strategy=0);q['variables']=[dict(v)for v in self.template['variables']]
  for j,v in self.fixed.items():q['variables'][j].update(lb=v,ub=v,start=None)
  q['objective']=dict(minimize=True,constant=0.,terms=[[j,c]for j,c in self.template['objective']['terms'] if self.locations[j][1] is not None and window*3<=self.locations[j][1]<window*3+24])
  q.update(dual=True,preparation_elapsed_s=0.,r67_window=window,r67_global_offset=window*3,r67_base_template_sha256=self.base_hash,r67_rows_sha256=self.rows_hash,r67_committed_prefix_end=self.committed_end,r67_prefix_numeric_policy='raw_native_unchanged_declared_original_domain_tolerance_1e-7')
  return q
 def commit_first_three(self,window,primal):
  require(window*3==self.committed_end and len(primal)==len(self.locations),'sequential complete global primal')
  end=window*3+3
  for j,(stem,k)in enumerate(self.locations):
   if k is not None and (k<=end if stem=='energy' else k<end):
    if j in self.fixed:require(abs(self.fixed[j]-primal[j])<=1e-7,'committed prefix changed')
    else:
     raw=float(primal[j]);base=self.template['variables'][j];violation=max(0.,0. if base['lb'] is None else base['lb']-raw,0. if base['ub'] is None else raw-base['ub']);require(violation<=1e-7,'raw prefix outside original domain numerical policy')
     self.fixed[j]=raw
     if violation:self.numeric_boundary_records[j]=dict(name=self.names['variables'][j],raw_fixed_unchanged=raw,original_lower=base['lb'],original_upper=base['ub'],violation=violation,tolerance=1e-7,policy='raw native unchanged; explicit numerical bound deviation, not physical capacity change')
  self.committed_end=end
 def local_view(self,window,primal):
  offset=window*3;names=[];values=[]
  for j,(stem,k)in enumerate(self.locations):
   if stem is None:names.append(self.names['variables'][j]);values.append(primal[j]);continue
   if offset<=k<(offset+25 if stem=='energy' else offset+24):
    label=self.names['variables'][j];head,inside=label.split('[',1);parts=inside[:-1].split(',');parts[1]=str(k-offset);names.append(head+'['+','.join(parts)+']');values.append(primal[j])
  return dict(variables=names,constraints=[]),values
