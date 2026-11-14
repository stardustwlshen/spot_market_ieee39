from pathlib import Path
from copy import deepcopy
import json,sys
import pytest
from pricing_math import build_local_price
import importlib.util
spec=importlib.util.spec_from_file_location("_r69_source_adapter",Path(__file__).with_name("prepare_batch.py"));adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter);Source=adapter.Source
B=Path(__file__).resolve().parent;R=B.parent
read=lambda p:json.loads(p.read_bytes())

def test_pure_local_interface_only():
 inp=read(R/'guangdong_r67_complete_tail_20261112/prepared_v1/LOCAL_W0_INPUT.json');before=deepcopy(inp)
 q,n,tr,base,bn=build_local_price(inp)
 assert inp==before and len(q['variables'])==1206 and len(q['rows'])==1821
 assert tr['RT_tail_dual_used']is False and tr['market_price']is None
 old=read(R/'guangdong_r64_same_source_price_20261112/batch_W0_v1/W0/template.json')
 for k in ('variables','rows','objective','options'):assert q[k]==old[k]

def test_tail_input_not_local():
 inp=read(R/'guangdong_r67_complete_tail_20261112/prepared_v1/LOCAL_W0_INPUT.json');inp['da_cells']*=2
 with pytest.raises(AssertionError):build_local_price(inp)

def test_old_bid_rejected():
 inp=read(R/'guangdong_r67_complete_tail_20261112/prepared_v1/LOCAL_W0_INPUT.json');next(iter(inp['renewable'].values()))['bid_proxy_yuan_mwh']=0
 with pytest.raises(AssertionError):build_local_price(inp)

def test_pure_fixture_not_actual_source():
 with pytest.raises(FileNotFoundError):Source(R/'guangdong_r67_complete_tail_20261112/prepared_v1')
