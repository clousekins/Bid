from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

import httpx

from app.config import get_settings

log = logging.getLogger("poong.octo")

LogFn = Callable[[str, str], None]


class OctoError(RuntimeError):
    pass


async def _api(method: str, path: str, json: dict | None = None) -> dict | list:
    settings = get_settings()
    url = settings.octo_api_url.rstrip("/") + path
    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.request(method, url, json=json)
        log.info("Octo %s %s -> %s", method, path, resp.status_code)
        if resp.status_code >= 400:
            raise OctoError(f"Octo API {resp.status_code}: {resp.text[:500]}")
        if not resp.content:
            return {}
        try:
            return resp.json()
        except Exception:  # noqa: BLE001
            return {"raw": resp.text[:500]}


def extract_ws_endpoint(start_payload: dict) -> str | None:
    if not start_payload:
        return None
    for key in ("ws_endpoint", "wsEndpoint", "ws_url", "debugger_address", "debug_port", "cdp"):
        val = start_payload.get(key)
        if val is None or val == "" or val is False:
            continue
        if key in {"debugger_address", "debug_port", "cdp"} and not str(val).startswith(("ws", "http")):
            host = str(val)
            if host.isdigit():
                host = f"127.0.0.1:{host}"
            return f"http://{host}"
        return str(val)
    for nest in ("data", "result", "profile", "browser"):
        data = start_payload.get(nest)
        if isinstance(data, dict):
            found = extract_ws_endpoint(data)
            if found:
                return found
    for nest in ("data", "result"):
        data = start_payload.get(nest)
        if isinstance(data, list) and data and isinstance(data[0], dict):
            found = extract_ws_endpoint(data[0])
            if found:
                return found
    return None


def _as_list(data: dict | list | None) -> list[dict]:
    if data is None:
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        if isinstance(data.get("data"), list):
            return [x for x in data["data"] if isinstance(x, dict)]
        return [data]
    return []


async def list_active_profiles(emit: LogFn | None = None) -> list[dict]:
    def _emit(msg: str, level: str = "info") -> None:
        log.info("%s", msg)
        if emit:
            emit(msg, level)

    try:
        data = await _api("GET", "/api/profiles/active")
        items = _as_list(data)
        _emit(f"Octo active profiles: {len(items)}")
        return items
    except Exception as exc:  # noqa: BLE001
        _emit(f"Octo probe /api/profiles/active failed: {exc}", "info")
        return []


async def active_cdp(uuid: str | None = None, emit: LogFn | None = None) -> str | None:
    """Return CDP endpoint if profile is already running WITH debug_port."""
    settings = get_settings()
    profile = uuid or settings.octo_profile_uuid
    if not profile:
        return None

    def _emit(msg: str, level: str = "info") -> None:
        log.info("%s", msg)
        if emit:
            emit(msg, level)

    for item in await list_active_profiles(emit=emit):
        uid = str(item.get("uuid") or item.get("profile_uuid") or item.get("id") or "")
        if uid and uid != str(profile):
            continue
        endpoint = extract_ws_endpoint(item)
        if endpoint:
            _emit(f"Octo already has CDP: {endpoint}")
            return endpoint
        if uid == str(profile):
            _emit(
                "Octo profile is STARTED but debug_port/ws_endpoint is null "
                "(opened from UI without CDP) — need restart with debug_port=true",
                "info",
            )
            return None

    # Last resort: some Octo builds expose CDP only via a no-op start probe
    try:
        data = await _api(
            "POST",
            "/api/profiles/start",
            {"uuid": profile, "headless": False, "debug_port": True},
        )
        endpoint = extract_ws_endpoint(data if isinstance(data, dict) else {})
        if endpoint:
            _emit(f"Octo CDP from start probe: {endpoint}")
            return endpoint
    except Exception as exc:  # noqa: BLE001
        _emit(f"Octo probe /api/profiles/start failed: {exc}", "info")
    return None


async def _stop_profile(profile: str, emit: LogFn | None = None) -> None:
    def _emit(msg: str, level: str = "info") -> None:
        log.info("%s", msg)
        if emit:
            emit(msg, level)

    for path, payload in (
        ("/api/profiles/stop", {"uuid": profile}),
        ("/api/profiles/force_stop", {"uuid": profile}),
    ):
        try:
            await _api("POST", path, payload)
            _emit(f"Octo {path} OK for {profile[:8]}…")
            await asyncio.sleep(1.2)
            return
        except Exception as exc:  # noqa: BLE001
            _emit(f"Octo {path} (ignored): {exc}")


