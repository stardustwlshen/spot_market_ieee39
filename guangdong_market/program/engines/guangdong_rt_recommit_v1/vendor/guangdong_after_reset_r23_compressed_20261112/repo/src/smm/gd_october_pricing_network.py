"""Standalone lossless DC transfer factors, with no optimization dependencies.

A positive line flow is from ``from_node`` to ``to_node``. Each PTDF column
injects one MW at that node and withdraws one MW at ``reference_node``.
The reference column is identically zero. Parallel lines are supported.
"""
from __future__ import annotations

from collections.abc import Hashable, Mapping
import math


def _solve_many(matrix: list[list[float]], rhs: list[list[float]]) -> list[list[float]]:
    """Partial-pivot Gaussian elimination for several right-hand sides."""
    n = len(matrix)
    a = [row[:] + values[:] for row, values in zip(matrix, rhs)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(a[row][col]))
        if a[pivot][col] == 0.0:
            raise ValueError("singular DC network matrix")
        a[col], a[pivot] = a[pivot], a[col]
        for row in range(col + 1, n):
            ratio = a[row][col] / a[col][col]
            a[row][col] = 0.0
            for j in range(col + 1, 2 * n):
                a[row][j] -= ratio * a[col][j]
    result = [[0.0] * n for _ in range(n)]
    for row in range(n - 1, -1, -1):
        for column in range(n):
            result[row][column] = (
                a[row][n + column]
                - math.fsum(a[row][j] * result[j][column] for j in range(row + 1, n))
            ) / a[row][row]
            if not math.isfinite(result[row][column]):
                raise ValueError("nonfinite DC network solution")
    return result


def dc_ptdf(
    nodes: tuple[Hashable, ...],
    lines: Mapping[Hashable, Mapping[str, object]],
    reference_node: Hashable,
) -> dict[Hashable, dict[Hashable, float]]:
    """Return line-by-node PTDFs for a connected, positive-susceptance network.

    Node IDs must be unique and hashable. Line IDs are unique mapping keys;
    duplicated line IDs must be rejected by a source reader before constructing
    the mapping. Invalid topology or coefficients raise ``ValueError``.
    A single reference node and no lines is a valid empty network.
    """
    try:
        node_set = set(nodes)
    except TypeError as exc:
        raise ValueError("node IDs must be hashable") from exc
    if len(node_set) != len(nodes):
        raise ValueError("duplicate node IDs")
    if not nodes or reference_node not in node_set:
        raise ValueError("unknown reference node")
    neighbors = {node: set() for node in nodes}
    edges = []
    seen_lines = set()
    for line_id, line in lines.items():
        if line_id in seen_lines:
            raise ValueError("duplicate line IDs")
        seen_lines.add(line_id)
        try:
            source, target = line["from_node"], line["to_node"]
            raw_b = line["susceptance_mw_per_rad"]
            if isinstance(raw_b, bool):
                raise ValueError("boolean susceptance")
            b = float(raw_b)
            known = source in node_set and target in node_set
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"invalid line {line_id!r}") from exc
        if not known:
            raise ValueError(f"unknown endpoint for line {line_id!r}")
        if source == target:
            raise ValueError(f"self-loop line {line_id!r}")
        if not math.isfinite(b) or b <= 0:
            raise ValueError(f"susceptance must be finite and positive: {line_id!r}")
        edges.append((line_id, source, target, b))
        neighbors[source].add(target)
        neighbors[target].add(source)
    visited = {reference_node}
    pending = [reference_node]
    while pending:
        for neighbor in neighbors[pending.pop()]:
            if neighbor not in visited:
                visited.add(neighbor)
                pending.append(neighbor)
    if visited != node_set:
        raise ValueError("disconnected network islands")
    nonreference = [node for node in nodes if node != reference_node]
    index = {node: i for i, node in enumerate(nonreference)}
    n = len(nonreference)
    if not n:
        return {}
    # Common scaling avoids overflow in sums of large, finite susceptances.
    scale = max(edge[3] for edge in edges)
    matrix = [[0.0] * n for _ in range(n)]
    for _, source, target, b in edges:
        weight = b / scale
        for node in (source, target):
            if node in index:
                matrix[index[node]][index[node]] += weight
        if source in index and target in index:
            matrix[index[source]][index[target]] -= weight
            matrix[index[target]][index[source]] -= weight
    inverse = _solve_many(matrix, [[float(i == j) for j in range(n)] for i in range(n)])
    factors = {}
    for line_id, source, target, b in edges:
        factors[line_id] = {reference_node: 0.0}
        for node, column in index.items():
            source_angle = inverse[index[source]][column] if source in index else 0.0
            target_angle = inverse[index[target]][column] if target in index else 0.0
            value = (b / scale) * (source_angle - target_angle)
            if not math.isfinite(value):
                raise ValueError("nonfinite PTDF")
            factors[line_id][node] = value
    return factors


