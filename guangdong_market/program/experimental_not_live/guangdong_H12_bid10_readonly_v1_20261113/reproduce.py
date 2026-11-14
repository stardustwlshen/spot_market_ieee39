from pathlib import Path
import sys,json
B=Path(__file__).resolve().parent
sys.path.insert(0,str(B.parent/'guangdong_general_hourly_agc_v2_20261113'))
from hourly import clear
for stem in ('BASE','CANDIDATE'):
 x=json.loads((B/(stem+'_H12_INPUT.json')).read_text());expected=json.loads((B/(stem+'_RESULT.json')).read_text());assert clear(x)==expected
assert json.loads((B/'CANDIDATE_RESULT.json').read_text())['awards_mw']['C1']==10
print('2 exact pure recomputations passed; 0 native; C1 not released')
