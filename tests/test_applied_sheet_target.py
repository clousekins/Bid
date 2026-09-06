"""Where the applied-job log is written.

The sheet is deployment config. It lived as a literal in config.py, which meant
one person's spreadsheet id travelled with the repo to anyone who cloned it and
silently became their default.
"""
from __future__ import annotations

import re

import pytest

from app.config import Settings


def test_no_spreadsheet_id_is_hardcoded_in_config():
    """Defaults must be blank so .env is the only place the target is set."""
    defaults = Settings.model_fields
    for name in ("applied_sheet_id", "applied_sheet_gid", "applied_sheet_tab"):
        assert defaults[name].default == "", f"{name} still ships a value"


def test_config_source_has_no_bare_google_doc_id():
    """A 40+ char Google id in the source is someone's real document."""
    import pathlib

    import app.config as config_module

    text = pathlib.Path(config_module.__file__).read_text(encoding="utf-8")
    suspicious = [
        m for m in re.findall(r"[\"']([A-Za-z0-9_-]{35,})[\"']", text)
    ]
    assert not suspicious, f"looks like a document id in config.py: {suspicious}"


def test_the_apps_script_uses_the_id_it_is_sent():
    """It used getActiveSpreadsheet(), so changing .env did not redirect it."""
    import pathlib

    script = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "applied_sheet_apps_script.js"
    body = script.read_text(encoding="utf-8")
    assert "openById" in body, "the script must open the sheet POONG names"
    assert "data.sheet_id" in body, "the payload's sheet_id must be read"


@pytest.mark.asyncio
async def test_payload_carries_the_target_so_the_webhook_can_route(monkeypatch, tmp_path):
    """The webhook can only honour a sheet it is told about."""
    from app.config import get_settings
    from app.services import applied_sheet

    monkeypatch.setenv("APPLIED_SHEET_ID", "TESTSHEETID")
    monkeypatch.setenv("APPLIED_SHEET_GID", "7")
    monkeypatch.setenv("APPLIED_SHEET_TAB", "Tab7")
    monkeypatch.setenv("APPLIED_SHEETS_WEBHOOK_URL", "")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    get_settings.cache_clear()

    settings = get_settings()
    payload = {
        "sheet_id": settings.applied_sheet_id,
        "sheet_tab": settings.applied_sheet_tab,
        "sheet_gid": settings.applied_sheet_gid,
    }
    assert payload["sheet_id"] == "TESTSHEETID"
    assert payload["sheet_gid"] == "7"
    assert payload["sheet_tab"] == "Tab7"
    get_settings.cache_clear()