def normalize_topology_ptdf(nodes, lines, reference_node, raw_factors):
    """Exact bridge cutsets and passive articulation branches, not epsilon drop.

    A component attached at one vertex carries zero flow when it contains
    neither the injecting bus nor withdrawal reference. Bridge flow is exactly
    its cut-set net injection. Floating results must already agree to 1e-12;
    otherwise fail rather than mask a DC calculation/topology defect.
    """
    if set(raw_factors)!=set(lines) or any(set(row)!=set(nodes) for row in raw_factors.values()):
        raise ValueError('complete raw PTDF inventory required')
    if any(type(v)not in (int,float) or not math.isfinite(v)for row in raw_factors.values()for v in row.values()):
        raise ValueError('finite raw PTDF factors required')
    # Recheck positive connected topology independently of caller normalization.
    if not nodes or len(set(nodes))!=len(nodes) or reference_node not in nodes:raise ValueError('invalid topology nodes/reference')
    neighbors={n:set()for n in nodes}
    for l,d in lines.items():
        a,b=d['from_node'],d['to_node'];v=d['susceptance_mw_per_rad']
        if a not in neighbors or b not in neighbors or a==b or type(v)not in (int,float) or not math.isfinite(v) or v<=0:raise ValueError('positive finite connected DC topology required')
        neighbors[a].add(b);neighbors[b].add(a)
    seen={reference_node};todo=[reference_node]
    while todo:
        for a in neighbors[todo.pop()]-seen:seen.add(a);todo.append(a)
    if seen!=set(nodes):raise ValueError('connected DC topology required')
    def components(removed_node=None,removed_line=None):
        adjacency={n:set()for n in nodes if n!=removed_node}
        for lid,d in lines.items():
            a,b=d['from_node'],d['to_node']
            if lid==removed_line or a==removed_node or b==removed_node:continue
            adjacency[a].add(b);adjacency[b].add(a)
        remaining=set(adjacency);parts=[]
        while remaining:
            todo=[min(remaining)];part=set(todo);remaining-=part
            while todo:
                for n in adjacency[todo.pop()]-part:
                    part.add(n);remaining.discard(n);todo.append(n)
            parts.append(part)
        return parts
    expected={};certificates={}
    for lid,d in lines.items():
        parts=components(removed_line=lid)
        if len(parts)==2:
            side=next(p for p in parts if reference_node not in p)
            sign=1. if d['from_node'] in side else -1.
            for n in nodes:
                expected[lid,n]=sign if n in side else 0.
                certificates[lid,n]=dict(kind='bridge_cutset',removed_line=lid,component=sorted(side))
    for a in nodes:
        for side in components(removed_node=a):
            if reference_node in side:continue
            for lid,d in lines.items():
                if {d['from_node'],d['to_node']}<=side|{a}:
                    for n in set(nodes)-side:
                        if (lid,n)in expected and expected[lid,n]!=0.:raise ValueError('inconsistent exact topology certificate')
                        expected[lid,n]=0.
                        certificates.setdefault((lid,n),dict(kind='passive_single_articulation',attachment=a,component=sorted(side)))
    result={l:dict(r)for l,r in raw_factors.items()};changes=[]
    for (l,n),value in sorted(expected.items()):
        old=raw_factors[l][n]
        if abs(old-value)>1e-12:raise ValueError('DC factors disagree with exact topology certificate')
        result[l][n]=value
        if old!=value:changes.append(dict(line=l,node=n,raw=old,normalized=value,certificate=certificates[l,n]))
    # No unproved tiny coefficient is silently discarded.
    if any(0<abs(v)<1e-9 for row in result.values()for v in row.values()):
        raise ValueError('unproved small PTDF coefficient cannot enter strict HiGHS matrix')
    return result,dict(method='topology_certified_zero_and_bridge_cutset_exact_values',verification_tolerance=1e-12,
        changes=changes,max_absolute_change=max([abs(x['raw']-x['normalized'])for x in changes]or [0.]),
        not_a_source_line_limit_or_slack_change=True,arbitrary_threshold_clipping=False)
