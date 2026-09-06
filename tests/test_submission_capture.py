"""Screenshots of what was actually submitted.

Nothing recorded what a form contained at the moment it was sent, so a silent
fill failure and a genuinely-ignored application looked identical afterwards.
"""
from __future__ import annotations

import pytest

from app.services.artifacts import KINDS, resolve_artifacts
from app.services.submission_capture import (
    POST_SUBMIT,
    PRE_SUBMIT,
    capture_submission,
    list_captures,
    screenshot_name,
)


class FakePage:
    """Stands in for a Playwright page."""

    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls: list[dict] = []

    async def screenshot(self, *, path: str, full_page: bool = False):
        self.calls.append({"path": path, "full_page": full_page})
        if self.fail:
            raise RuntimeError("target page has been closed")
        with open(path, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n")  # PNG magic; enough to be a real file


@pytest.mark.asyncio
async def test_capture_writes_into_the_package_folder(tmp_path):
    page = FakePage()
    out = await capture_submission(page, tmp_path, PRE_SUBMIT)
    assert out is not None
    assert out.parent == tmp_path
    assert out.exists() and out.stat().st_size > 0
    assert out.suffix == ".png"
    assert PRE_SUBMIT in out.name
    assert page.calls[0]["full_page"] is True, "a part-page shot loses the evidence"


@pytest.mark.asyncio
async def test_a_failed_screenshot_never_breaks_the_application(tmp_path):
    """The apply session must survive a capture failure."""
    page = FakePage(fail=True)
    logs: list[tuple[str, str]] = []
    out = await capture_submission(
        page, tmp_path, POST_SUBMIT, emit=lambda m, lvl="info": logs.append((m, lvl))
    )
    assert out is None
    assert list_captures(tmp_path) == []
    assert any(lvl == "warn" for _, lvl in logs), "the failure should be reported"


@pytest.mark.asyncio
async def test_missing_folder_and_missing_page_are_handled(tmp_path):
    assert await capture_submission(FakePage(), None, PRE_SUBMIT) is None
    assert await capture_submission(None, tmp_path, PRE_SUBMIT) is None
    assert await capture_submission(FakePage(), tmp_path / "nope", PRE_SUBMIT) is None


@pytest.mark.asyncio
async def test_both_stages_are_kept_not_overwritten(tmp_path):
    """Pre- and post-submit answer different questions; keep both."""
    page = FakePage()
    await capture_submission(page, tmp_path, PRE_SUBMIT)
    await capture_submission(page, tmp_path, POST_SUBMIT)
    shots = list_captures(tmp_path)
    assert len(shots) == 2
    names = " ".join(p.name for p in shots)
    assert PRE_SUBMIT in names and POST_SUBMIT in names


@pytest.mark.asyncio
async def test_capture_is_exposed_as_an_artifact(tmp_path):
    page = FakePage()
    await capture_submission(page, tmp_path, PRE_SUBMIT)
    assert "submission_png" in KINDS
    found = resolve_artifacts(tmp_path)
    assert "submission_png" in found
    assert found["submission_png"].media_type == "image/png"


def test_screenshot_names_are_filesystem_safe():
    name = screenshot_name("Pre Submit/Weird:Stage")
    assert not set(name) & set('<>:"/\\|?*')
    assert name.endswith(".png")


@pytest.mark.asyncio
async def test_other_pngs_in_the_folder_are_not_mistaken_for_captures(tmp_path):
    (tmp_path / "company-logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    assert list_captures(tmp_path) == []
    await capture_submission(FakePage(), tmp_path, PRE_SUBMIT)
    assert len(list_captures(tmp_path)) == 1
