"""Deterministic, source-aligned Guangdong energy arithmetic, NOT a formal bill.

No solver, market-data fabrication, inferred eligibility, or interregional
optimization. See reconstruction/r14_settlement_implementation/README.md.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import re

RULE_URL = 'https://nxepta.com/attached/file/20260205/20260205011306600.pdf'
RULE_SHA256 = '768c06ce2b77c958956e3b5ae8ca51de82b2712f78a1247b722ba845b795d263'
CAVEAT = ('Reposted provincial 2026 text, not official-domain original; §6.5 '
          'defers to regional rules and provincial transition documents. '
          'Current applicability requires external validation. Never a complete bill.')
CITATIONS = {
    'contract': 'Guangdong §7.2.3–7.2.4 print25–26/PDF27–28; §7.3 print26–30/PDF28–32',
    'DA': 'Guangdong §7.2.4 print26/PDF28; §7.3.1–7.3.2 print28–29/PDF30–31',
    'RT': 'Guangdong §7.2.5 print26/PDF28; §7.3 print26–30/PDF28–32',
    'renewable': 'Guangdong §7.4 print30/PDF32',
    'aggregation': 'Guangdong §5.3.3 print11/PDF13; regional §2.7.1.1 print84/PDF87 and §3.6.1.2 print141/PDF144',
    'quarter_contract': 'Guangdong §7.3.1–7.3.2 print28–29/PDF30–31',
    'ancillary': 'Guangdong chapter12 print102/PDF104',
}


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def aware(value):
    require(isinstance(value, datetime) and value.utcoffset() is not None, 'aware datetime required')
    return value


@dataclass(frozen=True)
class Evidence:
    """Caller-supplied provenance, not independently authenticated by this library."""
    status: str
    source_id: str = ''
    source_sha256: str = ''
    version: str = ''
    issued_at: datetime | None = None
    available_at: datetime | None = None
    missing_reason: str | None = None

    def validate(self, channel, asof):
        require(channel in ('formal_data', 'simulation'), 'explicit channel required')
        require(self.status in ('missing', 'official', 'observed', 'simulation'), 'invalid evidence status')
        if self.status == 'missing':
            require(bool(self.missing_reason), 'missing evidence needs reason')
            return
        require(not self.missing_reason, 'known evidence cannot have missing_reason')
        require(bool(self.source_id.strip()) and bool(self.version.strip()), 'source and version required, including known zero')
        require(bool(re.fullmatch('[0-9a-f]{64}', self.source_sha256)), 'source SHA256 required')
        require(aware(self.issued_at) <= aware(self.available_at) <= aware(asof), 'unavailable or reversed provenance time')
        require(channel != 'formal_data' or self.status != 'simulation', 'simulation cannot enter formal_data')


@dataclass(frozen=True)
class Value:
    value: Decimal | None
    unit: str
    evidence: Evidence

    def check(self, unit, channel, asof):
        require(self.unit == unit, f'expected unit {unit}, got {self.unit}')
        self.evidence.validate(channel, asof)
        require((self.value is None) == (self.evidence.status == 'missing'), 'null and missing status must agree')
        if self.value is not None:
            require(isinstance(self.value, Decimal) and self.value.is_finite(), 'finite Decimal required')
        return self.value


@dataclass(frozen=True)
class Attestation:
    value: bool | None
    evidence: Evidence

    def check(self, channel, asof):
        self.evidence.validate(channel, asof)
        require(self.value is None or type(self.value) is bool, 'Boolean or unknown required')
        require((self.value is None) == (self.evidence.status == 'missing'), 'qualification null/status mismatch')
        if channel == 'formal_data' and self.value is not None:
            require(self.evidence.status == 'official', 'official attestation required')
        return self.value


@dataclass(frozen=True)
class Key:
    resource_id: str
    settlement_unit_id: str
    node_id: str
    direction: str
    start: datetime
    end: datetime

    def validate(self):
        require(all(isinstance(x, str) and x.strip() for x in (self.resource_id, self.settlement_unit_id, self.node_id)), 'resource/unit/node identifiers required')
        require(self.direction in ('charge', 'discharge'), 'signed directional ledger required')
        require(aware(self.end) - aware(self.start) in (timedelta(hours=1), timedelta(minutes=15)), 'hour or quarter interval required')
        require(self.start.minute % 15 == 0 and self.start.second == self.start.microsecond == 0, 'quarter alignment required')
        if self.end-self.start == timedelta(hours=1):
            require(self.start.minute == 0, 'hour alignment required')


@dataclass(frozen=True)
class Award:
    market: str
    run_id: str
    pricing_run_id: str
    version: str
    publication_id: str
    key: Key
    price: Value
    quantity: Value | None = None
    price_publication_status: str | None = None

    @property
    def immutable_reference(self):
        # Content-addressed references, never mutated by a subsequent RT object.
        return sha256(repr(self).encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class Contract:
    contract_id: str
    reference_node_id: str
    quantity: Value
    strike: Value
    reference_price: Value
    hourly_quantity: Value | None = None


@dataclass(frozen=True)
class SettlementBasis:
    """External verification of exact meter/node/contract scope and tax basis."""
    meter_id: str
    auxiliary_consumption_basis: str
    loss_basis: str
    tax_basis: str
    node_interval_mapping_id: str
    contract_scope_id: str
    validated: Attestation

    def check(self, channel, asof):
        require(all(isinstance(v, str) and v.strip() for v in (
            self.meter_id, self.auxiliary_consumption_basis, self.loss_basis,
            self.tax_basis, self.node_interval_mapping_id, self.contract_scope_id)),
            'complete meter/loss/tax/node/contract scope basis required')
        return self.validated.check(channel, asof)


@dataclass(frozen=True)
class StatementAmount:
    amount: Value
    official_statement_id: str
    effective_rule_id: str
    validated: Attestation


def unknown(unit, reason):
    return Value(None, unit, Evidence('missing', missing_reason=reason))


def aggregate_hour(quarters, *, kind, channel, asof):
    """Four ordered (start, Value) pairs: price mean or energy sum. No weighting."""
    require(type(quarters) is tuple and len(quarters) == 4, 'exactly four quarters required')
    require(kind in ('price', 'energy'), 'price or energy required')
    start = aware(quarters[0][0])
    require(start.minute == start.second == start.microsecond == 0, 'hour alignment required')
    unit = 'CNY/MWh' if kind == 'price' else 'MWh'
    values, missing = [], []
    for i, (stamp, value) in enumerate(quarters):
        require(aware(stamp) == start + timedelta(minutes=15*i), 'quarters must be contiguous and ordered')
        v = value.check(unit, channel, asof)
        if kind == 'price' and channel == 'formal_data' and v is not None:
            require(value.evidence.status == 'official', 'formal price aggregation requires official provenance')
        if v is None:
            missing.append(value.evidence.missing_reason)
        else:
            values.append(v)
    return {'value': None if missing else sum(values, Decimal(0))/(4 if kind == 'price' else 1),
            'unit': unit, 'missing_reason': missing, 'source_ids': tuple(v.evidence.source_id for _, v in quarters),
            'channel': channel, 'rule': CITATIONS['aggregation']}


def split_hourly_contract(quantity, *, eligible, channel, asof):
    """Signed directional hourly quantity /4, not actual-volume allocation."""
    q = quantity.check('MWh', channel, asof)
    ok = eligible.check(channel, asof)
    if ok is not True:
        return tuple(unknown('MWh', '15-minute settlement eligibility not established') for _ in range(4))
    if q is None:
        return (quantity,) * 4
    return tuple(Value(q/4, 'MWh', quantity.evidence) for _ in range(4))


def calculate(*, key, settlement_class, classification, applicability, quarter_eligibility,
              channel, asof, rt, meter, da=None, contracts=None,
              base=None, agency_export=None, reserve=None, agc=None, adjustment=None, basis=None):
    """One exact directional interval. formal_data means gated arithmetic only.

    contracts=None is unknown; empty tuples are forbidden (use a sourced zero
    contract). Independent storage requires separate calls/units for each direction.
    No function accepts an extra congestion charge: it is already in contract.
    """
    key.validate(); aware(asof)
    require(settlement_class in ('generator', 'independent_storage', 'price_taking_renewable'), 'unsupported settlement class')
    classification_ok = classification.check(channel, asof)
    application_ok = applicability.check(channel, asof)
    require(settlement_class == 'independent_storage' or key.direction == 'discharge', 'only storage supports charge ledger')
    gates = []
    if basis is not None:
        require(isinstance(basis, SettlementBasis), 'typed settlement basis required')
        if basis.check(channel, asof) is not True:
            gates.append('meter/loss/tax/node/contract scope basis unvalidated')
    elif channel == 'formal_data':
        gates.append('meter/loss/tax/node/contract scope basis unavailable')
    if classification_ok is not True:
        gates.append('settlement classification not established; technology is insufficient')
    if application_ok is not True:
        gates.append('current provincial/regional rule applicability not established')
    if key.end-key.start == timedelta(minutes=15):
        require(settlement_class == 'independent_storage', 'quarter-hour settlement only supported for qualified storage')
        if quarter_eligibility is None or quarter_eligibility.check(channel, asof) is not True:
            gates.append('15-minute settlement eligibility not established')
    def get(v, unit, name, signed=False, official=False):
        if v is None:
            return None, [name + ': not supplied']
        x = v.check(unit, channel, asof)
        if x is None:
            return None, [name + ': ' + v.evidence.missing_reason]
        if official and channel == 'formal_data':
            require(v.evidence.status == 'official', name + ': official provenance required')
        if signed:
            require(x >= 0 if key.direction == 'discharge' else x <= 0, name + ': wrong directional sign')
        return x, []
    def award(a, market):
        if a is None:
            return None, [market + ' award unavailable']
        require(isinstance(a, Award) and a.market == market and a.key == key, 'exact market/node/unit/interval award required')
        require(all(isinstance(s, str) and s.strip() for s in (a.run_id, a.pricing_run_id, a.version, a.publication_id)), 'award run/version/publication required')
        require(a.price_publication_status in (None, 'provisional', 'final'), 'invalid price publication status')
        price, errors = get(a.price, 'CNY/MWh', market+' price', official=True)
        if channel == 'formal_data':
            require(a.price_publication_status != 'provisional', 'provisional prices cannot enter formal_data')
            if a.price_publication_status is None:
                errors += [market + ' final price publication status unavailable']
        return price, errors
    pr, er = award(rt, 'RT')
    qm, em = get(meter, 'MWh', 'meter', signed=True)
    if channel == 'formal_data' and meter is not None and meter.value is not None:
        require(meter.evidence.status in ('observed', 'official'), 'validated metering provenance required')
    rows = []
    def row(name, val, missing, rule):
        reasons = list(gates) + list(missing)
        rows.append({'component': name, 'amount_CNY': None if reasons else val,
                     'status': 'missing' if reasons else ('simulation_only' if channel == 'simulation' else 'formal_data_arithmetic_only'),
                     'missing_reason': reasons, 'rule': rule})
    if settlement_class == 'price_taking_renewable':
        require(da is None and contracts is None and base is None and agency_export is None, 'price-taking renewable is actual RT, not three-part settlement')
        row('renewable_actual_RT', None if em+er else qm*pr, em+er, CITATIONS['renewable'])
    else:
        pd, ed = award(da, 'DA')
        if da is not None and rt is not None:
            require(da.run_id != rt.run_id and da.pricing_run_id != rt.pricing_run_id, 'DA and RT run identities must be distinct')
        qd, eq = get(None if da is None else da.quantity, 'MWh', 'DA quantity', signed=True, official=True)
        cs, ce, cq = Decimal(0), [], Decimal(0)
        if contracts is None:
            ce = ['contracts unknown']
        else:
            require(type(contracts) is tuple and len(contracts)>0, 'explicit sourced zero contract required instead of empty set')
            require(len({c.contract_id for c in contracts}) == len(contracts), 'duplicate contract')
            for c in contracts:
                require(c.contract_id.strip() and c.reference_node_id.strip(), 'contract/reference identifiers required')
                q, e1 = get(c.quantity, 'MWh', 'contract quantity', signed=True, official=True)
                k, e2 = get(c.strike, 'CNY/MWh', 'contract strike', official=True)
                ref, e3 = get(c.reference_price, 'CNY/MWh', 'contract reference', official=True)
                if key.end-key.start == timedelta(minutes=15):
                    hq, he = get(c.hourly_quantity, 'MWh', 'source hourly contract quantity', signed=True, official=True)
                    if not he and not e1:
                        require(q == hq/4, 'quarter contract must equal source hourly quantity /4')
                    e1 += he
                ce += e1+e2+e3
                if not e1:
                    cq += q
                if not e1+e2+e3+ed:
                    cs += q*(k+pd-ref)
        row('contract_including_congestion', cs, ce+ed, CITATIONS['contract'])
        deductions, de = Decimal(0), []
        if settlement_class == 'generator':
            for name, value in (('base', base), ('agency_and_grid_export', agency_export)):
                v, e = get(value, 'MWh', name, signed=True, official=True)
                de += e
                if not e:
                    deductions += v
        else:
            require(base is None and agency_export is None, 'storage formula has no generator base/agency deductions')
        row('DA', None if eq+ce+ed+de else (qd-cq-deductions)*pd, eq+ce+ed+de, CITATIONS['DA'])
        row('RT', None if em+eq+er else (qm-qd)*pr, em+eq+er, CITATIONS['RT'])
    energy_rows = tuple(rows)
    for name, statement in (('reserve', reserve), ('AGC', agc), ('adjustment', adjustment)):
        if statement is None:
            row(name, None, ['authoritative validated statement amount not supplied'], CITATIONS['ancillary'] if name != 'adjustment' else 'Guangdong §7.2.1 and §7.3')
            continue
        require(isinstance(statement, StatementAmount), 'typed authoritative statement required')
        require(statement.official_statement_id.strip() and statement.effective_rule_id.strip(), 'official statement/rule identifiers required')
        ok = statement.validated.check(channel, asof)
        v, e = get(statement.amount, 'CNY', name, official=True)
        if ok is not True:
            e += ['statement validation not established']
        if name == 'adjustment' and settlement_class == 'independent_storage' and key.direction == 'charge':
            require(v in (None, Decimal(0)), 'storage allocation/refund belongs to discharge unit')
        row(name, v, e, statement.effective_rule_id)
    subtotal = None if any(r['amount_CNY'] is None for r in energy_rows) else sum((r['amount_CNY'] for r in energy_rows), Decimal(0))
    # Retain every immutable input, rather than collapsing provenance to one label.
    return {'key': key, 'channel': channel, 'rows': tuple(rows), 'energy_subtotal_CNY': subtotal,
            'full_bill_CNY': None, 'full_bill_missing_reason': 'not a full billing engine; monthly scope and other charges unverified',
            'DA_award_reference': None if da is None else da.immutable_reference,
            'RT_award_reference': None if rt is None else rt.immutable_reference,
            'inputs': {'meter': meter, 'DA': da, 'RT': rt, 'contracts': contracts, 'base': base,
                       'agency_export': agency_export, 'classification': classification, 'applicability': applicability,
                       'quarter_eligibility': quarter_eligibility, 'basis': basis, 'reserve': reserve, 'agc': agc, 'adjustment': adjustment},
            'rule_url': RULE_URL, 'rule_sha256': RULE_SHA256, 'caveat': CAVEAT,
            'authenticity_independently_verified': False, 'official_bill': False}
