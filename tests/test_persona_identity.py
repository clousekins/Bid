"""Guards against cross-persona identity contamination.

Every persona-specific answer must come from that persona. Before these tests
existed, five of six personas silently inherited phong_v_cao's street address,
ZIP, GitHub and school, and "Brian Gou (Yi)" parsed to a last name of "(Yi)".
"""
from __future__ import annotations

import pytest

from app.services.apply_answers import (
    PersonaIdentityError,
    split_legal_name,
    static_defaults,
    validate_persona_identity,
)
from app.services.persona import Persona, list_personas, load_persona, strip_name_parentheticals
from app.services.resume_header import enforce_resume_header
from app.services.tailor import _cover_letter_system

ALL_PERSONA_IDS = [p["id"] for p in list_personas()]

# phong_v_cao's own data. It must never appear on another persona.
PHONG_ONLY = (
    "21717 Gallus Dr",
    "78660",
    "Pflugerville",
    "github.com/phongvcao",
    "linkedin.com/in/phcao",
)


def _persona(**kwargs) -> Persona:
    base = {
        "persona_id": "test_persona",
        "display_name": "Test Person",
        "default_professional_title": "Data Engineer",
        "total_experience": "8 years",
        "identity": {
            "legal_name": "Test Person",
            "email": "test@example.com",
            "phone": "(555) 555-5555",
            "location_city": "Denver",
            "location_state": "CO",
        },
    }
    base.update(kwargs)
    return Persona(**base)


@pytest.mark.parametrize("persona_id", ALL_PERSONA_IDS)
def test_no_persona_inherits_phong_pii(persona_id):
    persona = load_persona(persona_id)
    if persona.persona_id == "phong_v_cao":
        pytest.skip("phong_v_cao legitimately owns this data")
    blob = " | ".join(f"{k}={v}" for k, v in static_defaults(persona).items())
    for secret in PHONG_ONLY:
        assert secret not in blob, f"{persona_id} leaked phong_v_cao's {secret!r}"


@pytest.mark.parametrize("persona_id", ALL_PERSONA_IDS)
def test_school_matches_the_persona_own_ground_truth(persona_id):
    """The form must not claim a school the attached resume contradicts."""
    persona = load_persona(persona_id)
    answers = static_defaults(persona)
    school = answers.get("university")
    if not school:
        return
    declared = {
        str(e.get("school") or e.get("institution") or e.get("university") or "").strip()
        for e in persona.education_ground_truth or []
    }
    assert school in declared, f"{persona_id}: form says {school!r}, resume says {declared!r}"


@pytest.mark.parametrize("persona_id", ALL_PERSONA_IDS)
def test_every_shipped_persona_is_valid(persona_id):
    assert validate_persona_identity(load_persona(persona_id)) == []


def test_parenthetical_names_do_not_become_the_last_name():
    persona = _persona(
        display_name="Brian Gou",
        identity={
            "legal_name": "Brian Gou (Yi)",
            "email": "b@example.com",
            "phone": "(929) 547 6426",
            "location_city": "Tustin",
            "location_state": "CA",
        },
    )
    assert split_legal_name(persona) == ("Brian", "Gou")
    assert static_defaults(persona)["last name"] == "Gou"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Brian Gou (Yi)", "Brian Gou"),
        ("Brian(Yi) Gou", "Brian Gou"),
        ("Brian Gou", "Brian Gou"),
        ("", ""),
    ],
)
def test_strip_name_parentheticals(raw, expected):
    assert strip_name_parentheticals(raw) == expected


def test_missing_identity_is_reported_not_substituted():
    persona = _persona(identity={"legal_name": "Test Person"})
    problems = validate_persona_identity(persona)
    assert any("email" in p for p in problems)
    assert any("phone" in p for p in problems)
    # And the answer is omitted rather than borrowed from someone else.
    assert "email" not in static_defaults(persona)


def test_unusable_name_raises_rather_than_guessing():
    persona = _persona(display_name="Cher", identity={"legal_name": "Cher"})
    with pytest.raises(PersonaIdentityError):
        split_legal_name(persona)


@pytest.mark.parametrize(
    "degree,discipline",
    [
        ("Master of Science (Data Science)", "Data Science"),
        ("Bachelor of Science in Computer Science", "Computer Science"),
        ("Master's degree, Machine Learning", "Machine Learning"),
    ],
)
def test_discipline_parsed_from_each_degree_format(degree, discipline):
    persona = _persona(education_ground_truth=[{"degree": degree, "school": "Somewhere U"}])
    assert static_defaults(persona)["discipline"] == discipline


def test_current_employer_prefers_the_present_role():
    persona = _persona(
        experience_ground_truth=[
            {"company": "UnitedHealth Group", "end": "Present"},
            {"company": "Older Corp", "end": "Jan 2023"},
        ]
    )
    assert static_defaults(persona)["current employer"] == "UnitedHealth Group"


@pytest.mark.parametrize("persona_id", ALL_PERSONA_IDS)
def test_cover_letter_signature_matches_the_persona(persona_id):
    """Resume and cover letter must agree on who is applying."""
    persona = load_persona(persona_id)
    prompt = _cover_letter_system(persona)
    assert persona.display_name in prompt
    city = str((persona.identity or {}).get("location_city") or "").strip()
    if city:
        assert city in prompt, f"{persona_id}: signature does not carry own city"
    if persona.persona_id != "phong_v_cao":
        assert "Pflugerville" not in prompt


def test_resume_header_survives_a_name_variant_and_location_line():
    """Neither the name variant nor the city may be mistaken for a job title."""
    persona = load_persona("brian_gou")
    md = "Brian(Yi) Gou\nTustin, CA\nbriangou9@gmail.com\n\n## Summary\nExperienced engineer.\n"
    header = enforce_resume_header(md, persona).split("Summary")[0]
    assert header.startswith("Brian Gou")
    assert "Brian(Yi) Gou" not in header
    assert persona.default_professional_title in header
