import unittest,ast,hashlib
from pathlib import Path
import assemble as A
B=Path(__file__).resolve().parent;OLD=B.parent/'guangdong_r71_daily_simulated_settlement_20261112'
class Test(unittest.TestCase):
 def test_unchanged_formula_and_execution(self):
  self.assertEqual((B/'arithmetic.py').read_bytes(),(OLD/'arithmetic.py').read_bytes())
  def funcs(p):return {x.name:ast.dump(x)for x in ast.parse(p.read_text()).body if isinstance(x,(ast.FunctionDef,ast.ClassDef))}
  old,new=funcs(OLD/'assemble.py'),funcs(B/'assemble.py')
  for key in ('Sources','assumptions','compute','load_execution'):self.assertEqual(old[key],new[key])
 def test_old_summary_rejected(self):
  class S:
   def pin(self,*args):return {'schema':'gd.research.r69.full96_price_summary.v1'}
  with self.assertRaisesRegex(ValueError,'R74'):A.load_prices(S(),'unused',{},None)
 def test_r74_missing_rejected(self):
  class S:
   def pin(self,*args):return {'schema':'gd.research.r74.full96_price_summary.v1','quarters':[]}
  with self.assertRaisesRegex(ValueError,'96 complete'):A.load_prices(S(),'unused',{},None)
if __name__=='__main__':unittest.main()
