from pathlib import Path
import json,hashlib
from r27_agc import synthetic_case,template
p=Path(__file__).parent/'prepared_synthetic_v1';p.mkdir(exist_ok=False)
c=synthetic_case();q,n,m=template(c)
for name,obj in [('input.json',c),('template.json',q),('names.json',n),('MODEL_SCOPE.json',m)]:
 (p/name).write_text(json.dumps(obj,sort_keys=True,separators=(',',':'),allow_nan=False))
(p/'PINS.json').write_text(json.dumps({z.name:hashlib.sha256(z.read_bytes()).hexdigest()for z in p.iterdir()},sort_keys=True))
print(str(p))
