"""SQLite persistence layer.

The database file is created and initialised (DDL idempotent) at application
startup.  Connections use ``Row`` factory and foreign keys are enforced.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

DB_PATH = os.environ.get("AUDIT_DB_PATH", "/data/licenses.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS manifests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    manifest_json   TEXT NOT NULL,
    report_json     TEXT NOT NULL,
    node_count      INTEGER NOT NULL DEFAULT 0,
    edge_count      INTEGER NOT NULL DEFAULT 0,
    conflict_count  INTEGER NOT NULL DEFAULT 0,
    unknown_count   INTEGER NOT NULL DEFAULT 0,
    cycle_count     INTEGER NOT NULL DEFAULT 0,
    warning_count   INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    manifest_id     INTEGER NOT NULL REFERENCES manifests(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    version         TEXT NOT NULL DEFAULT '',
    license         TEXT,
    license_raw     TEXT,
    license_recognized INTEGER NOT NULL DEFAULT 1,
    implicit        INTEGER NOT NULL DEFAULT 0,
    is_root         INTEGER NOT NULL DEFAULT 0,
    in_cycle        INTEGER NOT NULL DEFAULT 0,
    verdict         TEXT NOT NULL DEFAULT 'allow'
);

CREATE TABLE IF NOT EXISTS conflicts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    manifest_id         INTEGER NOT NULL REFERENCES manifests(id) ON DELETE CASCADE,
    consumer            TEXT NOT NULL,
    dependency          TEXT NOT NULL,
    consumer_license    TEXT,
    dependency_license  TEXT,
    verdict             TEXT NOT NULL,
    reason              TEXT,
    path_json           TEXT NOT NULL,
    distance            INTEGER NOT NULL,
    transitive          INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_manifests_created ON manifests(created_at);
CREATE INDEX IF NOT EXISTS idx_nodes_manifest    ON nodes(manifest_id);
CREATE INDEX IF NOT EXISTS idx_conflicts_manifest ON conflicts(manifest_id);
CREATE INDEX IF NOT EXISTS idx_conflicts_verdict ON conflicts(manifest_id, verdict);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db(db_path: Optional[str] = None) -> None:
    path = db_path or DB_PATH
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_conn(db_path: Optional[str] = None) -> Iterator[sqlite3.Connection]:
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_report(
    manifest_public: Dict[str, Any], report: Dict[str, Any], db_path: Optional[str] = None
) -> int:
    """Persist a normalized manifest plus its analysis report; return the id."""
    summary = report["summary"]
    with get_conn(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO manifests
                (name, manifest_json, report_json, node_count, edge_count,
                 conflict_count, unknown_count, cycle_count, warning_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                manifest_public["name"],
                json.dumps(manifest_public, ensure_ascii=False),
                json.dumps(report, ensure_ascii=False),
                summary["total_nodes"],
                summary["total_edges"],
                summary["conflicts"],
                summary["unknown"],
                summary["cycles"],
                summary["warnings"],
                _now(),
            ),
        )
        manifest_id = int(cur.lastrowid)

        node_rows = []
        for node in report["nodes"]:
            node_rows.append(
                (
                    manifest_id,
                    node["name"],
                    node["version"],
                    node["license"],
                    node["license_raw"],
                    1 if node["license_recognized"] else 0,
                    1 if node["implicit"] else 0,
                    1 if node["is_root"] else 0,
                    1 if node["in_cycle"] else 0,
                    node["verdict"],
                )
            )
        conn.executemany(
            """
            INSERT INTO nodes
                (manifest_id, name, version, license, license_raw,
                 license_recognized, implicit, is_root, in_cycle, verdict)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            node_rows,
        )

        finding_rows = []
        for finding in report["findings"]:
            finding_rows.append(
                (
                    manifest_id,
                    finding["consumer"],
                    finding["dependency"],
                    finding["consumer_license"],
                    finding["dependency_license"],
                    finding["verdict"],
                    finding["reason"],
                    json.dumps(finding["path"], ensure_ascii=False),
                    finding["distance"],
                    1 if finding["transitive"] else 0,
                )
            )
        if finding_rows:
            conn.executemany(
                """
                INSERT INTO conflicts
                    (manifest_id, consumer, dependency, consumer_license,
                     dependency_license, verdict, reason, path_json, distance, transitive)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                finding_rows,
            )
        return manifest_id


def list_manifests(
    limit: int = 50,
    offset: int = 0,
    package: Optional[str] = None,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        if package:
            like = f"%{package.lower()}%"
            rows = conn.execute(
                """
                SELECT DISTINCT m.* FROM manifests m
                JOIN nodes n ON n.manifest_id = m.id
                WHERE LOWER(n.name) LIKE ?
                ORDER BY m.id DESC LIMIT ? OFFSET ?
                """,
                (like, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM manifests ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [dict(r) for r in rows]


def get_manifest(manifest_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM manifests WHERE id = ?", (manifest_id,)
        ).fetchone()
        return dict(row) if row else None


def delete_manifest(manifest_id: int, db_path: Optional[str] = None) -> bool:
    with get_conn(db_path) as conn:
        cur = conn.execute("DELETE FROM manifests WHERE id = ?", (manifest_id,))
        return cur.rowcount > 0
