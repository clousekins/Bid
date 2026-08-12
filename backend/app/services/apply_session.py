from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.db.enums import ApplySessionStatus
from app.services.browser_attach import BrowserAttach


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ApplySession:
    job_id: int
    status: ApplySessionStatus = ApplySessionStatus.STARTING
    logs: list[dict[str, Any]] = field(default_factory=list)
    attach: BrowserAttach | None = None
    task: asyncio.Task | None = None
    resume_event: asyncio.Event = field(default_factory=asyncio.Event)
    confirm_event: asyncio.Event = field(default_factory=asyncio.Event)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    ui_opened_tab: bool = True
    apply_url: str = ""
    error: str | None = None
    created_at: datetime = field(default_factory=_utcnow)

    def log(self, message: str, level: str = "info") -> None:
        self.logs.append(
            {"ts": _utcnow().isoformat(), "level": level, "message": message}
        )
        if len(self.logs) > 500:
            self.logs = self.logs[-500:]

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status.value,
            "logs": self.logs[-100:],
            "error": self.error,
            "ui_opened_tab": self.ui_opened_tab,
            "apply_url": self.apply_url,
        }


class ApplySessionManager:
    """Sole owner of in-memory apply sessions."""

    def __init__(self) -> None:
        self._sessions: dict[int, ApplySession] = {}
        self._lock = asyncio.Lock()

    async def clear_all(self) -> None:
        async with self._lock:
            ids = list(self._sessions.keys())
        for jid in ids:
            await self.end_session(jid, status=ApplySessionStatus.CANCELLED)

    async def start(
        self,
        job_id: int,
        *,
        apply_url: str,
        ui_opened_tab: bool = True,
        runner=None,
    ) -> ApplySession:
        await self.clear_all()
        session = ApplySession(
            job_id=job_id,
            apply_url=apply_url,
            ui_opened_tab=ui_opened_tab,
        )
        session.log("Apply session starting")
        async with self._lock:
            self._sessions[job_id] = session
        if runner:
            session.task = asyncio.create_task(runner(session), name=f"apply-{job_id}")
        return session

    def get(self, job_id: int) -> ApplySession | None:
        return self._sessions.get(job_id)

    async def resume(self, job_id: int) -> bool:
        s = self._sessions.get(job_id)
        if not s:
            return False
        s.log("Human resume signaled")
        s.resume_event.set()
        if s.status == ApplySessionStatus.AWAITING_HUMAN:
            s.status = ApplySessionStatus.FILLING
        return True

    async def confirm(self, job_id: int) -> bool:
        s = self._sessions.get(job_id)
        if not s:
            return False
        s.log("Confirm submit signaled")
        s.confirm_event.set()
        return True

    async def cancel(self, job_id: int) -> bool:
        s = self._sessions.get(job_id)
        if not s:
            return False
        s.log("Cancel signaled")
        s.cancel_event.set()
        await self.end_session(job_id, status=ApplySessionStatus.CANCELLED)
        return True

    async def end_session(
        self,
        job_id: int,
        *,
        status: ApplySessionStatus | None = None,
    ) -> None:
        async with self._lock:
            s = self._sessions.pop(job_id, None)
        if not s:
            return
        if status:
            s.status = status
        s.log(f"Ending apply session ({s.status.value})")
        s.cancel_event.set()
        if s.task and not s.task.done():
            s.task.cancel()
            try:
                await s.task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if s.attach:
            await s.attach.disconnect()
            s.attach = None


# Global singleton
apply_sessions = ApplySessionManager()
