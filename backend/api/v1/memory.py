from __future__ import annotations

from fastapi import APIRouter, Request

from backend.core.config import get_app_config
from backend.core.responses import success_response
from backend.core.security import sanitize_config
from memory.schemas import (
    ConflictMemoryRequest,
    DisableMemoryRequest,
    MemoryLinkCreate,
    MemoryNoteCreate,
    MemorySearchQuery,
)
from memory.service import MemoryService


router = APIRouter(prefix="/memory", tags=["memory"])


@router.post("/notes")
def create_memory_note(payload: MemoryNoteCreate, request: Request) -> dict:
    data = MemoryService().create_memory(payload).model_dump(mode="json")
    data["real_trading_enabled"] = get_app_config().real_trading_enabled
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)


@router.get("/notes/{note_id}")
def get_memory_note(note_id: int, request: Request) -> dict:
    data = MemoryService().get_memory(note_id).model_dump(mode="json")
    data["real_trading_enabled"] = get_app_config().real_trading_enabled
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)


@router.post("/search")
def search_memory(payload: MemorySearchQuery, request: Request) -> dict:
    result = MemoryService().search_memory(payload)
    data = result.model_dump(mode="json")
    data["real_trading_enabled"] = get_app_config().real_trading_enabled
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)


@router.post("/links")
def create_memory_link(payload: MemoryLinkCreate, request: Request) -> dict:
    data = MemoryService().create_link(payload).model_dump(mode="json")
    data["real_trading_enabled"] = get_app_config().real_trading_enabled
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)


@router.post("/reflection/from-review/{review_id}")
def create_reflection_from_review(review_id: int, request: Request) -> dict:
    data = MemoryService().build_reflection_from_review(review_id).model_dump(mode="json")
    data["real_trading_enabled"] = get_app_config().real_trading_enabled
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)


@router.post("/playbook/from-review/{review_id}")
def create_playbook_from_review(review_id: int, request: Request) -> dict:
    data = MemoryService().build_playbook_from_review(review_id).model_dump(mode="json")
    data["real_trading_enabled"] = get_app_config().real_trading_enabled
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)


@router.get("/playbooks")
def list_playbooks(request: Request) -> dict:
    playbooks = MemoryService().list_playbooks()
    data = {
        "playbooks": [playbook.model_dump(mode="json") for playbook in playbooks],
        "count": len(playbooks),
        "real_trading_enabled": get_app_config().real_trading_enabled,
    }
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)


@router.post("/notes/{note_id}/disable")
def disable_memory_note(note_id: int, payload: DisableMemoryRequest, request: Request) -> dict:
    data = MemoryService().disable_memory(note_id, reason=payload.reason).model_dump(mode="json")
    data["real_trading_enabled"] = get_app_config().real_trading_enabled
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)


@router.post("/notes/{note_id}/conflict")
def mark_memory_conflict(note_id: int, payload: ConflictMemoryRequest, request: Request) -> dict:
    data = MemoryService().mark_memory_conflict(note_id, status=payload.status, reason=payload.reason).model_dump(mode="json")
    data["real_trading_enabled"] = get_app_config().real_trading_enabled
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)


@router.get("/config")
def memory_config(request: Request) -> dict:
    data = MemoryService().config_summary()
    data["real_trading_enabled"] = get_app_config().real_trading_enabled
    return success_response(data=sanitize_config(data), trace_id=request.state.trace_id)
