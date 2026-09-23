"""FastAPI application: license dependency conflict audit console.

On startup the SQLite database is initialised (DDL is idempotent).

Web UI : GET /
JSON API:
  GET  /api/health
  GET  /api/licenses
  POST /api/manifests            (JSON body or multipart file upload)
  GET  /api/manifests
  GET  /api/manifests/{id}
  GET  /api/manifests/{id}/export  (?package=&severity=&scope=)
  GET  /api/manifests/{id}/findings(?package=&severity=&scope=)
  DELETE /api/manifests/{id}
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from datetime import datetime, timezone

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response

from . import db
from .analyzer import analyze, filter_report
from .licenses import ALL_LICENSES, CATEGORY_OF, build_matrix, categories
from .models import ManifestError, manifest_to_public_dict, parse_manifest

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(
    title="License Dependency Conflict Audit Console",
    description="Upload a package manifest; compute transitive license "
    "constraints, shortest conflict paths and export JSON reports.",
    version="1.0.0",
)


@app.on_event("startup")
def _startup_init_db() -> None:
    db.init_db()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_analysis(payload: Any) -> Dict[str, Any]:
    try:
        normalized = parse_manifest(payload)
    except ManifestError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    report = analyze(normalized)
    manifest_public = manifest_to_public_dict(normalized)
    manifest_id = db.save_report(manifest_public, report)
    return {
        "id": manifest_id,
        "manifest": manifest_public,
        "report": report,
        "links": {
            "self": f"/api/manifests/{manifest_id}",
            "export": f"/api/manifests/{manifest_id}/export",
            "findings": f"/api/manifests/{manifest_id}/findings",
        },
    }


def _load_report(manifest_id: int) -> Dict[str, Any]:
    row = db.get_manifest(manifest_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Manifest {manifest_id} not found")
    return json.loads(row["report_json"])


def _export_payload(manifest_id: int, report: Dict[str, Any]) -> Dict[str, Any]:
    row = db.get_manifest(manifest_id)
    assert row is not None
    return {
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool": "license-dependency-audit",
        "manifest_id": manifest_id,
        "manifest_name": row["name"],
        "created_at": row["created_at"],
        "manifest": json.loads(row["manifest_json"]),
        "report": report,
    }


# ---------------------------------------------------------------------------
# Web UI
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> HTMLResponse:
    with open(os.path.join(BASE_DIR, "static", "index.html"), "r", encoding="utf-8") as fh:
        return HTMLResponse(fh.read())


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "database": db.DB_PATH}


@app.get("/api/licenses")
def licenses() -> Dict[str, Any]:
    return {
        "licenses": ALL_LICENSES,
        "categories": categories(),
        "category_of": CATEGORY_OF,
        "matrix": build_matrix(),
        "disclaimer": "This matrix is a pragmatic summary for engineering "
        "review only and does not constitute legal advice.",
    }


@app.post("/api/manifests", status_code=201)
async def create_manifest(
    request: Request,
    file: Optional[UploadFile] = File(default=None),
) -> Dict[str, Any]:
    """Accept either a raw JSON body or a multipart ``file`` upload."""
    if file is not None:
        raw = await file.read()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON upload: {exc}")
        return _run_analysis(payload)

    try:
        payload = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=400,
            detail="Request body must be JSON (or upload a 'file' part with JSON content).",
        )
    return _run_analysis(payload)


@app.get("/api/manifests")
def get_manifests(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    package: Optional[str] = Query(None, description="Filter to manifests containing this package name"),
) -> Dict[str, Any]:
    rows = db.list_manifests(limit=limit, offset=offset, package=package)
    items: List[Dict[str, Any]] = []
    for row in rows:
        items.append(
            {
                "id": row["id"],
                "name": row["name"],
                "created_at": row["created_at"],
                "node_count": row["node_count"],
                "edge_count": row["edge_count"],
                "conflict_count": row["conflict_count"],
                "unknown_count": row["unknown_count"],
                "cycle_count": row["cycle_count"],
                "warning_count": row["warning_count"],
                "links": {
                    "self": f"/api/manifests/{row['id']}",
                    "export": f"/api/manifests/{row['id']}/export",
                    "findings": f"/api/manifests/{row['id']}/findings",
                },
            }
        )
    return {"items": items, "limit": limit, "offset": offset, "count": len(items)}


@app.get("/api/manifests/{manifest_id}")
def get_manifest_detail(manifest_id: int) -> Dict[str, Any]:
    row = db.get_manifest(manifest_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Manifest {manifest_id} not found")
    return {
        "id": row["id"],
        "name": row["name"],
        "created_at": row["created_at"],
        "manifest": json.loads(row["manifest_json"]),
        "report": json.loads(row["report_json"]),
        "links": {
            "self": f"/api/manifests/{manifest_id}",
            "export": f"/api/manifests/{manifest_id}/export",
            "findings": f"/api/manifests/{manifest_id}/findings",
        },
    }


@app.get("/api/manifests/{manifest_id}/findings")
def get_manifest_findings(
    manifest_id: int,
    package: Optional[str] = Query(None),
    severity: Optional[str] = Query(None, pattern="^(conflict|unknown|review|all)$"),
    find_scope: str = Query("any", alias="scope", pattern="^(any|consumer|dependency|subtree)$"),
) -> Dict[str, Any]:
    report = _load_report(manifest_id)
    sev = None if severity in (None, "all") else severity
    filtered = filter_report(report, package=package, severity=sev, scope=find_scope)
    return {
        "id": manifest_id,
        "filter": filtered["filter"],
        "summary": filtered["summary"],
        "findings": filtered["findings"],
        "cycles": report["cycles"],
        "warnings": report["warnings"],
    }


@app.get("/api/manifests/{manifest_id}/export")
def export_manifest(
    manifest_id: int,
    package: Optional[str] = Query(None),
    severity: Optional[str] = Query(None, pattern="^(conflict|unknown|review|all)$"),
    find_scope: str = Query("any", alias="scope", pattern="^(any|consumer|dependency|subtree)$"),
    download: bool = Query(False, description="Force attachment download"),
) -> Response:
    report = _load_report(manifest_id)
    sev = None if severity in (None, "all") else severity
    filtered = filter_report(report, package=package, severity=sev, scope=find_scope)
    payload = _export_payload(manifest_id, filtered)
    headers: Dict[str, str] = {}
    if download:
        headers["Content-Disposition"] = (
            f'attachment; filename="audit-report-{manifest_id}.json"'
        )
    return JSONResponse(payload, headers=headers)


@app.delete("/api/manifests/{manifest_id}", status_code=204)
def remove_manifest(manifest_id: int) -> Response:
    if not db.delete_manifest(manifest_id):
        raise HTTPException(status_code=404, detail=f"Manifest {manifest_id} not found")
    return Response(status_code=204)
