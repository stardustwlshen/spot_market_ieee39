from pathlib import Path
import sys,json
sys.path.insert(0,str(Path(__file__).resolve().parent))
from gd_provincial_da_lmp_job import solve
if __name__=='__main__':
 print(json.dumps(solve(sys.argv[1],sys.argv[2],delta=float(sys.argv[3]),network_penalty_yuan_mwh=1000,representative_periods=json.loads((Path(sys.argv[1])/"SOURCE.json").read_text())["representative_mapping"])))
