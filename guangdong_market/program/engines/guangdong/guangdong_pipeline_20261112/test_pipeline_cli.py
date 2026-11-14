import unittest,tempfile,argparse
from pathlib import Path
import pipeline_cli as C
class Tests(unittest.TestCase):
 def test_dryrun_never_initializes(self):
  with tempfile.TemporaryDirectory()as t:
   r=Path(t)/'not_created';z=C.pipeline(argparse.Namespace(run=str(r),rt_windows=4,execute_native=False,simulation_settlement=False));self.assertEqual(z['status'],'dryrun_no_native_no_initialization');self.assertFalse(r.exists())
 def test_window_boundary(self):
  with self.assertRaises(ValueError):C.pipeline(argparse.Namespace(run='unused',rt_windows=97,execute_native=False,simulation_settlement=False))
 def test_exact_decimal_save(self):
  from decimal import Decimal
  with tempfile.TemporaryDirectory()as t:
   p=Path(t)/'x.json';C.save(p,{'amount':Decimal('1.000000000000000001')});self.assertEqual(C.read(p)['amount'],'1.000000000000000001');C.save(p,{'amount':Decimal('1.000000000000000001')})
if __name__=='__main__':unittest.main()
