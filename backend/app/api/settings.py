from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.services.octo import browser_diag
from app.services.persona import get_active_persona_id, list_personas, set_active_persona
from app.services import runtime_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


class PersonaActivate(BaseModel):
    persona_id: str


class AtsFloorPatch(BaseModel):
    min_ats_score: int = Field(..., ge=0, le=100)
    ats_max_attempts: int | None = Field(default=None, ge=1, le=6)


class AccentPatch(BaseModel):
    # "" clears the choice and returns each persona to its own stylesheet.
    accent: str = ""


@router.get("")
async def get_settings_api():
    from app.services.company_discovery import search_provider_status

    s = get_settings()
    return {
        "openai_model": s.openai_model,
        "auto_submit": s.auto_submit,
        "octo_enabled": s.octo_enabled,
        "octo_api_url": s.octo_api_url,
        "octo_profile_configured": bool(s.octo_profile_uuid),
        "browser_cdp_configured": bool(s.browser_cdp_url),
        "apply_allow_launch_chromium": s.apply_allow_launch_chromium,
        "active_persona": get_active_persona_id(),
        "access_token_enabled": bool(s.access_token),
        "company_discovery": search_provider_status(),
        "min_ats_score": runtime_settings.min_ats_score(),
        "ats_max_attempts": runtime_settings.max_attempts(),
    }


@router.get("/browser-diag")
async def settings_browser_diag():
    return await browser_diag()


@router.get("/personas")
async def settings_personas():
    return {"personas": list_personas(), "active": get_active_persona_id()}


@router.post("/personas/activate")
async def activate_persona(body: PersonaActivate):
    try:
        set_active_persona(body.persona_id)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    return {"active": get_active_persona_id()}


def _accent_payload() -> dict:
    from app.services.resume_accent import PALETTE

    return {
        "palette": [dict(swatch) for swatch in PALETTE],
        "accent": runtime_settings.resume_accent(),
    }


@router.get("/resume-accent")
async def get_resume_accent():
    """The colours on offer, and which one generated resumes currently use."""
    return _accent_payload()


@router.post("/resume-accent")
async def set_resume_accent(body: AccentPatch):
    try:
        runtime_settings.update(resume_accent=body.accent)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    return _accent_payload()


@router.post("/ats-floor")
async def set_ats_floor(body: AtsFloorPatch):
    """Resumes scoring under this are regenerated with the missing terms fed back."""
    runtime_settings.update(
        min_ats_score=body.min_ats_score, ats_max_attempts=body.ats_max_attempts
    )
    return {
        "min_ats_score": runtime_settings.min_ats_score(),
        "ats_max_attempts": runtime_settings.max_attempts(),
    }
