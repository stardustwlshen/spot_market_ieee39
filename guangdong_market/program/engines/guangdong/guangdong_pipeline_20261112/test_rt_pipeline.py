import json
from pathlib import Path
import pytest
from rt_pipeline import OccurrenceJournal,checked_ref,sha,accepted_da,run_rt

def test_missing_or_unaccepted_DA_rejected(tmp_path):
 (tmp_path/'ARTIFACTS.json').write_text(json.dumps({'schema':'gd.pipeline.da_artifacts.v1','status':'prepared'}))
 with pytest.raises(ValueError,match='accepted'):accepted_da(tmp_path)
def test_refs_hash_and_escape(tmp_path):
 p=tmp_path/'x.json';p.write_text('{}');r=dict(path=p.name,sha256=sha(p));assert checked_ref(tmp_path,r)==p
 p.write_text('{"x":1}')
 with pytest.raises(ValueError):checked_ref(tmp_path,r)
 with pytest.raises(ValueError):checked_ref(tmp_path,dict(path='../x',sha256='x'))
def test_journal_never_repeats_attempt(tmp_path):
 j=OccurrenceJournal(tmp_path,'new','da');j.begin(0,{'path':'not_a_capability'})
 assert j.recovery()['state']=='pending_uncertain_native'
 with pytest.raises(ValueError):j.begin(0,{})
def test_completed_recovery_and_evidence_change(tmp_path):
 j=OccurrenceJournal(tmp_path,'new','da');j.begin(0,{})
 p=tmp_path/'W0'/'review.json';p.write_text('{}');j.accept(0,{'review':dict(path='W0/review.json',sha256=sha(p))})
 assert OccurrenceJournal(tmp_path,'new','da').recovery()['next_index']==1
 with pytest.raises(ValueError):OccurrenceJournal(tmp_path,'other','da')
 p.write_text('{"changed":1}')
 with pytest.raises(ValueError):j.recovery()
def test_native_dryrun_does_not_read_old_cases(tmp_path):
 r=run_rt(tmp_path/'newDA',tmp_path/'newPrep',tmp_path/'RT',execute_native=False)
 assert r['native_calls']==0 and r['R80']is False and not(tmp_path/'RT').exists()
def test_noncontiguous_recovery_rejected(tmp_path):
 j=OccurrenceJournal(tmp_path,'new','da');(tmp_path/'W2').mkdir();(tmp_path/'W2/ATTEMPT.json').write_text('{}')
 with pytest.raises(ValueError):j.recovery()
