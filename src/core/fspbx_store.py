"""SQLite persistence for FS PBX connection settings and tenant-agent assignments."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any, Dict, List, Optional
import uuid


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FspbxStore:
    """Thread-safe store for FS PBX tenant-agent mappings."""

    DEFAULT_CONNECTION_ID = "default"

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or os.getenv(
            "FSPBX_DB_PATH", "/app/data/operator/fspbx.db"
        )
        self._lock = threading.RLock()
        self._init_db()

    def _connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS fspbx_connection (
                    id TEXT PRIMARY KEY,
                    base_url TEXT NOT NULL,
                    token TEXT NOT NULL,
                    verify_ssl INTEGER NOT NULL DEFAULT 1,
                    timeout_ms INTEGER NOT NULL DEFAULT 10000,
                    last_verification_json TEXT,
                    last_verified_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS fspbx_tenant_assignments (
                    id TEXT PRIMARY KEY,
                    domain_uuid TEXT NOT NULL UNIQUE,
                    domain_name TEXT,
                    domain_description TEXT,
                    agent_slug TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_fspbx_assignments_domain
                    ON fspbx_tenant_assignments(domain_uuid);
                CREATE INDEX IF NOT EXISTS idx_fspbx_assignments_agent
                    ON fspbx_tenant_assignments(agent_slug);
                """
            )
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        return {key: row[key] for key in row.keys()}

    @staticmethod
    def _redact_connection(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not row:
            return None
        result = dict(row)
        token = str(result.get("token") or "")
        result["token"] = ""
        result["token_configured"] = bool(token.strip())
        result["verify_ssl"] = bool(result.get("verify_ssl"))
        result["enabled"] = True
        if result.get("last_verification_json"):
            try:
                result["last_verification"] = json.loads(result["last_verification_json"])
            except (TypeError, json.JSONDecodeError):
                result["last_verification"] = None
        return result

    @staticmethod
    def _decode_assignment(row: sqlite3.Row) -> Dict[str, Any]:
        data = FspbxStore._row_to_dict(row)
        data["enabled"] = bool(data.get("enabled"))
        return data

    def get_connection(self) -> Optional[Dict[str, Any]]:
        with self._lock, self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM fspbx_connection WHERE id = ?",
                (self.DEFAULT_CONNECTION_ID,),
            ).fetchone()
        if not row:
            return None
        return self._redact_connection(self._row_to_dict(row))

    def get_connection_with_token(self) -> Optional[Dict[str, Any]]:
        with self._lock, self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM fspbx_connection WHERE id = ?",
                (self.DEFAULT_CONNECTION_ID,),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def save_connection(
        self,
        *,
        base_url: str,
        token: Optional[str] = None,
        verify_ssl: bool = True,
        timeout_ms: int = 10000,
    ) -> Dict[str, Any]:
        base_url = (base_url or "").strip().rstrip("/")
        if not base_url:
            raise ValueError("base_url is required")

        now = _now()
        with self._lock, self._connection() as conn:
            existing = conn.execute(
                "SELECT token FROM fspbx_connection WHERE id = ?",
                (self.DEFAULT_CONNECTION_ID,),
            ).fetchone()
            resolved_token = (token or "").strip()
            if not resolved_token and existing:
                resolved_token = str(existing["token"] or "").strip()
            if not resolved_token:
                raise ValueError("token is required")

            conn.execute(
                """
                INSERT INTO fspbx_connection (
                    id, base_url, token, verify_ssl, timeout_ms,
                    last_verification_json, last_verified_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    base_url = excluded.base_url,
                    token = CASE
                        WHEN excluded.token != '' THEN excluded.token
                        ELSE fspbx_connection.token
                    END,
                    verify_ssl = excluded.verify_ssl,
                    timeout_ms = excluded.timeout_ms,
                    created_at = fspbx_connection.created_at,
                    updated_at = excluded.updated_at
                """,
                (
                    self.DEFAULT_CONNECTION_ID,
                    base_url,
                    resolved_token,
                    1 if verify_ssl else 0,
                    max(1000, int(timeout_ms)),
                    now,
                    now,
                ),
            )
        return self.get_connection() or {}

    def record_connection_verification(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        now = _now()
        with self._lock, self._connection() as conn:
            conn.execute(
                """
                UPDATE fspbx_connection
                SET last_verification_json = ?, last_verified_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (json.dumps(payload, sort_keys=True), now, now, self.DEFAULT_CONNECTION_ID),
            )
        return self.get_connection() or {}

    def list_assignments(self) -> List[Dict[str, Any]]:
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM fspbx_tenant_assignments
                ORDER BY COALESCE(domain_description, domain_name, domain_uuid)
                """
            ).fetchall()
        return [self._decode_assignment(row) for row in rows]

    def get_assignment(self, assignment_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM fspbx_tenant_assignments WHERE id = ?",
                (assignment_id,),
            ).fetchone()
        return self._decode_assignment(row) if row else None

    def get_assignment_by_domain(self, domain_uuid: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM fspbx_tenant_assignments WHERE domain_uuid = ?",
                (domain_uuid,),
            ).fetchone()
        return self._decode_assignment(row) if row else None

    def save_assignment(
        self,
        *,
        domain_uuid: str,
        agent_slug: str,
        domain_name: Optional[str] = None,
        domain_description: Optional[str] = None,
        enabled: bool = True,
        notes: Optional[str] = None,
        assignment_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        domain_uuid = (domain_uuid or "").strip()
        agent_slug = (agent_slug or "").strip()
        if not domain_uuid:
            raise ValueError("domain_uuid is required")
        if not agent_slug:
            raise ValueError("agent_slug is required")

        now = _now()
        assignment_id = (assignment_id or "").strip() or str(uuid.uuid4())

        with self._lock, self._connection() as conn:
            conn.execute(
                """
                INSERT INTO fspbx_tenant_assignments (
                    id, domain_uuid, domain_name, domain_description, agent_slug,
                    enabled, notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(domain_uuid) DO UPDATE SET
                    domain_name = excluded.domain_name,
                    domain_description = excluded.domain_description,
                    agent_slug = excluded.agent_slug,
                    enabled = excluded.enabled,
                    notes = excluded.notes,
                    created_at = fspbx_tenant_assignments.created_at,
                    updated_at = excluded.updated_at
                """,
                (
                    assignment_id,
                    domain_uuid,
                    (domain_name or None),
                    (domain_description or None),
                    agent_slug,
                    1 if enabled else 0,
                    (notes or None),
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM fspbx_tenant_assignments WHERE domain_uuid = ?",
                (domain_uuid,),
            ).fetchone()
        if not row:
            raise RuntimeError("Failed to persist tenant assignment")
        return self._decode_assignment(row)

    def delete_assignment(self, assignment_id: str) -> bool:
        with self._lock, self._connection() as conn:
            cursor = conn.execute(
                "DELETE FROM fspbx_tenant_assignments WHERE id = ?",
                (assignment_id,),
            )
        return cursor.rowcount > 0


_store: Optional[FspbxStore] = None
_store_lock = threading.Lock()


def get_fspbx_store() -> FspbxStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = FspbxStore()
        return _store
