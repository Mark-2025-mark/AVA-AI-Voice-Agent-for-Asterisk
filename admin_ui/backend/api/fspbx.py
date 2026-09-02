"""Admin API for FS PBX tenant to AVA agent assignments."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

project_root = os.environ.get("PROJECT_ROOT") or str(Path(__file__).resolve().parents[3])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agents_store import AgentsStore
from src.core.fspbx_store import FspbxStore, get_fspbx_store
from src.integrations.fspbx import FspbxApiClient

router = APIRouter(prefix="/fspbx", tags=["fspbx"])
logger = logging.getLogger(__name__)


class FspbxConnectionRequest(BaseModel):
    base_url: str
    token: Optional[str] = None
    verify_ssl: bool = True
    timeout_ms: int = Field(default=10000, ge=1000, le=120000)


class FspbxAssignmentRequest(BaseModel):
    domain_uuid: str
    agent_slug: str
    domain_name: Optional[str] = None
    domain_description: Optional[str] = None
    enabled: bool = True
    notes: Optional[str] = None


class FspbxAssignmentPatch(BaseModel):
    agent_slug: Optional[str] = None
    domain_name: Optional[str] = None
    domain_description: Optional[str] = None
    enabled: Optional[bool] = None
    notes: Optional[str] = None


def _store() -> FspbxStore:
    try:
        return get_fspbx_store()
    except Exception as exc:
        logger.exception("FS PBX store unavailable")
        raise HTTPException(status_code=500, detail="FS PBX store unavailable") from exc


def _client_from_store() -> FspbxApiClient:
    connection = _store().get_connection_with_token()
    if not connection:
        raise HTTPException(status_code=409, detail="FS PBX connection is not configured.")
    return FspbxApiClient(
        str(connection.get("base_url") or ""),
        str(connection.get("token") or ""),
        verify_ssl=bool(connection.get("verify_ssl")),
        timeout_ms=int(connection.get("timeout_ms") or 10000),
    )


def _active_agent(slug: str) -> Optional[Dict[str, Any]]:
    store = AgentsStore()
    try:
        agent = store.get_by_slug(slug)
        if agent and bool(agent.get("is_active")):
            return agent
        return None
    finally:
        store.close()


def _assignment_with_agent(assignment: Dict[str, Any]) -> Dict[str, Any]:
    slug = str(assignment.get("agent_slug") or "")
    agent = _active_agent(slug)
    result = dict(assignment)
    result["agent_display_name"] = (
        str(agent.get("display_name") or slug) if agent else None
    )
    result["agent_available"] = bool(agent)
    return result


@router.get("/connection")
def get_connection() -> Dict[str, Any]:
    connection = _store().get_connection()
    return {"connection": connection}


@router.put("/connection")
def save_connection(req: FspbxConnectionRequest) -> Dict[str, Any]:
    try:
        connection = _store().save_connection(
            base_url=req.base_url,
            token=req.token,
            verify_ssl=req.verify_ssl,
            timeout_ms=req.timeout_ms,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"connection": connection}


@router.post("/connection/test")
def test_connection(req: Optional[FspbxConnectionRequest] = None) -> Dict[str, Any]:
    if req and (req.base_url or req.token):
        token = (req.token or "").strip()
        if not token:
            existing = _store().get_connection_with_token()
            token = str(existing.get("token") or "") if existing else ""
        client = FspbxApiClient(
            req.base_url,
            token,
            verify_ssl=req.verify_ssl,
            timeout_ms=req.timeout_ms,
        )
    else:
        client = _client_from_store()

    result = client.test_connection()
    payload = {
        "ok": result.ok,
        "status_code": result.status_code,
        "error": result.error,
    }
    if result.ok and isinstance(result.data, dict):
        rows = result.data.get("data")
        payload["domain_count_sample"] = len(rows) if isinstance(rows, list) else 0

    if req and (req.base_url or req.token):
        if result.ok and req.base_url:
            try:
                _store().save_connection(
                    base_url=req.base_url,
                    token=req.token,
                    verify_ssl=req.verify_ssl,
                    timeout_ms=req.timeout_ms,
                )
                _store().record_connection_verification(payload)
            except ValueError:
                pass
    elif result.ok:
        _store().record_connection_verification(payload)

    if not result.ok:
        raise HTTPException(
            status_code=502,
            detail=result.error or "FS PBX connection test failed.",
        )
    return payload


@router.get("/domains")
def list_domains() -> Dict[str, Any]:
    client = _client_from_store()
    result = client.list_domains(limit=100)
    if not result.ok:
        raise HTTPException(
            status_code=502,
            detail=result.error or "Failed to list FS PBX domains.",
        )

    assignments = {
        str(row.get("domain_uuid") or ""): row
        for row in _store().list_assignments()
    }
    rows = []
    payload = result.data if isinstance(result.data, dict) else {}
    for domain in payload.get("data") or []:
        if not isinstance(domain, dict):
            continue
        domain_uuid = str(domain.get("domain_uuid") or "")
        assignment = assignments.get(domain_uuid)
        rows.append(
            {
                "domain_uuid": domain_uuid,
                "domain_name": domain.get("domain_name"),
                "domain_description": domain.get("domain_description"),
                "domain_enabled": domain.get("domain_enabled"),
                "assignment": _assignment_with_agent(assignment) if assignment else None,
            }
        )
    return {"domains": rows, "count": len(rows)}


@router.get("/assignments")
def list_assignments() -> Dict[str, Any]:
    assignments = [_assignment_with_agent(row) for row in _store().list_assignments()]
    return {"assignments": assignments, "count": len(assignments)}


@router.post("/assignments")
def create_assignment(req: FspbxAssignmentRequest) -> Dict[str, Any]:
    if not _active_agent(req.agent_slug):
        raise HTTPException(
            status_code=422,
            detail=f"Agent '{req.agent_slug}' was not found or is inactive.",
        )
    existing = _store().get_assignment_by_domain(req.domain_uuid)
    try:
        assignment = _store().save_assignment(
            domain_uuid=req.domain_uuid,
            agent_slug=req.agent_slug,
            domain_name=req.domain_name,
            domain_description=req.domain_description,
            enabled=req.enabled,
            notes=req.notes,
            assignment_id=str(existing.get("id")) if existing else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    status = 200 if existing else 201
    return {"assignment": _assignment_with_agent(assignment), "status": status}


@router.patch("/assignments/{assignment_id}")
def update_assignment(assignment_id: str, req: FspbxAssignmentPatch) -> Dict[str, Any]:
    existing = _store().get_assignment(assignment_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Assignment not found.")

    agent_slug = req.agent_slug if req.agent_slug is not None else str(existing.get("agent_slug") or "")
    if not _active_agent(agent_slug):
        raise HTTPException(
            status_code=422,
            detail=f"Agent '{agent_slug}' was not found or is inactive.",
        )

    try:
        assignment = _store().save_assignment(
            assignment_id=assignment_id,
            domain_uuid=str(existing.get("domain_uuid") or ""),
            agent_slug=agent_slug,
            domain_name=req.domain_name if req.domain_name is not None else existing.get("domain_name"),
            domain_description=(
                req.domain_description
                if req.domain_description is not None
                else existing.get("domain_description")
            ),
            enabled=req.enabled if req.enabled is not None else bool(existing.get("enabled")),
            notes=req.notes if req.notes is not None else existing.get("notes"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"assignment": _assignment_with_agent(assignment)}


@router.delete("/assignments/{assignment_id}")
def delete_assignment(assignment_id: str) -> Dict[str, Any]:
    if not _store().delete_assignment(assignment_id):
        raise HTTPException(status_code=404, detail="Assignment not found.")
    return {"deleted": True, "id": assignment_id}


@router.get("/agents")
def list_agents_for_assignment() -> Dict[str, Any]:
    store = AgentsStore()
    try:
        agents = []
        for row in store.list_all():
            if not bool(row.get("is_active")):
                continue
            agents.append(
                {
                    "slug": row.get("slug"),
                    "display_name": row.get("display_name"),
                    "is_default": bool(row.get("is_default")),
                }
            )
        agents.sort(key=lambda item: str(item.get("display_name") or item.get("slug") or ""))
        return {"agents": agents}
    finally:
        store.close()
