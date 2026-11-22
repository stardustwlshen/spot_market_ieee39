"""Pure policy primitives for the Sichuan V5.0 consultation draft.

This module does not optimize, publish prices or attest market access. Some
categories need verified inputs beyond the technology label. Missing inputs
are not silently substituted with permissions or an IEEE39 assumption.
"""

from dataclasses import dataclass
from enum import Enum
import math
from numbers import Real


class BidMode(str, Enum):
    QUANTITY_PRICE = "quantity_price"
    QUANTITY_ONLY = "quantity_only"
    NON_REPORTING = "non_reporting"
    NON_MARKET = "non_market"
    NOT_ELIGIBLE = "not_eligible"
    UNDETERMINED = "undetermined"


class Participant(str, Enum):
    PROVINCIAL_HYDRO = "provincial_hydro"
    PUBLIC_COAL = "public_coal"
    CENTRAL_RENEWABLE = "central_renewable"
    RENEWABLE_WITH_COLOCATED_STORAGE = "renewable_with_colocated_storage"
    POVERTY_PV = "poverty_pv"
    COMPETITIVE_PV = "competitive_pv"
    DISTRIBUTED_RENEWABLE = "distributed_renewable"
    GRID_DIRECT_PLANT = "grid_direct_plant"
    INDEPENDENT_STORAGE = "independent_storage"
    VPP_GENERATION = "vpp_generation"
    VPP_LOAD = "vpp_load"
    GREEN_DIRECT_LINE = "green_direct_line"
    POWER_USER = "power_user"
    RETAILER = "retailer"
    GAS = "gas"
    BIOMASS = "biomass"
    SECURITY_SUPPORT = "security_support"


class Product(str, Enum):
    SPOT = "spot"
    REGULATION = "regulation"
    RESERVE = "reserve"


class GridAllocation(str, Enum):
    ALL_IN_PROVINCE = "all_in_province"
    PARTLY_IN_PROVINCE = "partly_in_province"
    NONE_IN_PROVINCE = "none_in_province"


class Scope(str, Enum):
    V5_DRAFT = "v5_draft"
    NOV3_PUBLIC_SUMMARY = "nov3_public_summary"


@dataclass(frozen=True)
class ParticipationDecision:
    bid_mode: BidMode
    basis: str
    reason: str
    scope: Scope = Scope.V5_DRAFT
    quantity_is_fixed_boundary: bool = False


@dataclass(frozen=True)
class SeasonClassification:
    competitive_pv_bidding: str
    market_power: str
    nonrolling_contract: str
    market_power_scope: Scope = Scope.NOV3_PUBLIC_SUMMARY
    nonrolling_contract_scope: Scope = Scope.NOV3_PUBLIC_SUMMARY


def _month(month):
    if isinstance(month, bool) or not isinstance(month, int) or not 1 <= month <= 12:
        raise ValueError("月份须为 1–12 的整数")
    return month


def _check_optional_bool(value, label):
    if value is not None and type(value) is not bool:
        raise ValueError(label + "须为布尔值或未确认 (None)")


def classify_seasons(month):
    """Separate calendars. The risk/contract classifications are not spot bid limits.

    Competitive PV: V5.0 spot §2.5. Risk/contract calendars: Nov 3
    consultation summaries; keep provenance until original appendices verified.
    """
    month = _month(month)
    return SeasonClassification(
        competitive_pv_bidding="wet" if 6 <= month <= 10 else "dry",
        market_power="wet" if 5 <= month <= 10 else "dry",
        nonrolling_contract="wet" if 6 <= month <= 10 else ("normal" if month in (5, 11) else "dry"),
    )


def spot_bid_bounds():
    """Annex 3 Table 3-1: yuan/MWh, Pr1 and Pr2."""
    return (0.0, 800.0)


def spot_clear_bounds():
    """Annex 3 Table 3-1: yuan/MWh, PO1 and PO2 (not a dual-extraction method)."""
    return (0.0, 800.0)


def nonrolling_contract_bounds(month):
    """Nov 3 overall draft summary: annual/monthly/nonrolling intramonth cap.

    Not a general rule for high-consumption-user exceptions, market-power
    replacement bids, ancillary awards, or spot price bounds.
    """
    caps = {"wet": 211.43, "normal": 333.84, "dry": 415.63}
    return (0.0, caps[classify_seasons(month).nonrolling_contract])


def rolling_contract_bounds():
    """Nov 3 overall draft summary: rolling intramonth energy transaction bounds."""
    return (0.0, 800.0)


