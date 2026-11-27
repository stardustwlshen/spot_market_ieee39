"""Print short source locators, not rule interpretations. Run: python probe.py [draft.pdf]."""
import bisect
import hashlib
import re
import sys
import unicodedata
from pathlib import Path
from urllib.request import Request, urlopen

import pymupdf

URL = "https://mfs.mysteelcdn.com/group1/M00/7A/DB/rBL64GrqyT-AcGDEACBH_hBshK8829.pdf"
SHA = "05cda5cabc552a8c8b8e5a58686f3a79684d8e24bf97255d00d8b3246d16e0b5"
data = (Path(sys.argv[1]).read_bytes() if len(sys.argv) > 1 else
        urlopen(Request(URL, headers={"User-Agent": "Mozilla/5.0"}), timeout=60).read())
digest = hashlib.sha256(data).hexdigest()
doc = pymupdf.open(stream=data, filetype="pdf")
print(f"PDF bytes={len(data)} pages={len(doc)} SHA256={digest}")
if digest != SHA or len(doc) != 212:
    sys.exit("Byte identity does not match the reviewed 212-page file.")
# (search topic, distinctive anchor regex, other terms that distinguish operative text).
TOPICS = [
    ("01 计划单元和省调水电", r"省调直调水电|水电(?:机组|厂站|企业)", ["计划单元", "报量报价", "网调"]),
    ("02 气电保供计划", r"燃气(?:发电)?(?:机组|电厂)|气电(?:机组|企业|报送)", ["非市场", "计划", "8:30"]),
    ("03 普通集中式光伏与竞价光伏", r"竞价光伏|集中式光伏", ["报量", "报价", "6月", "10月"]),
    ("04 日前申报时点", r"9[：:]45|9时45分", ["日前", "申报", "截止", "运行日"]),
    ("05 水电报价段与价格间距", r"150元|20元|报价段数|分段报价", ["水电", "报价", "段", "20%"]),
    ("06 水情、可发量和持续顶峰", r"持续顶峰|连续[三3]小时|[三3]小时顶峰", ["水电", "能力", "出力", "申报"]),
    ("07 日前预出清不结算", r"不(?:作为|纳入|参与|进行)[^。]{0,10}结算|不结算", ["日前", "预出清"]),
    ("08 日前机组组合和经济调度", r"安全约束机组组合|SCUC", ["经济调度", "日前", "出清", "SCED"]),
    ("09 水电实时耦合选择", r"水电[机厂站组企业]{0,4}[^。]{0,20}耦合|耦合出清", ["实时", "水电", "申报", "审核"]),
    ("10 实时滚动窗口与15分钟", r"未来[两2]小时|滚动出清|每15分钟", ["实时", "滚动", "15分钟", "小时"]),
    ("11 节点电价与统一结算点", r"统一结算点电价|节点边际电价", ["实时", "电量", "加权", "节点"]),
    ("12 调频占位与备用", r"调频容量|备用容量|调频中标", ["调频", "备用", "电能量", "出清"]),
    ("13 同价优化与分配", r"报价相同|同等报价|相同报价|同价", ["清洁能源", "比例", "容量", "出清"]),
    ("14 市场力缓解启动和上限", r"1[.]01|1[.]25|煤耗曲线", ["供需比", "缓解", "限价", "丰水期"]),
    ("15 煤机最小开停时间", r"72小时|120小时|最小(?:连续)?开机时间", ["停机", "24小时", "燃煤", "月"]),
]
# Preserve page and heading positions through wrapped Chinese prose. Repeated chapter
# numbering across annexes is disambiguated by the physical PDF page, starting at 1.
parts, starts, headings, offset = [], [], [], 0
for pageno, page in enumerate(doc, 1):
    starts.append(offset)
    for line in page.get_text("text", sort=True).splitlines():
        line = re.sub(r"\s+", "", unicodedata.normalize("NFKC", line))
        if not line or re.fullmatch(r"[-—]?\d+[-—]?", line):
            continue
        match = re.match(r"^(\d{1,2}(?:[.]\d{1,2}){1,3})(?![.\d])", line)
        if match and not re.search(r"[…·]{2,}|[.]{3,}", line):
            headings.append((offset, match[1], pageno))
        parts.append(line)
        offset += len(line)
body = "".join(parts)
head_offsets = [h[0] for h in headings]
print("All pages below are physical 1-based PDF pages; nearest numeric heading is a locator only.")
for topic, pattern, terms in TOPICS:
    candidates = []
    for m in re.finditer(pattern, body, re.I):
        pos = m.start()
        context = body[max(0, pos-105):m.end()+150]
        score = sum(term in context for term in terms)
        if len(re.findall(r"[…·]{2,}|[.]{3,}", context)) > 1:
            continue
        p = bisect.bisect_right(starts, pos)
        i = bisect.bisect_right(head_offsets, pos)-1
        nearby = headings[i] if i >= 0 and p-headings[i][2] <= 2 else None
        snip = body[max(starts[p-1], pos-10):min(starts[p] if p<len(starts) else len(body),pos+36)]
        candidates.append((-score, p, nearby[1] if nearby else "待核", snip))
    by_page = {}
    for score, p, section, snip in sorted(candidates):
        if score < 0 and p not in by_page:
            by_page[p] = f"PDF p.{p}, 近邻§{section}, {snip}"
        if len(by_page) == 2:
            break
    print(topic, " | ".join(by_page.values()) if by_page else "未命中，需人工查找", sep="  ")
