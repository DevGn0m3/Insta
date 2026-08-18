from __future__ import annotations
from typing import Any
from fastapi import APIRouter
from backend.services import settings_service

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
async def get_settings() -> dict[str, Any]:
    return settings_service.get_settings()


@router.post("")
async def update_settings(payload: dict[str, Any]) -> dict[str, Any]:
    return settings_service.update_settings(payload)


@router.post("/reset")
async def reset_settings() -> dict[str, Any]:
    return {"settings": settings_service.reset_to_defaults()}
