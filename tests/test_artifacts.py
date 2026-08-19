from pathlib import Path

import pytest

from app.config import get_settings
from app.services.artifacts import folder_is_allowed, resolve_artifacts, resolve_kind


def test_resolve_prefers_resume_pdf_not_cover(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PERSONA_OUTPUT_ROOT", str(tmp_path))
    get_settings.cache_clear()
    try:
        (tmp_path / "Phong V. Cao.pdf").write_bytes(b"%PDF-1.4")
        (tmp_path / "Phong V. Cao - Cover Letter.pdf").write_bytes(b"%PDF-1.4")
        (tmp_path / "Phong V. Cao.html").write_text(
            "<html><body>Resume</body></html>", encoding="utf-8"
        )
        (tmp_path / "cover_letter.txt").write_text("Dear hiring manager", encoding="utf-8")

        arts = resolve_artifacts(tmp_path)
        assert set(arts) == {"resume_pdf", "resume_html", "cover_pdf", "cover_txt"}
        assert arts["resume_pdf"].name == "Phong V. Cao.pdf"
        assert arts["cover_pdf"].name == "Phong V. Cao - Cover Letter.pdf"
        assert arts["cover_txt"].name == "cover_letter.txt"
        assert folder_is_allowed(tmp_path)

        pdf = resolve_kind(tmp_path, "resume_pdf")
        assert pdf.path.parent == tmp_path
        assert pdf.media_type == "application/pdf"
    finally:
        get_settings.cache_clear()


def test_resolve_kind_rejects_outside_output_root(tmp_path: Path, monkeypatch):
    inside = tmp_path / "apps"
    outside = tmp_path / "other"
    inside.mkdir()
    outside.mkdir()
    (outside / "leak.pdf").write_bytes(b"%PDF-1.4")
    monkeypatch.setenv("PERSONA_OUTPUT_ROOT", str(inside))
    get_settings.cache_clear()
    try:
        assert folder_is_allowed(outside) is False
        with pytest.raises(FileNotFoundError):
            resolve_kind(outside, "resume_pdf")
    finally:
        get_settings.cache_clear()


def test_resolve_missing_folder():
    assert resolve_artifacts(None) == {}
    assert resolve_artifacts("/no/such/folder") == {}
