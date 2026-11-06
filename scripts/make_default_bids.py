"""生成缺省报价（v 自动递增），校验并另存 CSV。用法：python scripts/make_default_bids.py（统一规则 MKT）"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from smm.bids import bids_to_csv, default_bids, raise_on_errors, save_bids, validate_bids  # noqa: E402
from smm.config import load_config  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--season", default=None)
a = ap.parse_args()
cfg = load_config(season=a.season)
b = default_bids(cfg)
iss = validate_bids(cfg, b)
for i in iss:
    print(i)
raise_on_errors(iss)
p = save_bids(cfg, b, note=b.get("note", "缺省报价（假设值）"))
bids_to_csv(json.loads(p.read_text(encoding="utf-8")), p.with_suffix(".csv"))
print("已保存", p, "及", p.with_suffix(".csv"))
