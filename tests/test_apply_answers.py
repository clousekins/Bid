from app.services.apply_answers import match_static, pick_option, static_defaults
from app.services.persona import load_persona


def test_static_defaults_core():
    p = load_persona("phong_v_cao")
    d = static_defaults(p)
    assert d["first name"] == "Phong"
    assert d["last name"] == "Cao"
    assert "21717" in d["full mailing address"]
    assert d["degree"] == "Bachelor's Degree"
    assert match_static("Are you a U.S. citizen?*", d)
    assert match_static("Desired Salary/Hourly Rate*", d)
    opts = ["No", "Yes", "Prefer not to say"]
    assert pick_option("Yes", opts) == "Yes"
    assert pick_option("Asian", ["White", "Asian", "Black"]) == "Asian"
