import unittest,textwrap
from pathlib import Path
from decimal import Decimal as D
from datetime import datetime
from arithmetic import need
S=(Path(__file__).parent/'assemble_v2.py').read_text();BLOCK=textwrap.dedent(S[S.index('    raw_qd,raw_qe=qd,qe'):S.index('    rows.append(settle_hour')])
class Tests(unittest.TestCase):
 def run_block(self,qe,direction='charge'):
  x=dict(qd=D(0),qe=D(qe),direction=direction,normalizations=[],r='ES3',st=datetime.fromisoformat('2026-11-11T23:00:00+08:00'),D=D,need=need)
  exec(BLOCK,x);return x
 def test_actual_tiny_charge(self):
  x=self.run_block('8.376924884750655E-13');self.assertEqual(x['qe'],0);self.assertEqual(x['raw_qe'],D('8.376924884750655E-13'));self.assertEqual(x['normalizations'][0]['delta_MWh'],-x['raw_qe'])
 def test_large_reject(self):
  with self.assertRaises(ValueError):self.run_block('1.1e-9')
 def test_no_round_valid_direction(self):
  x=self.run_block('-8e-13');self.assertEqual(x['qe'],D('-8e-13'));self.assertEqual(x['normalizations'],[])
if __name__=='__main__':unittest.main()
