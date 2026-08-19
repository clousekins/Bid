from types import SimpleNamespace

from app.services.resume_header import (
    enforce_resume_header,
    resume_contact_line,
    resume_identity,
)


def test_resume_contact_line_is_city_only():
    ident = {
        "legal_name": "Phong V Cao",
        "email": "pvc.pc.ai@gmail.com",
        "phone": "+1 (737) 378-2821",
        "address_line": "21717 Gallus Dr",
        "location_city": "Pflugerville",
        "location_state": "TX",
        "location_postal": "78660",
        "linkedin_url": "https://www.linkedin.com/in/phcao/",
        "github_url": "https://github.com/phongvcao",
        "citizenship": "US Citizen",
        "work_authorization": "Authorized to work in the US",
    }
    line = resume_contact_line(ident)
    assert line == (
        "Pflugerville, TX | pvc.pc.ai@gmail.com | +1 (737) 378-2821 | "
        "linkedin.com/in/phcao | github.com/phongvcao"
    )
    assert "Gallus" not in line
    assert "78660" not in line
    assert "Citizen" not in line
    safe = resume_identity(ident)
    assert "citizenship" not in safe
    assert "address_line" not in safe


def test_enforce_resume_header_strips_street_and_citizenship():
    persona = SimpleNamespace(
        display_name="Phong V Cao",
        default_professional_title="Senior AI Software Engineer",
        identity={
            "email": "pvc.pc.ai@gmail.com",
            "phone": "+1 (737) 378-2821",
            "location_city": "Pflugerville",
            "location_state": "TX",
            "linkedin_url": "https://www.linkedin.com/in/phcao/",
            "github_url": "https://github.com/phongvcao",
        },
    )
    messy = """
**Phong V Cao**
21717 Gallus Dr
Pflugerville, TX 78660
**Email:** pvc.pc.ai@gmail.com
**Work Authorization:** US Citizen

**Senior Platform Engineer**

### SUMMARY

Senior engineer with 11+ years.
"""
    out = enforce_resume_header(messy, persona)
    assert out.startswith("Phong V Cao\n\nSenior Platform Engineer\n\nPflugerville, TX |")
    assert "Gallus" not in out
    assert "Citizen" not in out
    assert "78660" not in out
    assert "Work Authorization" not in out
    assert "\nSUMMARY\n" in out
    assert "###" not in out
