"""Bid curve validation for §7.7.1–3 and Annex 3 of the November V5 draft."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable, Literal

Technology = Literal["coal", "run_of_river", "reservoir", "reservoir_curtailment", "renewable"]
D = Decimal


@dataclass(frozen=True)
class Segment:
    start_mw: Decimal
    end_mw: Decimal
    price_yuan_mwh: Decimal


def _decimal(v, name, places, lo=0, hi=None):
    try:
        result = D(str(v))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{name} 数值格式无效") from None
    if (not result.is_finite() or result < lo or (hi is not None and result > hi)
            or result != result.quantize(D(1).scaleb(-places))):
        raise ValueError(f"{name} 超出取值范围或精度")
    return result


def validate_curve(
    technology: Technology,
    maximum_mw,
    rows: Iterable[tuple],
    *,
    coal_min_adjustable_mw=None,
    coal_min_stable_mw=None,
    coal_flexibility_retrofit=False,
    coal_flex_block_price=None,
) -> tuple[Segment, ...]:
    """Return validated segments; include optional coal low-load block first.

    The regular reservoir's first 80% check counts each segment with positive
    overlap in that band. This validator does not apply risk replacement or
    infer trading eligibility. A renewable may offer all power at the floor.
    """
    if technology not in ("coal", "run_of_river", "reservoir", "reservoir_curtailment", "renewable"):
        raise ValueError("不支持的报价类型")
    ceiling = _decimal(maximum_mw, "报价容量", 2, lo=D(".01"))
    s = tuple(Segment(_decimal(a, "段起点", 2, hi=ceiling),
                      _decimal(b, "段终点", 2, hi=ceiling),
                      _decimal(p, "段报价", 0, hi=800)) for a, b, p in rows)
    if technology == "renewable" and s == (Segment(D(0), ceiling, D(0)),):
        return s
    if not 5 <= len(s) <= 10:
        raise ValueError("分段报价须为 5 至 10 段")
    if s[-1].end_mw != ceiling:
        raise ValueError("末段终点须等于规定最大上网电力或额定有功功率")
    first = s[0].start_mw
    low = None
    if technology == "coal":
        stable = _decimal(coal_min_stable_mw, "最小稳定出力", 2, hi=ceiling)
        adjustable = _decimal(coal_min_adjustable_mw, "最小可调出力", 2, hi=stable)
        if first not in ({stable, adjustable} if coal_flexibility_retrofit else {stable}):
            raise ValueError("燃煤首段起点不符合最小稳定/可调出力要求")
        if s[0].price_yuan_mwh < 85:
            raise ValueError("燃煤首段与申报下限价差须至少 85 元/MWh")
        if coal_flexibility_retrofit and first == stable and adjustable < stable:
            prior = _decimal(coal_flex_block_price, "低出力补充段", 0, lo=85,
                             hi=s[0].price_yuan_mwh)
            low = Segment(adjustable, stable, prior)
        elif coal_flex_block_price is not None:
            raise ValueError("当前燃煤报价起点不应另报低出力补充段")
    elif first != 0:
        raise ValueError("水电和新能源首段须从零开始")
    width = ceiling - first
    prev = None
    for segment in s:
        length = segment.end_mw - segment.start_mw
        if not width > 0 or not width * D(".05") <= length <= width * D(".20"):
            raise ValueError("各常规段跨度须为 5% 至 20%")
        if prev is not None:
            if segment.start_mw != prev.end_mw:
                raise ValueError("相邻报价段须连续")
            if not 20 <= segment.price_yuan_mwh - prev.price_yuan_mwh <= 150:
                raise ValueError("相邻常规段价差须为 20 至 150 元/MWh")
        prev = segment
    if technology == "reservoir" and sum(row.start_mw < ceiling * D(".80") for row in s) < 5:
        raise ValueError("水库电站前 80% 容量区间至少须有 5 个报价段")
    return (low, *s) if low is not None else s


def research_curve(technology: Technology, maximum_mw, first_price, *, coal_minimum_mw=0):
    """Generate six declared synthetic offer segments with the exact draft ticks.

    For integration with a solver that represents all output as price blocks,
    a coal minimum-output base block is priced at the first price for this
    teaching scenario only. It is not a declared energy-offer segment nor
    a prescribed minimum-output cost calculation.
    """
    maximum = _decimal(maximum_mw, "额定容量", 2, lo=D(".01"))
    base = _decimal(coal_minimum_mw if technology == "coal" else 0, "起点", 2, hi=maximum)
    first = _decimal(first_price, "首段价", 0, hi=700)
    points = [base + ((maximum - base) * i / 6).quantize(D(".01")) for i in range(7)]
    curve = validate_curve(technology, maximum,
                           ((a, b, first + i * 20)
                            for i, (a, b) in enumerate(zip(points, points[1:]))),
                           coal_min_adjustable_mw=base, coal_min_stable_mw=base)
    blocks = [(float(row.end_mw - row.start_mw), float(row.price_yuan_mwh)) for row in curve]
    if technology == "coal" and base:
        blocks.insert(0, (float(base), float(first)))
    return tuple(blocks)
