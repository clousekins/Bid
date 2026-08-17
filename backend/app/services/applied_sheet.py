from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)


def _local_log_path() -> Path:
    settings = get_settings()
    path = settings.root_dir / "data" / "applied_sheet_log.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _append_local_csv(row: list[str]) -> None:
    path = _local_log_path()
    new_file = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        if new_file:
            f.write("timestamp,title,company,url,location,persona_id,job_id\n")
        # minimal CSV escape
        cells = []
        for c in row:
            s = str(c).replace('"', '""')
            if "," in s or '"' in s or "\n" in s:
                s = f'"{s}"'
            cells.append(s)
        f.write(",".join(cells) + "\n")


async def log_applied_to_sheet(
    *,
    title: str,
    company: str,
    url: str,
    persona_id: str = "",
    job_id: int | None = None,
    location: str | None = None,
) -> dict[str, Any]:
    """Append applied job to Google Sheet (webhook and/or service account).

    Always mirrors to data/applied_sheet_log.csv.
    """
    settings = get_settings()
    ts = datetime.now(timezone.utc).isoformat()
    link = url or ""
    local_row = [ts, title, company, link, location or "", persona_id, str(job_id or "")]
    try:
        _append_local_csv(local_row)
    except Exception as exc:  # noqa: BLE001
        log.warning("local applied log failed: %s", exc)

    results: dict[str, Any] = {"local": True, "sheet": False, "webhook": False, "errors": []}

    if not settings.applied_sheet_enabled:
        results["skipped"] = "disabled"
        return results

    payload = {
        "title": title,
        "company": company,
        "url": link,
        "link": link,
        "job_title": title,
        "company_name": company,
        "job_link": link,
        "location": location or "",
        "persona_id": persona_id,
        "job_id": job_id,
        "timestamp": ts,
        "sheet_id": settings.applied_sheet_id,
        "sheet_tab": settings.applied_sheet_tab,
        "sheet_gid": settings.applied_sheet_gid,
    }

    if settings.applied_sheets_webhook_url:
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                resp = await client.post(
                    settings.applied_sheets_webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                if resp.status_code < 400:
                    results["webhook"] = True
                    results["sheet"] = True
                else:
                    results["errors"].append(
                        f"webhook_http_{resp.status_code}:{resp.text[:200]}"
                    )
        except Exception as exc:  # noqa: BLE001
            results["errors"].append(f"webhook:{exc}")

    if settings.google_service_account_json:
        try:
            ok = await _append_via_service_account(
                title=title,
                company=company,
                url=link,
                ts=ts,
                persona_id=persona_id,
                location=location or "",
            )
            if ok:
                results["sheet"] = True
                results["service_account"] = True
        except Exception as exc:  # noqa: BLE001
            results["errors"].append(f"service_account:{exc}")

    if not results["sheet"] and not settings.applied_sheets_webhook_url and not settings.google_service_account_json:
        results["errors"].append(
            "no_sheet_credentials — set APPLIED_SHEETS_WEBHOOK_URL or GOOGLE_SERVICE_ACCOUNT_JSON"
        )

    return results


async def _append_via_service_account(
    *,
    title: str,
    company: str,
    url: str,
    ts: str,
    persona_id: str,
    location: str = "",
) -> bool:
    """Append a row with gspread + service account JSON."""
    settings = get_settings()
    path = Path(settings.google_service_account_json)
    if not path.is_absolute():
        path = settings.root_dir / path
    if not path.exists():
        raise FileNotFoundError(f"service account JSON not found: {path}")

    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError as exc:
        raise RuntimeError(
            "Install gspread and google-auth: pip install gspread google-auth"
        ) from exc

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(str(path), scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(settings.applied_sheet_id)
    ws = None
    tab = (settings.applied_sheet_tab or "").strip()
    if tab:
        try:
            ws = sh.worksheet(tab)
        except Exception:  # noqa: BLE001
            ws = None
    if ws is None and settings.applied_sheet_gid:
        for sheet in sh.worksheets():
            if str(sheet.id) == str(settings.applied_sheet_gid):
                ws = sheet
                break
    if ws is None:
        ws = sh.sheet1

    # Ensure header row
    try:
        header = ws.row_values(1)
    except Exception:  # noqa: BLE001
        header = []
    if not header or not any(header):
        ws.update(
            "A1:F1",
            [["Title", "Company", "Job Link", "Applied At", "Persona", "Location"]],
        )
    elif not any("title" in (c or "").lower() for c in header):
        # Sheet already has other content — still append our columns at end of used range
        pass

    ws.append_row(
        [title, company, url, ts, persona_id, location],
        value_input_option="USER_ENTERED",
    )
    return True


def applied_sheet_setup_hint() -> str:
    settings = get_settings()
    return (
        f"Sheet {settings.applied_sheet_id} tab={settings.applied_sheet_tab or settings.applied_sheet_gid}. "
        "Deploy scripts/applied_sheet_apps_script.js as a Web App and set APPLIED_SHEETS_WEBHOOK_URL, "
        "or share the sheet with a service account and set GOOGLE_SERVICE_ACCOUNT_JSON."
    )
