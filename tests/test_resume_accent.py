"""Picking an accent repaints the stylesheet, and nothing else."""
import pytest

from app.services.resume_accent import (
    DEFAULT,
    PALETTE,
    apply_to_css,
    is_valid,
    normalise,
)

SHEET = """
.name { color: #1D6E93; }
body { color: #1A1A1A; background: #FFFFFF; }
.section-title { color: #1D6E93; border-bottom: 1.5px solid #1D6E93; }
.job-date { color: #5A6672; }
li::marker { color: #1d6e93; }
"""


class TestThePalette:
    def test_seven_colours_are_offered(self):
        assert len(PALETTE) == 7

    def test_each_is_a_usable_hex(self):
        for swatch in PALETTE:
            assert is_valid(swatch["value"]), swatch
            assert swatch["name"]

    def test_they_are_all_different(self):
        values = [s["value"].upper() for s in PALETTE]
        assert len(set(values)) == len(PALETTE)

    def test_the_default_is_the_sheet_alex_ships_with(self):
        assert DEFAULT == "#1D6E93"


class TestWhatCountsAsAColour:
    @pytest.mark.parametrize("good", ["#1D6E93", "#abcdef", "#ABCDEF", "  #123456  "])
    def test_a_hex_triplet_is_accepted(self, good):
        assert normalise(good) == good.strip().upper()

    @pytest.mark.parametrize(
        "bad",
        ["red", "#12345", "#1234567", "rgb(1,2,3)", "#12345g", "url(x)",
         "#000; } body { display:none", "javascript:alert(1)"],
    )
    def test_anything_else_is_refused(self, bad):
        # The value is substituted into a stylesheet, so free text must not pass.
        with pytest.raises(ValueError):
            normalise(bad)

    def test_blank_means_use_the_stylesheets_own(self):
        assert normalise("") is None
        assert normalise("   ") is None
        assert normalise(None) is None


class TestRepainting:
    def test_every_occurrence_of_the_accent_changes(self):
        out = apply_to_css(SHEET, "#1D6E93", "#8B2E13")
        assert "#1D6E93" not in out and "#1d6e93" not in out
        assert out.count("#8B2E13") == 4

    def test_it_matches_the_accent_whatever_its_case(self):
        # The sheet writes li::marker in lowercase.
        assert "#1d6e93" not in apply_to_css(SHEET, "#1D6E93", "#4F6151")

    def test_the_other_colours_are_untouched(self):
        out = apply_to_css(SHEET, "#1D6E93", "#2C3181")
        for keep in ("#1A1A1A", "#FFFFFF", "#5A6672"):
            assert keep in out

    def test_nothing_but_colour_is_disturbed(self):
        out = apply_to_css(SHEET, "#1D6E93", "#5F3A4D")
        assert "border-bottom: 1.5px solid" in out
        assert "li::marker" in out
        assert len(out.splitlines()) == len(SHEET.splitlines())

    def test_a_persona_that_declares_no_accent_is_left_alone(self):
        assert apply_to_css(SHEET, None, "#8B2E13") == SHEET
        assert apply_to_css(SHEET, "", "#8B2E13") == SHEET

    def test_no_choice_means_no_change(self):
        assert apply_to_css(SHEET, "#1D6E93", None) == SHEET
        assert apply_to_css(SHEET, "#1D6E93", "") == SHEET

    def test_choosing_the_colour_it_already_is_changes_nothing(self):
        assert apply_to_css(SHEET, "#1D6E93", "#1d6e93") == SHEET

    def test_a_bad_value_cannot_reach_the_stylesheet(self):
        # Belt and braces: normalise() guards the API, this guards the render.
        assert apply_to_css(SHEET, "#1D6E93", "#000; } body { display:none") == SHEET

    def test_every_palette_colour_can_be_applied(self):
        for swatch in PALETTE:
            out = apply_to_css(SHEET, "#1D6E93", swatch["value"])
            assert swatch["value"] in out or swatch["value"] == "#1D6E93"


class TestEachRequestCarriesItsOwnColour:
    """Two people generating at once: the colour comes with the request, not
    from the one shared setting."""

    @pytest.fixture
    def persona(self, tmp_path, monkeypatch):
        from types import SimpleNamespace

        from app.services import runtime_settings, tailor

        (tmp_path / "persona_identity").mkdir()
        (tmp_path / "persona_identity" / "p.css").write_text(SHEET, encoding="utf-8")
        monkeypatch.setattr(
            tailor, "get_settings", lambda: SimpleNamespace(config_dir=tmp_path)
        )
        monkeypatch.setattr(runtime_settings, "resume_accent", lambda: "#2C3181")
        return SimpleNamespace(tailor={"css_file": "p.css", "accent_color": "#1D6E93"})

    def test_two_requests_get_two_colours(self, persona):
        from app.services.tailor import _load_persona_css

        assert "#8B2E13" in _load_persona_css(persona, "#8B2E13")
        assert "#4F6151" in _load_persona_css(persona, "#4F6151")

    def test_an_unsent_colour_falls_back_to_the_saved_one(self, persona):
        from app.services.tailor import _load_persona_css

        assert "#2C3181" in _load_persona_css(persona, None)

    def test_blank_keeps_the_stylesheets_own(self, persona):
        from app.services.tailor import _load_persona_css

        assert _load_persona_css(persona, "") == SHEET

    def test_a_request_with_a_bad_colour_is_refused(self):
        from pydantic import BaseModel, ValidationError

        from app.services.resume_accent import RequestAccent

        class Body(BaseModel):
            accent: RequestAccent = None

        assert Body().accent is None
        assert Body(accent="").accent == ""
        assert Body(accent="#8b2e13").accent == "#8B2E13"
        with pytest.raises(ValidationError):
            Body(accent="red; } body { display:none")


class TestEachRequestCarriesItsOwnModel:
    def test_the_api_engine_asks_with_the_model_it_was_given(self, monkeypatch):
        import asyncio

        from app.services import runtime_settings, tailor

        seen = []

        async def fake_background(client, *, model, **_):
            seen.append(model)
            return "ok"

        monkeypatch.setattr(tailor, "_client", lambda: None)
        monkeypatch.setattr(tailor, "background_response_text", fake_background)
        monkeypatch.setattr(runtime_settings, "tailor_model", lambda: "saved-model")

        asyncio.run(tailor._ApiEngine("gpt-a").ask("s", "u"))
        asyncio.run(tailor._ApiEngine("gpt-b").ask("s", "u"))
        asyncio.run(tailor._ApiEngine().ask("s", "u"))
        assert seen == ["gpt-a", "gpt-b", "saved-model"]

    def test_a_malformed_model_id_is_refused(self):
        from pydantic import BaseModel, ValidationError

        from app.services.runtime_settings import RequestModel

        class Body(BaseModel):
            model: RequestModel = None

        assert Body().model is None
        assert Body(model=" ").model is None
        assert Body(model="gpt-6.1-sol").model == "gpt-6.1-sol"
        with pytest.raises(ValidationError):
            Body(model="gpt; rm -rf /")
