"""Pure gate tests, not generated physical-security evidence."""
from pathlib import Path
import unittest
import result_status as S

class ACGateTests(unittest.TestCase):
 def test_ac_pass_reserve_or_agc_failure_is_red(self):
  self.assertFalse(S.ac_joint_safe({'AC':{'ac_snapshot_safe':True},'capacity_review':{'research_safe':False}}))
 def test_missing_capacity_is_not_green(self):
  for value in ({},None,False):
   self.assertFalse(S.ac_joint_safe({'AC':{'ac_snapshot_safe':True},'capacity_review':value}))
  self.assertFalse(S.ac_joint_safe({'AC':{'ac_snapshot_safe':True}}))
 def test_both_explicit_true(self):
  self.assertTrue(S.ac_joint_safe({'AC':{'ac_snapshot_safe':True},'capacity_review':{'research_safe':True}}))
 def test_truthy_values_are_not_boolean_pass(self):
  for v in (1,'true','pass',[],None):
   self.assertFalse(S.ac_joint_safe({'AC':{'ac_snapshot_safe':True},'capacity_review':{'research_safe':v}}))
   self.assertFalse(S.ac_joint_safe({'AC':{'ac_snapshot_safe':v},'capacity_review':{'research_safe':True}}))
 def test_actual_three_unsafe_same_run_bindings(self):
  root=Path(__file__).resolve().parents[4];run=root/'guangdong_pipeline_20261112/pure_fresh_run_v1';ac=root/'guangdong_fresh_run_ac_bridge_20261112'
  z=S.status(run,ac/'prepared_v1/PLAN.json',ac/'actual_v1/RESULT.json')
  self.assertEqual(z['ac']['status'],'research_snapshot_unsafe')
  self.assertEqual([r['index']for r in z['ac']['checked_cells']],[0,124,212])
  self.assertTrue(all(r['safe'] is False for r in z['ac']['checked_cells']))
  self.assertEqual(z['energy']['rt_executed_cells'],288)
 def test_cross_run_ac_still_rejected(self):
  root=Path(__file__).resolve().parents[4];run=root/'guangdong_pipeline_20261112/pure_fresh_run_v1';old=root/'guangdong_r81_sced_ac_bridge_20261112'
  with self.assertRaises(ValueError):S.status(run,old/'prepared_v1/PLAN.json',old/'actual_v1/RESULT.json')

if __name__=='__main__':unittest.main()
