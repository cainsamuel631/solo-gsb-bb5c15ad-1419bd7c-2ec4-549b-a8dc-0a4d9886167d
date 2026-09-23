"""Manifest parsing and normalization.

Accepted JSON shapes
--------------------

1. Explicit nodes + edges::

       {
         "name": "my-app",
         "nodes": [
           {"name": "app",  "version": "1.0.0", "license": "MIT"},
           {"name": "libx", "version": "2.1",   "license": "GPL-3.0"}
         ],
         "edges": [
           {"from": "app", "to": "libx"},
           {"from": "app", "to": ["liby"]}
         ]
       }

2. Package-manager style (a flat list / dict where each package carries its
   own ``dependencies``)::

       {
         "packages": [
           {"name": "app", "version": "1.0.0", "license": "MIT",
            "dependencies": ["libx", {"name": "liby", "version": "0.4"}]}
         ]
       }

Dependency references that do not resolve to a declared node are materialized
as nodes with an unknown license, and a warning is emitted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from .licenses import normalize_license


class ManifestError(ValueError):
    """Raised when the uploaded manifest cannot be interpreted."""


@dataclass
class Node:
    name: str
    version: str = ""
    license_raw: Optional[str] = None
    license: Optional[str] = None
    license_recognized: bool = True
    implicit: bool = False  # created from an unresolved edge reference


@dataclass
class Edge:
    src: str
    dst: str


@dataclass
class NormalizedManifest:
    name: str
    nodes: Dict[str, Node] = field(default_factory=dict)
    edges: List[Edge] = field(default_factory=list)
    roots: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    node_order: List[str] = field(default_factory=list)


def _as_str(value: Any, field_name: str) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):  # pragma: no cover - defensive
        raise ManifestError(f"Field '{field_name}' must be a string, got boolean")
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    raise ManifestError(f"Field '{field_name}' must be a string, got {type(value).__name__}")


def _ref_name(ref: Any) -> str:
    """Extract a package name from a dependency reference (str or dict)."""
    if isinstance(ref, str):
        name = ref.strip()
    elif isinstance(ref, dict):
        name = _as_str(ref.get("name"), "dependency.name")
        if not name:
            raise ManifestError("Every dependency object must contain a non-empty 'name'")
        return name
    else:
        raise ManifestError(
            f"Dependency references must be strings or objects, got {type(ref).__name__}"
        )
    if not name:
        raise ManifestError("Dependency references must not be empty strings")
    return name


def parse_manifest(payload: Any) -> NormalizedManifest:
    if not isinstance(payload, dict):
        raise ManifestError("Manifest root must be a JSON object")

    name = _as_str(payload.get("name"), "name") or "uploaded-manifest"
    warnings: List[str] = []
    nodes: Dict[str, Node] = {}
    node_order: List[str] = []
    edges: List[Edge] = []
    pending_inline: List[Tuple[str, Any]] = []

    def add_node(raw: Any, *, implicit: bool = False) -> str:
        if not isinstance(raw, dict):
            raise ManifestError(
                f"Node entries must be objects, got {type(raw).__name__}"
            )
        node_name = _as_str(raw.get("name"), "node.name")
        if not node_name:
            raise ManifestError("Every node must contain a non-empty 'name'")
        version = _as_str(raw.get("version", ""), "node.version")
        license_raw = raw.get("license")
        license_raw = _as_str(license_raw, "node.license") if license_raw is not None else None
        canon, recognized = normalize_license(license_raw)

        if node_name in nodes:
            existing = nodes[node_name]
            if version and existing.version and version != existing.version:
                warnings.append(
                    f"Node '{node_name}' declared multiple times with different versions "
                    f"('{existing.version}' vs '{version}'); keeping the first."
                )
            if license_raw and existing.license_raw and license_raw != existing.license_raw:
                warnings.append(
                    f"Node '{node_name}' declared with multiple licenses "
                    f"('{existing.license_raw}' vs '{license_raw}'); keeping the first."
                )
            elif not existing.license_raw and license_raw:
                existing.license_raw = license_raw
                existing.license = canon
                existing.license_recognized = recognized
            if existing.implicit and not implicit:
                existing.implicit = False
            return node_name

        node = Node(
            name=node_name,
            version=version,
            license_raw=license_raw,
            license=canon,
            license_recognized=recognized,
            implicit=implicit,
        )
        nodes[node_name] = node
        node_order.append(node_name)
        return node_name

    raw_nodes = payload.get("nodes")
    raw_packages = payload.get("packages")

    if raw_nodes is not None:
        if not isinstance(raw_nodes, list):
            raise ManifestError("'nodes' must be a list")
        for raw in raw_nodes:
            add_node(raw)

        raw_edges = payload.get("edges", [])
        if not isinstance(raw_edges, list):
            raise ManifestError("'edges' must be a list")
        for i, raw_edge in enumerate(raw_edges):
            if not isinstance(raw_edge, dict):
                raise ManifestError(f"edges[{i}] must be an object")
            src = _as_str(raw_edge.get("from"), "edges.from")
            if not src:
                raise ManifestError(f"edges[{i}] is missing 'from'")
            tos = raw_edge.get("to", raw_edge.get("to"))
            targets = tos if isinstance(tos, list) else [tos]
            for ref in targets:
                dst = _ref_name(ref)
                edges.append(Edge(src, dst))

    if raw_packages is not None:
        if isinstance(raw_packages, dict):
            # {"name": {"version": ..., "license": ..., "dependencies": {...}}}
            iterable: List[Any] = []
            for pkg_name, meta in raw_packages.items():
                meta = meta if isinstance(meta, dict) else {}
                entry = {"name": pkg_name, **{k: v for k, v in meta.items()}}
                iterable.append(entry)
        elif isinstance(raw_packages, list):
            iterable = raw_packages
        else:
            raise ManifestError("'packages' must be a list or object")

        for raw in iterable:
            if isinstance(raw, str):
                raw = {"name": raw}
            if not isinstance(raw, dict):
                raise ManifestError(
                    f"Package entries must be objects or strings, got {type(raw).__name__}"
                )
            node_name = add_node(raw)
            deps = raw.get("dependencies", [])
            if isinstance(deps, dict):
                deps = [
                    {"name": dep_name, **(meta if isinstance(meta, dict) else {})}
                    for dep_name, meta in deps.items()
                ]
            if not isinstance(deps, list):
                raise ManifestError(f"'dependencies' of '{node_name}' must be a list or object")
            pending_inline.append((node_name, deps))

    if raw_nodes is None and raw_packages is None:
        raise ManifestError(
            "Manifest must contain either a 'nodes' list (with 'edges') "
            "or a 'packages' list/object."
        )

    # Inline dependency objects may themselves carry version/license metadata.
    for src, deps in pending_inline:
        for ref in deps:
            dst = _ref_name(ref)
            if isinstance(ref, dict):
                if dst in nodes:
                    # Merge metadata into an existing node only where the
                    # existing declaration left the field empty.
                    existing = nodes[dst]
                    ref_version = _as_str(ref.get("version", ""), "dependency.version")
                    if ref_version and not existing.version:
                        existing.version = ref_version
                    ref_license = ref.get("license")
                    if ref_license is not None and not existing.license_raw:
                        license_raw = _as_str(ref_license, "dependency.license")
                        canon, recognized = normalize_license(license_raw)
                        existing.license_raw = license_raw
                        existing.license = canon
                        existing.license_recognized = recognized
                        if existing.implicit:
                            existing.implicit = False
                elif ref.get("version") is not None or ref.get("license") is not None:
                    # A dependency object carrying metadata acts as a node
                    # declaration ...
                    add_node(
                        {
                            "name": dst,
                            "version": ref.get("version", ""),
                            "license": ref.get("license"),
                        }
                    )
                # ... otherwise a bare {"name": ...} behaves like a string
                # reference and becomes an implicit node in the loop below.
            edges.append(Edge(src, dst))

    # Resolve edge endpoints: create implicit nodes for unknown targets.
    for edge in edges:
        for endpoint in (edge.src, edge.dst):
            if endpoint not in nodes:
                nodes[endpoint] = Node(
                    name=endpoint,
                    license_raw=None,
                    license=None,
                    license_recognized=False,
                    implicit=True,
                )
                node_order.append(endpoint)
                warnings.append(
                    f"Referenced package '{endpoint}' is not declared in the manifest; "
                    "added as an implicit node with an unknown license."
                )

    # Validate node licenses that failed normalization
    for nname in node_order:
        node = nodes[nname]
        if node.license_raw is not None and not node.license_recognized:
            warnings.append(
                f"License '{node.license_raw}' of package '{nname}' is not in the "
                "compatibility matrix; edges touching it will be marked 'unknown'."
            )

    # Deduplicate edges while preserving order.
    seen_edges: Set[Tuple[str, str]] = set()
    unique_edges: List[Edge] = []
    for edge in edges:
        key = (edge.src, edge.dst)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        unique_edges.append(edge)
    edges = unique_edges

    # Roots: explicit "roots" field, else declared roots, else nodes without
    # incoming edges.
    roots = _resolve_roots(payload.get("roots"), nodes, edges)

    return NormalizedManifest(
        name=name,
        nodes=nodes,
        edges=edges,
        roots=roots,
        warnings=warnings,
        node_order=node_order,
    )


def _resolve_roots(
    raw_roots: Any, nodes: Dict[str, Node], edges: List[Edge]
) -> List[str]:
    if raw_roots is not None:
        if isinstance(raw_roots, str):
            raw_roots = [raw_roots]
        if not isinstance(raw_roots, list):
            raise ManifestError("'roots' must be a list of package names")
        resolved: List[str] = []
        for r in raw_roots:
            name = _as_str(r, "roots")
            if name and name in nodes and name not in resolved:
                resolved.append(name)
        if resolved:
            return resolved

    incoming: Dict[str, int] = {n: 0 for n in nodes}
    for edge in edges:
        incoming[edge.dst] = incoming.get(edge.dst, 0) + 1
        incoming.setdefault(edge.src, incoming.get(edge.src, 0))
    declared_roots = [
        n for n in nodes if incoming.get(n, 0) == 0 and not nodes[n].implicit
    ]
    if declared_roots:
        return declared_roots
    # Cyclic graph with no source node: fall back to the first node.
    return list(nodes)[:1]


def manifest_to_public_dict(man: NormalizedManifest) -> Dict[str, Any]:
    return {
        "name": man.name,
        "nodes": [
            {
                "name": n.name,
                "version": n.version,
                "license": n.license,
                "license_raw": n.license_raw,
                "license_recognized": n.license_recognized,
                "implicit": n.implicit,
            }
            for n in (man.nodes[name] for name in man.node_order)
        ],
        "edges": [{"from": e.src, "to": e.dst} for e in man.edges],
        "roots": man.roots,
        "warnings": man.warnings,
    }
