"""Role-separated October input and output identities, without solver claims.

Hashes prove local byte consistency, not the truth of a supplied role/source.
No function in this module upgrades a replay, registration, or backend label to
native computation. RT output bytes are bound only after their creation.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import math


class OctoberRunIdentityError(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise OctoberRunIdentityError(message)


def text(value, label):
    require(type(value) is str and bool(value.strip()), label + ': explicit nonempty text required')
    return value


def digest(value, label):
    require(type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value),
            label + ': lowercase SHA256 required')
    return value


def time_text(value, label):
    if type(value) is str:
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError as exc:
            raise OctoberRunIdentityError(label + ': invalid time') from exc
    require(isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None,
            label + ': timezone-aware time required')
    return value.astimezone(timezone.utc).isoformat()


def canonical_json_bytes(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                          allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, OverflowError) as exc:
        raise OctoberRunIdentityError('plain finite JSON required') from exc


def parse_json_bytes(raw):
    require(type(raw) is bytes and bool(raw), 'original immutable nonempty bytes required')
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate JSON member: ' + key)
            result[key] = value
        return result
    def bad(value):
        raise OctoberRunIdentityError('nonfinite JSON constant: ' + value)
    try:
        result = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=bad)
        require(type(result) is dict, 'JSON object required')
        canonical_json_bytes(result)
        return result
    except (UnicodeError, ValueError, TypeError) as exc:
        if isinstance(exc, OctoberRunIdentityError):
            raise
        raise OctoberRunIdentityError('strict finite UTF-8 JSON required') from exc


def _typed(value):
    """Collision-resistant typed snapshot, including tuple/int dictionary keys.

    Null, false, zero, empty containers, list/tuple and bytes/text are distinct.
    In particular, an unresolved optional policy is never converted to zero.
    """
    if value is None:
        return ['none']
    if type(value) in (str, int, bool):
        return [type(value).__name__, value]
    if type(value) is float:
        require(math.isfinite(value), 'snapshot: finite float required')
        return ['float', value.hex()]
    if type(value) is bytes:
        return ['bytes', value.hex()]
    if isinstance(value, datetime):
        return ['datetime', time_text(value, 'snapshot time')]
    if type(value) in (list, tuple):
        return [type(value).__name__, [_typed(x) for x in value]]
    if type(value) is dict:
        rows = [(_typed(k), _typed(v)) for k, v in value.items()]
        rows.sort(key=lambda row: canonical_json_bytes(row[0]))
        return ['dict', rows]
    if is_dataclass(value) and not isinstance(value, type):
        return ['dataclass', type(value).__module__, type(value).__qualname__,
                [[f.name, _typed(getattr(value, f.name))] for f in fields(value)]]
    raise OctoberRunIdentityError('snapshot: unsupported object type ' + type(value).__name__)


def snapshot_bytes(value):
    return canonical_json_bytes(_typed(value))


@dataclass(frozen=True)
class KnownDispatchReference:
    """Explicit externally supplied role declaration plus exact known bytes.

    ``source`` identifies the basis for the declaration, not a role inferred
    from a filename. Authenticity and reproduction of these values are not
    established here. No default role or automatic legacy conversion exists.
    """
    role: str
    run_id: str
    window_id: str
    dispatch_id: str
    source_id: str
    source_sha256: str
    artifact: bytes
    source: str
    published_at: str
    available_at: str

    def validate(self):
        require(type(self.role) is str and self.role in {'day_ahead_selected_dispatch', 'effective_baseline_dispatch'},
                'known dispatch: explicit supported known DA/effective baseline role required')
        for name in ('run_id', 'window_id', 'dispatch_id', 'source_id', 'source'):
            text(getattr(self, name), 'known dispatch.' + name)
        digest(self.source_sha256, 'known dispatch source hash')
        require(type(self.artifact) is bytes and bool(self.artifact), 'known dispatch: original immutable bytes required')
        require(sha256(self.artifact).hexdigest() == self.source_sha256, 'known dispatch: source hash mismatch')
        published = time_text(self.published_at, 'known dispatch publication')
        available = time_text(self.available_at, 'known dispatch availability')
        require(published <= available, 'known dispatch: publication must precede availability')
        # Store immutable, normalized timestamp strings, even if callers used datetime.
        return KnownDispatchReference(self.role, self.run_id, self.window_id, self.dispatch_id,
            self.source_id, self.source_sha256, self.artifact, self.source, published, available)

    def as_dict(self):
        ref = self.validate()
        return {f.name: getattr(ref, f.name) for f in fields(ref) if f.name != 'artifact'}


@dataclass(frozen=True)
class PreSolveIdentity:
    run_id: str
    window_id: str
    known_dispatch_reference: KnownDispatchReference
    input_bundle_sha256: str
    solve_intent_sha256: str
    physical_model_sha256: str
    delivery_start: str
    delivery_end: str
    information_cutoff: str

    def validate(self):
        text(self.run_id, 'input run'); text(self.window_id, 'input window')
        require(isinstance(self.known_dispatch_reference, KnownDispatchReference), 'typed known reference required')
        known = self.known_dispatch_reference.validate()
        require(known.run_id == self.run_id and known.window_id == self.window_id,
                'pre-solve known-reference run/window mismatch')
        for name in ('input_bundle_sha256', 'solve_intent_sha256', 'physical_model_sha256'):
            digest(getattr(self, name), name)
        start = time_text(self.delivery_start, 'input delivery start')
        end = time_text(self.delivery_end, 'input delivery end')
        cutoff = time_text(self.information_cutoff, 'input cutoff')
        require(known.available_at <= cutoff <= start < end, 'pre-solve input time ordering mismatch')
        return self

    def as_dict(self):
        self.validate()
        return {f.name: (self.known_dispatch_reference.as_dict() if f.name == 'known_dispatch_reference'
                         else getattr(self, f.name)) for f in fields(self)}


@dataclass(frozen=True)
class RTResultReference:
    """Byte/lineage binding only; not a native-solve or feasibility certificate."""
    run_id: str
    window_id: str
    selected_dispatch_id: str
    selected_dispatch_sha256: str
    artifact: bytes
    input_identity: PreSolveIdentity

    @property
    def role(self):
        return 'realtime_selected_dispatch'

    def as_dict(self):
        return dict(role=self.role, run_id=self.run_id, window_id=self.window_id,
                    selected_dispatch_id=self.selected_dispatch_id,
                    selected_dispatch_sha256=self.selected_dispatch_sha256,
                    input_bundle_sha256=self.input_identity.input_bundle_sha256,
                    solve_intent_sha256=self.input_identity.solve_intent_sha256,
                    physical_model_sha256=self.input_identity.physical_model_sha256,
                    known_dispatch_reference=self.input_identity.known_dispatch_reference.as_dict())


def bind_rt_result(identity, *, selected_dispatch_id, artifact):
    """Bind a finished RT artifact to its pre-solve identity, without solving.

    The artifact must repeat the exact lineage header. The output hash is never
    included in its own bytes or compared with the known DA/baseline hash.
    """
    require(isinstance(identity, PreSolveIdentity), 'typed pre-solve identity required')
    identity.validate()
    text(selected_dispatch_id, 'RT selected id')
    data = parse_json_bytes(artifact)
    expected = {k: identity.as_dict()[k] for k in ('run_id', 'window_id', 'known_dispatch_reference',
                'input_bundle_sha256', 'solve_intent_sha256', 'physical_model_sha256')}
    expected['selected_dispatch_id'] = selected_dispatch_id
    for key, value in expected.items():
        require(data.get(key) == value, 'RT result lineage mismatch: ' + key)
    return RTResultReference(identity.run_id, identity.window_id, selected_dispatch_id,
                             sha256(artifact).hexdigest(), artifact, identity)


def assert_rt_result_binding(reference, *, expected_input_identity, expected_identity=None):
    require(isinstance(reference, RTResultReference), 'typed RT result reference required; replay is not a new result')
    require(reference.input_identity == expected_input_identity, 'RT result: different pre-solve input identity')
    checked = bind_rt_result(expected_input_identity, selected_dispatch_id=reference.selected_dispatch_id,
                             artifact=reference.artifact)
    require(checked == reference, 'RT result reference/hash mismatch')
    identity = dict(run_id=reference.run_id, window_id=reference.window_id,
                    selected_dispatch_id=reference.selected_dispatch_id,
                    selected_dispatch_sha256=reference.selected_dispatch_sha256)
    if expected_identity is not None:
        require(type(expected_identity) is dict and expected_identity == identity,
                'RT result: expected run/window/selection mismatch')
    return dict(identity=identity, rt_result_binding_verified=True, input_identity=expected_input_identity.as_dict(),
                native_solver_verified=False, source_authenticity_verified=False,
                numerical_feasibility_verified=False, official_algorithm_certified=False)
