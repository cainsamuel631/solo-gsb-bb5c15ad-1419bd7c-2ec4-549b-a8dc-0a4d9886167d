"""Transitive license constraint analysis.

For every ordered ancestor/descendant pair ``(a, b)`` reachable in the
dependency graph we ask the compatibility matrix whether code under ``a``'s
license may incorporate code under ``b``'s license.  Every non-allow result
is reported together with the *shortest* dependency path ``a -> ... -> b``
(BFS), which is exactly the chain that drags an incompatible license into
the consuming package.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from .licenses import CATEGORY_OF, evaluate
from .models import NormalizedManifest

VERDICT_ORDER = {"conflict": 0, "unknown": 1, "allow": 2}
SEVERITY_LABEL = {"conflict": "conflict", "unknown": "review", "allow": "ok"}


@dataclass
class Finding:
    consumer: str
    dependency: str
    consumer_license: Optional[str]
    dependency_license: Optional[str]
    verdict: str
    reason: Optional[str]
    path: List[str]
    distance: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "consumer": self.consumer,
            "dependency": self.dependency,
            "consumer_license": self.consumer_license,
            "dependency_license": self.dependency_license,
            "verdict": self.verdict,
            "severity": SEVERITY_LABEL[self.verdict],
            "reason": self.reason,
            "path": self.path,
            "path_edges": [
                {"from": self.path[i], "to": self.path[i + 1]}
                for i in range(len(self.path) - 1)
            ],
            "distance": self.distance,
            "transitive": self.distance > 1,
        }


def _adjacency(man: NormalizedManifest) -> Dict[str, List[str]]:
    adj: Dict[str, List[str]] = {name: [] for name in man.node_order}
    for edge in man.edges:
        adj.setdefault(edge.src, []).append(edge.dst)
        adj.setdefault(edge.dst, [])
    return adj


def _tarjan_cycles(adj: Dict[str, List[str]]) -> List[List[str]]:
    """Return strongly connected components of size > 1 (and self loops)."""
    index_counter = [0]
    stack: List[str] = []
    on_stack: Set[str] = set()
    indices: Dict[str, int] = {}
    lowlink: Dict[str, int] = {}
    cycles: List[List[str]] = []

    def strongconnect(v: str) -> None:
        indices[v] = index_counter[0]
        lowlink[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack.add(v)

        for w in adj.get(v, []):
            if w not in indices:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], indices[w])

        if lowlink[v] == indices[v]:
            component: List[str] = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                component.append(w)
                if w == v:
                    break
            if len(component) > 1 or (len(component) == 1 and v in adj.get(v, [])):
                cycles.append(list(reversed(component)))

    for node in adj:
        if node not in indices:
            strongconnect(node)
    return cycles


def _shortest_paths_tree(
    adj: Dict[str, List[str]], source: str
) -> Dict[str, Optional[str]]:
    """BFS predecessor map from ``source`` to every reachable node."""
    parent: Dict[str, Optional[str]] = {source: None}
    queue = deque([source])
    while queue:
        cur = queue.popleft()
        for nxt in adj.get(cur, []):
            if nxt not in parent:
                parent[nxt] = cur
                queue.append(nxt)
    return parent


def _reconstruct(parent: Dict[str, Optional[str]], target: str) -> List[str]:
    path: List[str] = []
    cur: Optional[str] = target
    while cur is not None:
        path.append(cur)
        cur = parent[cur]
    path.reverse()
    return path


def analyze(man: NormalizedManifest) -> Dict[str, Any]:
    adj = _adjacency(man)
    nodes = man.nodes

    # --- Cycle detection --------------------------------------------------
    sccs = _tarjan_cycles(adj)
    cycles: List[List[str]] = []
    cyclic_nodes: Set[str] = set()
    for comp in sccs:
        ordered = _order_cycle(comp, adj)
        cycles.append(ordered)
        cyclic_nodes.update(ordered)

    # --- Transitive pair evaluation with shortest dependency paths --------
    findings: List[Finding] = []
    reachable_from: Dict[str, Set[str]] = {}

    for source in man.node_order:
        parent = _shortest_paths_tree(adj, source)
        reachable = {n for n in parent if n != source}
        reachable_from[source] = reachable

        src_license = nodes[source].license
        for target in reachable:
            verdict, reason = evaluate(src_license, nodes[target].license)
            if verdict == "allow":
                continue
            path = _reconstruct(parent, target)
            findings.append(
                Finding(
                    consumer=source,
                    dependency=target,
                    consumer_license=src_license,
                    dependency_license=nodes[target].license,
                    verdict=verdict,
                    reason=reason,
                    path=path,
                    distance=len(path) - 1,
                )
            )

    findings.sort(
        key=lambda f: (
            VERDICT_ORDER[f.verdict],
            f.distance,
            f.consumer,
            f.dependency,
        )
    )

    # --- Per-node summary -------------------------------------------------
    node_summaries: Dict[str, Dict[str, Any]] = {}
    for name in man.node_order:
        node = nodes[name]
        outgoing = sorted(reachable_from.get(name, set()))
        own_findings = [f for f in findings if f.consumer == name]
        worst = "allow"
        if any(f.verdict == "conflict" for f in own_findings):
            worst = "conflict"
        elif any(f.verdict == "unknown" for f in own_findings):
            worst = "unknown"
        node_summaries[name] = {
            "name": name,
            "version": node.version,
            "license": node.license,
            "license_raw": node.license_raw,
            "license_recognized": node.license_recognized,
            "category": CATEGORY_OF.get(node.license) if node.license else None,
            "implicit": node.implicit,
            "in_cycle": name in cyclic_nodes,
            "is_root": name in man.roots,
            "verdict": worst,
            "depends_on": outgoing,
            "depended_by": sorted(
                [other for other in man.node_order if name in reachable_from.get(other, set())]
            ),
            "finding_count": len(own_findings),
            "conflict_count": sum(1 for f in own_findings if f.verdict == "conflict"),
            "unknown_count": sum(1 for f in own_findings if f.verdict == "unknown"),
        }

    summary = {
        "total_nodes": len(nodes),
        "total_edges": len(man.edges),
        "total_findings": len(findings),
        "conflicts": sum(1 for f in findings if f.verdict == "conflict"),
        "unknown": sum(1 for f in findings if f.verdict == "unknown"),
        "cycles": len(cycles),
        "cyclic_nodes": sorted(cyclic_nodes),
        "warnings": len(man.warnings),
    }

    return {
        "findings": [f.to_dict() for f in findings],
        "nodes": [node_summaries[name] for name in man.node_order],
        "cycles": cycles,
        "warnings": list(man.warnings),
        "roots": list(man.roots),
        "summary": summary,
    }


def _order_cycle(component: List[str], adj: Dict[str, List[str]]) -> List[str]:
    """Best-effort ordering of an SCC as an actual walk (closes back)."""
    members = set(component)
    start = sorted(component)[0]
    path = [start]
    current = start
    visited = {start}
    while True:
        nxt = next((n for n in adj.get(current, []) if n in members and n not in visited), None)
        if nxt is None:
            break
        path.append(nxt)
        visited.add(nxt)
        current = nxt
    return path


def filter_report(
    report: Dict[str, Any],
    package: Optional[str] = None,
    severity: Optional[str] = None,
    scope: str = "any",
) -> Dict[str, Any]:
    """Filter findings (and, for a package filter, nodes) of a report.

    ``scope`` for a package filter:
      * ``any``      - package appears anywhere on the conflict path;
      * ``consumer`` - package is the consuming package;
      * ``dependency``- package is the offending dependency;
      * ``subtree``  - consumer or dependency lies in the package's subtree.
    """
    findings = report["findings"]

    sev = severity.lower() if severity else None
    if sev in ("review", "unknown"):
        sev = "unknown"
    if sev:
        findings = [f for f in findings if f["verdict"] == sev]

    if package:
        pkg = package.strip().lower()

        def in_path(f: Dict[str, Any]) -> bool:
            return pkg in [p.lower() for p in f["path"]]

        if scope == "consumer":
            findings = [f for f in findings if f["consumer"].lower() == pkg]
        elif scope == "dependency":
            findings = [f for f in findings if f["dependency"].lower() == pkg]
        elif scope == "subtree":
            node_map = {n["name"].lower(): n for n in report["nodes"]}
            target = node_map.get(pkg)
            subtree: Set[str] = set()
            if target:
                subtree = {target["name"], *target["depends_on"]}
            findings = [
                f
                for f in findings
                if f["consumer"] in subtree or f["dependency"] in subtree
            ]
        else:  # any
            findings = [f for f in findings if in_path(f)]

    filtered = dict(report)
    filtered["findings"] = findings
    filtered["filter"] = {
        "package": package,
        "severity": sev,
        "scope": scope,
    }
    filtered["summary"] = {
        **report["summary"],
        "total_findings": len(findings),
        "conflicts": sum(1 for f in findings if f["verdict"] == "conflict"),
        "unknown": sum(1 for f in findings if f["verdict"] == "unknown"),
    }
    return filtered