async def start_profile_with_debug(
    uuid: str | None = None,
    *,
    force_restart: bool = False,
    emit: LogFn | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    profile = uuid or settings.octo_profile_uuid
    if not profile:
        raise OctoError("OCTO_PROFILE_UUID is not set")

    def _emit(msg: str, level: str = "info") -> None:
        log.info("%s", msg)
        if emit:
            emit(msg, level)

    if not force_restart:
        existing = await active_cdp(profile, emit=emit)
        if existing:
            return {"ws_endpoint": existing, "reused": True}

        # Running without CDP — must stop + restart with debug_port
        active = await list_active_profiles(emit=emit)
        running_no_cdp = any(
            str(p.get("uuid") or "") == str(profile) and not extract_ws_endpoint(p) for p in active
        )
        if running_no_cdp:
            _emit(
                "Restarting Octo profile with debug_port=true so Playwright can attach",
                "info",
            )
            force_restart = True

    if force_restart:
        _emit("Stopping Octo profile before CDP start…")
        await _stop_profile(profile, emit=emit)

    payload: dict = {"uuid": profile, "headless": False, "debug_port": True}
    if settings.octo_profile_password:
        payload["password"] = settings.octo_profile_password

    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            data = await _api("POST", "/api/profiles/start", payload)
            if not isinstance(data, dict):
                data = {"data": data}
            _emit(
                f"Octo start response keys: {list(data.keys()) if isinstance(data, dict) else type(data)}"
            )
            endpoint = extract_ws_endpoint(data)
            if endpoint:
                data = {**data, "ws_endpoint": endpoint}
                _emit(f"Octo CDP endpoint: {endpoint}")
                return data
            # Start succeeded but no endpoint in body — re-read /active
            await asyncio.sleep(0.8)
            existing = await active_cdp(profile, emit=emit)
            if existing:
                return {"ws_endpoint": existing, "reused": True, "start": data}
            last_err = OctoError(f"Start OK but no ws_endpoint in response: {str(data)[:300]}")
        except OctoError as exc:
            last_err = exc
            msg = str(exc).lower()
            _emit(f"Octo start attempt {attempt} failed: {exc}", "error")
            if "already_started" in msg or "already started" in msg:
                _emit("Profile still running — force stop then retry")
                await _stop_profile(profile, emit=emit)
                await asyncio.sleep(1.0)
                continue
            break

    existing = await active_cdp(profile, emit=emit)
    if existing:
        return {"ws_endpoint": existing, "reused": True}

    raise OctoError(
        "Could not obtain Octo CDP endpoint. "
        "POONG must start the profile with debug_port=true "
        f"(last error: {last_err})"
    )


async def stop_profile(uuid: str | None = None) -> None:
    settings = get_settings()
    profile = uuid or settings.octo_profile_uuid
    if not profile:
        return
    await _stop_profile(profile)


async def browser_diag() -> dict:
    settings = get_settings()
    diag: dict = {
        "octo_enabled": settings.octo_enabled,
        "octo_api_url": settings.octo_api_url,
        "octo_profile_uuid": settings.octo_profile_uuid,
        "browser_cdp_url": settings.browser_cdp_url or None,
        "apply_allow_launch_chromium": settings.apply_allow_launch_chromium,
        "octo_reachable": False,
        "active_profiles": [],
        "active_cdp": None,
        "error": None,
    }
    if not settings.octo_enabled:
        return diag
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(settings.octo_api_url.rstrip("/") + "/api/profiles/active")
            diag["octo_reachable"] = resp.status_code < 500
            diag["octo_status_code"] = resp.status_code
        active = await list_active_profiles()
        diag["active_profiles"] = [
            {
                "uuid": p.get("uuid"),
                "state": p.get("state"),
                "debug_port": p.get("debug_port"),
                "ws_endpoint": p.get("ws_endpoint"),
                "headless": p.get("headless"),
            }
            for p in active
        ]
        diag["active_cdp"] = await active_cdp()
    except Exception as exc:  # noqa: BLE001
        diag["error"] = str(exc)
    return diag
