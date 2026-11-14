from pathlib import Path
import json
from hashlib import sha256
from r24_bridge import require

def bind_scuc_event(native,preparation,postreview,expected_response_sha,expected_post_sha):
    native,preparation,postreview=map(Path,(native,preparation,postreview))
    sha=lambda p:sha256(p.read_bytes()).hexdigest()
    read=lambda p:json.loads(p.read_bytes())
    identity=read(native/'IDENTITY.json');launch=read(native/'launch.json');confirmed=read(native/'CONFIRMED_NATIVE_RESULT.json');prep=read(preparation/'PURE_PREPARATION.json')
    require(identity['request_sha256']==sha(native/'request.json'),'SCUC request/event identity mismatch')
    require(confirmed['response_sha256']==sha(native/'response.json')==expected_response_sha,'SCUC response/event identity mismatch')
    require(launch['attempt']==1 and launch['pid']==confirmed['pid']and confirmed['returncode']==0,'SCUC launch/result mismatch')
    require(identity['preparation_sha256']==sha(preparation/'PURE_PREPARATION.json'),'SCUC preparation mismatch')
    require(sha(postreview)==expected_post_sha,'SCUC postreview mismatch')
    for name,h in prep['artifact_hashes'].items():require(sha(preparation/name)==h,'SCUC compression artifact mismatch')
    request=read(native/'request.json');template=read(preparation/'compact_template.json')
    strip=lambda x:{k:v for k,v in x.items()if k not in ('deadline','preparation_elapsed_s')}
    require(strip(request)==strip(template),'SCUC native request differs from prepared compression')
    paths=[native/n for n in ('request.json','response.json','IDENTITY.json','launch.json','CONFIRMED_NATIVE_RESULT.json')]+[preparation/n for n in ('PURE_PREPARATION.json','compression_plan.json','compact_template.json')]+[postreview]
    return dict(SCUC_request_sha256=sha(native/'request.json'),SCUC_response_sha256=sha(native/'response.json'),SCUC_event_pins={str(p):sha(p)for p in paths},SCUC_event_pair_verified=True,equivalent_lift_interpretation='compressed native request/response same launch; full original math request is distinct, connected by pinned preparation/compression/postreview')