def participation_rule(participant, month, *, four_capabilities=None,
                       grid_allocation=None, provincial_mlt=None):
    participant = Participant(participant)
    season = classify_seasons(month)
    _check_optional_bool(four_capabilities, "四可")
    _check_optional_bool(provincial_mlt, "是否参与省内中长期")

    def decision(mode, basis, reason, *, fixed=False):
        return ParticipationDecision(mode, basis, reason, quantity_is_fixed_boundary=fixed)

    if participant in (
        Participant.PROVINCIAL_HYDRO, Participant.PUBLIC_COAL,
        Participant.CENTRAL_RENEWABLE, Participant.RENEWABLE_WITH_COLOCATED_STORAGE,
    ):
        return decision(BidMode.QUANTITY_PRICE, "V5.0 §2.5(1)",
                        "省调直调水电、公用燃煤火电、集中式新能源；新能源配储由联合主体申报。")
    if participant == Participant.POVERTY_PV:
        return decision(BidMode.QUANTITY_ONLY, "V5.0 §2.5(1)", "扶贫光伏报量不报价。")
    if participant == Participant.COMPETITIVE_PV:
        wet = season.competitive_pv_bidding == "wet"
        return decision(BidMode.QUANTITY_PRICE if wet else BidMode.QUANTITY_ONLY,
                        "V5.0 §2.5(1)", "竞价光伏仅 6–10 月报量报价，其余月份报量不报价。")
    if participant == Participant.DISTRIBUTED_RENEWABLE:
        if four_capabilities is None:
            return decision(BidMode.UNDETERMINED, "V5.0 §2.5(2)", "须确认可观、可测、可调、可控。")
        return decision(BidMode.QUANTITY_PRICE if four_capabilities else BidMode.NON_REPORTING,
                        "V5.0 §2.5(2)", "四可具备可报量报价；暂不具备时不报量不报价。")
    if participant == Participant.GRID_DIRECT_PLANT:
        allocation = None if grid_allocation is None else GridAllocation(grid_allocation)
        if provincial_mlt is False or allocation == GridAllocation.NONE_IN_PROVINCE:
            return decision(BidMode.NOT_ELIGIBLE, "V5.0 §2.1、§2.5(3)",
                            "此入口只覆盖参加省内中长期市场且有省内消纳的网调直调厂。")
        if provincial_mlt is None or allocation is None:
            return decision(BidMode.UNDETERMINED, "V5.0 §2.1、§2.5(3)",
                            "须明确省内中长期参与资格和省内消纳范围。")
        if allocation == GridAllocation.ALL_IN_PROVINCE:
            return decision(BidMode.QUANTITY_PRICE, "V5.0 §2.5(3)", "电量全额在省内消纳。")
        return decision(BidMode.NON_REPORTING, "V5.0 §2.5(3)",
                        "部分留川，以网调制定送四川曲线作为省内出清边界。", fixed=True)
    if participant == Participant.INDEPENDENT_STORAGE:
        return decision(BidMode.QUANTITY_ONLY, "V5.0 §2.5(4)、§7.7.4",
                        "初期申报充放电曲线，实时可申请调整，非自主价格报价。")
    if participant == Participant.VPP_GENERATION:
        if four_capabilities is None:
            return decision(BidMode.UNDETERMINED, "V5.0 §2.5(5)", "发电类聚合单元须确认四可。")
        if not four_capabilities:
            return decision(BidMode.NOT_ELIGIBLE, "V5.0 §2.5(5)", "发电类聚合单元入市须具备四可。")
        return decision(BidMode.QUANTITY_ONLY, "V5.0 §2.5(5)", "发电类聚合单元分别申报发电曲线。")
    if participant == Participant.VPP_LOAD:
        return decision(BidMode.QUANTITY_ONLY, "V5.0 §2.5(5)", "负荷类聚合单元分别申报用电曲线。")
    if participant == Participant.GREEN_DIRECT_LINE:
        return decision(BidMode.QUANTITY_ONLY, "V5.0 §2.5(6)", "项目整体申报净交换功率曲线。")
    if participant in (Participant.POWER_USER, Participant.RETAILER):
        return decision(BidMode.NON_REPORTING, "V5.0 §2.5", "初期用户及售电公司不报量不报价。")
    if participant in (Participant.GAS, Participant.BIOMASS, Participant.SECURITY_SUPPORT):
        return decision(BidMode.NON_MARKET, "V5.0 §2.1、§7.5.12",
                        "暂不参加现货市场竞价，可按相关规则进入非市场发电计划。", fixed=True)
    raise AssertionError("未覆盖的参与类别")


def _finite(value, label):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise ValueError(label + "须为有限实数")
    return float(value)


def validate_spot_bid_price(price):
    """Validate only the draft spot price band; not a complete bid-curve check."""
    value = _finite(price, "电能量申报价")
    lo, hi = spot_bid_bounds()
    if not lo <= value <= hi:
        raise ValueError("现货电能量申报价须在 0–800 元/MWh")
    return value


def validate_storage_period(product, charge_mw=0.0, discharge_mw=0.0):
    """One independent-storage participation intent per interval (§3.4, §7.7.4).

    This validates the submitted spot schedule only, not actual response energy,
    full power/SOC limits, qualification for auxiliary products, or award rules.
    """
    choice = Product(product)
    charge = _finite(charge_mw, "计划充电功率")
    discharge = _finite(discharge_mw, "计划放电功率")
    if min(charge, discharge) < 0:
        raise ValueError("计划充放电功率均须非负")
    if choice != Product.SPOT and (charge != 0 or discharge != 0):
        raise ValueError("独立储能未选择现货的对应时段充放电计划须为 0")
    return choice


def validate_spot_submission(decision, submitted_mode, *, price=None):
    """Check the administrative mode, not complete curve/physical validation.

    No economic quote can be smuggled in as a zero bid for a quantity-only or
    non-reporting resource. Any optimization penalty/comparator is separate.
    """
    candidate = BidMode(submitted_mode)
    if decision.bid_mode in (BidMode.NON_MARKET, BidMode.NOT_ELIGIBLE, BidMode.UNDETERMINED):
        raise ValueError("该主体没有已确认的现货竞价申报入口")
    if candidate != decision.bid_mode:
        raise ValueError("申报方式与该主体及水期不符")
    if candidate != BidMode.QUANTITY_PRICE and price is not None:
        raise ValueError("当前申报方式不得填报电能量价格")
    if price is not None:
        validate_spot_bid_price(price)
    return candidate
