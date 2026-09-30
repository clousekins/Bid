from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.config import get_settings


def strip_name_parentheticals(raw: str) -> str:
    """'Brian Gou (Yi)' and 'Brian(Yi) Gou' both become 'Brian Gou'.

    Persona names carry alternate spellings in parentheses. Left in place they
    leak into resume titles, ATS last-name fields and cover-letter signatures,
    so every consumer normalizes through here.
    """
    return re.sub(r"\s+", " ", re.sub(r"\([^)]*\)", " ", raw or "")).strip()


@dataclass
class Persona:
    persona_id: str
    display_name: str
    default_professional_title: str
    total_experience: str
    identity: dict[str, Any] = field(default_factory=dict)
    screening_answers: list[dict[str, str]] = field(default_factory=list)
    tailor: dict[str, Any] = field(default_factory=dict)
    skills_ground_truth: dict[str, Any] = field(default_factory=dict)
    experience_ground_truth: list[dict[str, Any]] = field(default_factory=list)
    education_ground_truth: list[dict[str, Any]] = field(default_factory=list)
    projects_ground_truth: list[dict[str, Any]] = field(default_factory=list)
    system_prompt: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def _personas_index() -> dict[str, Any]:
    path = get_settings().config_dir / "personas.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def list_personas() -> list[dict[str, Any]]:
    data = _personas_index()
    active = data.get("active_option")
    out = []
    for opt in data.get("options") or []:
        if not opt.get("enabled", True):
            continue
        out.append({**opt, "active": opt.get("id") == active})
    return out


def get_active_persona_id() -> str:
    data = _personas_index()
    return str(data.get("active_option") or "phong_v_cao")


def set_active_persona(persona_id: str) -> None:
    path = get_settings().config_dir / "personas.yml"
    data = _personas_index()
    ids = {o["id"] for o in data.get("options") or []}
    if persona_id not in ids:
        raise ValueError(f"Unknown persona: {persona_id}")
    data["active_option"] = persona_id
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _persona_stamp(persona_id: str) -> tuple[float, ...]:
    """mtimes of the files a persona is built from.

    Part of the cache key so an edit to a .yml or .prompt.md is picked up
    without a restart, while a table of 50 rows stops re-reading a 71KB
    prompt file 50 times.
    """
    data = _personas_index()
    opt = next((o for o in data.get("options") or [] if o.get("id") == persona_id), None)
    cfg = get_settings().config_dir
    paths = [cfg / "personas.yml"]
    if opt:
        for key in ("persona_file", "prompt_file"):
            name = opt.get(key)
            if not name:
                continue
            candidate = cfg / name
            if not candidate.exists():
                candidate = cfg / "persona_identity" / name
            paths.append(candidate)
    stamps = []
    for path in paths:
        try:
            stamps.append(path.stat().st_mtime)
        except OSError:
            stamps.append(0.0)
    return tuple(stamps)


@lru_cache(maxsize=16)
def _load_persona_cached(persona_id: str, stamp: tuple[float, ...]) -> Persona:
    return _load_persona_uncached(persona_id)


def load_persona(persona_id: str | None = None) -> Persona:
    data = _personas_index()
    pid = persona_id or data.get("active_option") or "phong_v_cao"
    return _load_persona_cached(pid, _persona_stamp(pid))


def _load_persona_uncached(persona_id: str | None = None) -> Persona:
    data = _personas_index()
    pid = persona_id or data.get("active_option") or "phong_v_cao"
    opt = next((o for o in data.get("options") or [] if o.get("id") == pid), None)
    if not opt:
        raise ValueError(f"Persona not found: {pid}")

    persona_path = get_settings().config_dir / opt["persona_file"]
    raw = yaml.safe_load(persona_path.read_text(encoding="utf-8")) or {}

    system_prompt = ""
    prompt_file = opt.get("prompt_file") or (raw.get("tailor") or {}).get("prompt_file")
    if prompt_file:
        # relative to persona_identity or config
        candidates = [
            persona_path.parent / prompt_file,
            get_settings().config_dir / "persona_identity" / prompt_file,
            get_settings().config_dir / prompt_file,
        ]
        for c in candidates:
            if Path(c).exists():
                system_prompt = Path(c).read_text(encoding="utf-8")
                break
    if not system_prompt:
        system_prompt = (raw.get("tailor") or {}).get("system_prompt") or (
            "You are a senior technical resume strategist. Output ONLY the final resume."
        )

    system_prompt = fill_identity_placeholders(system_prompt, raw.get("identity") or {})

    tailor = raw.get("tailor") or {}
    return Persona(
        persona_id=raw.get("persona_id") or pid,
        display_name=raw.get("display_name") or pid,
        default_professional_title=raw.get("default_professional_title") or "Software Engineer",
        total_experience=raw.get("total_experience") or "",
        identity=raw.get("identity") or {},
        screening_answers=raw.get("screening_answers") or [],
        tailor=tailor,
        skills_ground_truth=tailor.get("skills_ground_truth") or raw.get("skills_ground_truth") or {},
        experience_ground_truth=tailor.get("experience_ground_truth")
        or raw.get("experience_ground_truth")
        or [],
        education_ground_truth=tailor.get("education_ground_truth")
        or raw.get("education_ground_truth")
        or [],
        projects_ground_truth=tailor.get("projects_ground_truth")
        or raw.get("projects_ground_truth")
        or [],
        system_prompt=system_prompt,
        raw=raw,
    )


# {{phone}} in a prompt file is filled from the persona's identity, so the
# number lives in one place: the yml. With no number on record, the
# placeholder goes together with the separator in front of it — " | " in a
# prose contact line, the &nbsp; run in the HTML one — so the header never
# shows an empty slot. Prompts without a placeholder are untouched.
_PHONE_SLOT_RE = re.compile(r"(?:[ \t]*\|[ \t]*|[ \t]*(?:&nbsp;)+[ \t]*)?\{\{phone\}\}")


def fill_identity_placeholders(prompt: str, identity: dict[str, Any]) -> str:
    phone = str(identity.get("phone") or "").strip()
    if "{{phone}}" not in prompt:
        return prompt
    if phone:
        return prompt.replace("{{phone}}", phone)
    return _PHONE_SLOT_RE.sub("", prompt)


def ground_truth_payload(persona: Persona, *, for_resume: bool = False) -> dict[str, Any]:
    payload = {
        "persona_id": persona.persona_id,
        "display_name": persona.display_name,
        "default_professional_title": persona.default_professional_title,
        "total_experience": persona.total_experience,
        "identity": persona.identity,
        "skills_ground_truth": persona.skills_ground_truth,
        "experience_ground_truth": persona.experience_ground_truth,
        "education_ground_truth": persona.education_ground_truth,
        "projects_ground_truth": persona.projects_ground_truth,
        "screening_answers": persona.screening_answers,
    }
    if for_resume:
        from app.services.resume_header import resume_identity

        payload["identity"] = resume_identity(persona.identity)
        payload.pop("screening_answers", None)
    return payload
